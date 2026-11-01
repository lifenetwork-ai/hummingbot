"""Expected admission blocks must not masquerade as transport failures."""
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core import order_submission_guard as guard
from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.event.events import MarketOrderFailureEvent
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.executors import TrackedOrder


def owner_with_row(**changes):
    owner = BotOwnership(MagicMock(), 'test')
    row = dict(backend_pid=31, desired_state='running', control_version=1,
               config_file_path='mm.yml', recovery_state={})
    row.update(changes)
    owner.connection = MagicMock()
    owner.connection.closed = owner.connection.invalidated = False
    owner.connection.execute.return_value.mappings.return_value.first.return_value = row
    owner.backend_pid, owner.version, owner.config_file_path = 31, 1, 'mm.yml'
    owner.allow_new_orders = True
    return owner


@pytest.mark.parametrize('changes,expected', [
    ({'desired_state': 'stopped'}, 'SessionSubmissionBlocked'),
    ({'control_version': 2}, 'SessionSubmissionBlocked'),
    ({'desired_state': 'stopped', 'recovery_state': {'halt': True}}, 'SubmissionBlocked'),
    ({'desired_state': 'stopped', 'recovery_state': {'pending_mutations': [{}]}}, 'SubmissionBlocked'),
    ({'config_file_path': 'other.yml'}, 'SubmissionBlocked'),
])
def test_owner_block_classification_preserves_recovery_priority(changes, expected):
    with pytest.raises(guard.SubmissionBlocked) as error:
        owner_with_row(**changes).check_owner(new_risk=True)
    assert type(error.value).__name__ == expected


def test_shutdown_remains_a_block_but_is_expected_control():
    owner = owner_with_row()
    owner.begin_shutdown()
    with pytest.raises(guard.SubmissionBlocked) as error:
        owner._check_shutdown()
    assert type(error.value).__name__ == 'SessionSubmissionBlocked'


@pytest.mark.parametrize('error_type,level', [
    ('SessionSubmissionBlocked', logging.INFO),
    ('MarketReadinessBlocked', logging.WARNING),
    ('SubmissionBlocked', logging.ERROR),
    ('OwnershipLost', logging.ERROR),
])
def test_connector_block_logs_reason_without_network_advice(caplog, error_type, level):
    cls = OwnershipLost if error_type == 'OwnershipLost' else getattr(guard, error_type)
    exception = cls('test admission reason')
    logger = logging.getLogger('test.submission.connector')
    host = SimpleNamespace(name_cap='Okx', logger=lambda: logger, _update_order_after_failure=MagicMock())
    with caplog.at_level(logging.INFO):
        ExchangePyBase._on_order_failure(host, 'order-1', 'BTC-USDT', Decimal('.1'), TradeType.BUY,
                                         OrderType.LIMIT_MAKER, Decimal('100'), exception)
    records = [r for r in caplog.records if r.name == logger.name]
    assert len(records) == 1 and records[0].levelno == level
    assert records[0].event == 'order_submission_blocked'
    assert records[0].reason_code
    assert 'test admission reason' in records[0].getMessage()
    assert 'Check API key' not in caplog.text
    host._update_order_after_failure.assert_called_once_with(order_id='order-1', trading_pair='BTC-USDT', exception=exception)


@pytest.mark.parametrize('slot,role,retry_increment', [
    ('_open_order', 'Open', 1), ('_close_order', 'Close', 1), ('_take_profit_limit_order', 'Take profit', 0),
])
@pytest.mark.parametrize('kind,level', [
    ('SessionSubmissionBlocked', logging.INFO), ('MarketReadinessBlocked', logging.WARNING),
    ('SubmissionBlocked', logging.ERROR), ('OwnershipLost', logging.ERROR),
    ('IOError', logging.ERROR), (None, logging.ERROR),
])
def test_executor_classifies_logs_without_changing_failure_bookkeeping(caplog, slot, role, retry_increment, kind, level):
    executor = PositionExecutor.__new__(PositionExecutor)
    executor._open_order = executor._close_order = executor._take_profit_limit_order = None
    tracked = TrackedOrder('order-1')
    setattr(executor, slot, tracked)
    executor._failed_orders, executor._current_retries, executor._max_retries = [], 2, 10
    event = MarketOrderFailureEvent(1, 'order-1', OrderType.LIMIT_MAKER, 'test admission reason', kind)
    logger = logging.getLogger('test.submission.executor')
    with patch.object(PositionExecutor, 'logger', return_value=logger), caplog.at_level(logging.INFO):
        executor.process_order_failed_event(None, None, event)
    records = [r for r in caplog.records if r.name == logger.name]
    assert len(records) == 1 and records[0].levelno == level
    assert role in records[0].getMessage()
    if kind in ('SessionSubmissionBlocked', 'MarketReadinessBlocked', 'SubmissionBlocked', 'OwnershipLost'):
        assert records[0].event == 'order_submission_blocked'
        assert 'test admission reason' in records[0].getMessage()
        assert 'Retrying' not in records[0].getMessage()
    else:
        assert 'failed order-1. Retrying 2/10' in records[0].getMessage()
    assert getattr(executor, slot) is None
    assert executor._failed_orders == [tracked]
    assert executor._current_retries == 2 + retry_increment


def test_real_connector_failure_retains_network_diagnostic():
    logger = MagicMock()
    host = SimpleNamespace(name_cap='Okx', logger=lambda: logger, _update_order_after_failure=MagicMock())
    error = OSError('transport failed')
    ExchangePyBase._on_order_failure(host, 'order-1', 'BTC-USDT', Decimal('.1'), TradeType.BUY,
                                     OrderType.LIMIT_MAKER, Decimal('100'), error)
    logger.network.assert_called_once()
    host._update_order_after_failure.assert_called_once_with(order_id='order-1', trading_pair='BTC-USDT', exception=error)


@pytest.mark.parametrize('scope,permits,ready,expected', [
    (None, True, True, 'SubmissionBlocked'),
    (('old-session', 0), False, True, 'SessionSubmissionBlocked'),
    (('session', 0), True, False, 'MarketReadinessBlocked'),
    (('session', 0), True, None, 'SubmissionBlocked'),
])
def test_mm_authority_distinguishes_waiting_from_missing_wiring(scope, permits, ready, expected):
    owner = owner_with_row()
    owner.mm_ready_check = None if ready is None else lambda: ready
    with patch('hummingbot.core.mm_session.session_permits', return_value=permits):
        with pytest.raises(guard.SubmissionBlocked) as error:
            owner._check_mm_authority({'config_type': 'mm-session', 'mm_session': {}, 'database_now': 1}, scope)
    assert type(error.value).__name__ == expected


@pytest.mark.parametrize('cls', [guard.SessionSubmissionBlocked, guard.MarketReadinessBlocked, guard.SubmissionBlocked, OwnershipLost])
def test_failure_event_preserves_block_type_reason_and_failed_state(cls):
    from hummingbot.connector.client_order_tracker import ClientOrderTracker
    from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
    connector = MagicMock()
    connector.current_timestamp = 12
    connector._order_tracker = MagicMock()
    error = cls('Explicit safe reason')
    ExchangePyBase._update_order_after_failure(connector, 'order-1', 'BTC-USDT', error)
    update = connector._order_tracker.process_order_update.call_args.args[0]
    assert update.new_state == OrderState.FAILED
    order = InFlightOrder(client_order_id='order-1', trading_pair='BTC-USDT', order_type=OrderType.LIMIT_MAKER,
                          trade_type=TradeType.BUY, amount=Decimal('.1'), price=Decimal('100'), creation_timestamp=1)
    ClientOrderTracker(connector)._trigger_failure_event(order, update)
    event = connector.trigger_event.call_args.args[1]
    assert event.error_type == cls.__name__ and event.error_message == str(error)
    assert event.order_id == 'order-1'
