"""A queued cash BUY cannot spin on an unchanged owner outflow cap."""
import logging
from decimal import Decimal
from test.hummingbot.core.test_spot_outflow import fill
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import (
    controller_actions,
    fill as native_fill,
    setup_lifecycle,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import MarketOrderFailureEvent, OrderCancelledEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.spot_outflow import SpotOutflowProjection
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType, TrackedOrder


@pytest.mark.asyncio
@pytest.mark.parametrize('configured', [False, True])
async def test_outflow_denial_retires_one_unsent_entry_and_controller_waits_for_capacity(monkeypatch, caplog, configured):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY,
                                  mm_session_id='session', mm_generation=1)
    controller, executor, order = state.controller, state.executor, state.order
    controller.config = controller.config.model_copy(update={'runtime_config_managed': True})
    executor.config = executor.config.model_copy(update={'use_configured_entry_price': configured})
    order.exchange_order_id, order.current_state = None, OrderState.FAILED
    state.connector.name = 'okx'
    state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    owner = SimpleNamespace(require_recovery=MagicMock())
    guard = SpotOutflowGuard(owner, dict(trading_pair='LIFE-USDT', window_seconds=10,
                                         max_net_outflow_quote='100', fee_allowance_rate='.01'))
    guard.identity = lambda: {'account_id': 'same'}
    guard.binding, guard.reason_code = guard.identity(), None
    guard.projection = SpotOutflowProjection(guard.config, 'LIFE-USDT', coverage_start_ms=0)
    guard.projection.advance(1000000)
    # The quote was prepared earlier; another committed fill now exhausts its cap.
    guard.projection.add_fill(fill('other:fill', 999999, amount='10'))
    monkeypatch.setattr('hummingbot.core.order_submission_guard.spot_buy_preview', guard.preview)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SubmissionBlocked, match='spot_outflow_limit') as denied:
        guard.before_order(state.connector, order, {'database_now': 1000})
    executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1000, order.client_order_id, OrderType.LIMIT_MAKER, str(denied.value), type(denied.value).__name__))
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor._current_retries == 0  # A local cap denial is not an exchange failure budget.
    executor.place_open_order = MagicMock()
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED
    executor.place_open_order.assert_not_called()
    assert executor._failed_orders and not order.executed_amount_base
    for _ in range(12):
        actions = await controller_actions(controller, executor)
        assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)
        assert any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.SELL for a in actions)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()
    owner.require_recovery.assert_not_called()
    assert guard.projection.net == Decimal('100') and guard.config.max_net_outflow_quote == Decimal('100')
    # Real window expiry, not an altered cap or fabricated terminal proof.
    guard.projection.advance(1010000)
    actions = await controller_actions(controller, executor)
    assert any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['SubmissionBlocked', 'OwnershipLost', 'MarketReadinessBlocked', 'IOError'])
async def test_matching_message_without_the_typed_outflow_denial_keeps_existing_failure_semantics(monkeypatch, kind):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY)
    state.executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1000, state.order.client_order_id, OrderType.LIMIT_MAKER, 'spot_outflow_limit', kind))
    assert state.executor.status == RunnableStatus.RUNNING
    assert state.executor._current_retries == 1 and state.executor._failed_orders


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['unmanaged', 'perpetual', 'sell', 'nonmaker'])
async def test_typed_outflow_wait_is_limited_to_managed_spot_maker_buys(monkeypatch, change):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY, mm_session_id='session', mm_generation=1)
    config = state.executor.config
    updates = {'mm_session_id': None} if change == 'unmanaged' else (
        {'connector_name': 'okx_perpetual'} if change == 'perpetual' else (
            {'side': TradeType.SELL} if change == 'sell' else {
                'triple_barrier_config': config.triple_barrier_config.model_copy(update={'open_order_type': OrderType.LIMIT})}))
    state.executor.config = config.model_copy(update=updates)
    state.executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1000, state.order.client_order_id, OrderType.LIMIT_MAKER, 'spot_outflow_limit', 'SpotOutflowBlocked'))
    assert state.executor.status == RunnableStatus.RUNNING and state.executor._current_retries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('stopping', [False, True])
async def test_outflow_denial_preserves_prior_fills_pending_orders_and_existing_stop(monkeypatch, stopping):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY, mm_session_id='session', mm_generation=1)
    executor, order = state.executor, state.order
    assert native_fill(order, 'prior-fill', '100')
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    executor._open_order = TrackedOrder('queued-retry')
    pending = InFlightOrder('pending-tp', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                            Decimal('100'), 1001, Decimal('.102'), exchange_order_id='venue-tp',
                            initial_state=OrderState.OPEN)
    executor._take_profit_limit_order = TrackedOrder(pending.client_order_id)
    executor._take_profit_limit_order.order = pending
    executor._current_retries = executor._max_retries
    if stopping:
        executor.early_stop(keep_position=False)
    executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1001, 'queued-retry', OrderType.LIMIT_MAKER, 'spot_outflow_limit', 'SpotOutflowBlocked'))
    assert executor._current_retries == executor._max_retries
    executor.evaluate_max_retries()
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == (CloseType.EARLY_STOP if stopping else CloseType.POSITION_HOLD)
    executor._wait_for_shutdown_orders = AsyncMock()
    await executor.control_task()
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    state.strategy.cancel.assert_called_once_with(connector_name='okx', trading_pair='LIFE-USDT', order_id='pending-tp')
    pending.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, state.connector, OrderCancelledEvent(1002, pending.client_order_id))
    if not stopping:
        await executor.control_task()
        for _ in range(2):
            state.orchestrator._update_positions_from_done_executors()
        held, = state.orchestrator.positions_held[state.controller.config.id]
        assert held.net_amount_base == Decimal('100') and held.cum_fees_quote == Decimal('.000001')
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()


@pytest.mark.parametrize('change', ['fee', 'scope', 'projection', 'binding', 'numeric', 'terms'])
def test_noncapacity_guard_failures_keep_the_generic_safety_error(change):
    guard = SpotOutflowGuard(SimpleNamespace(require_recovery=MagicMock()), dict(
        trading_pair='LIFE-USDT', window_seconds=10, max_net_outflow_quote='100', fee_allowance_rate='.01'))
    guard.identity = lambda: {'account_id': 'same'}
    guard.binding, guard.reason_code = guard.identity(), None
    guard.projection = SpotOutflowProjection(guard.config, 'LIFE-USDT', coverage_start_ms=0)
    guard.projection.advance(20000)
    connector = SimpleNamespace(name='okx', get_fee=MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001'))))
    order = InFlightOrder('candidate', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal('5'), 20, Decimal('10'))
    if change == 'fee':
        connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.02'))
    elif change == 'scope':
        order.trading_pair = 'ETH-USDT'
    elif change == 'projection':
        guard.projection.failure = 'spot_outflow_projection_limit'
    elif change == 'binding':
        guard.binding = {'account_id': 'different'}
    elif change == 'numeric':
        order.amount = Decimal('NaN')
    else:
        guard.before_order(connector, order, {'database_now': 20})
        order.amount = Decimal('6')
    with pytest.raises(SubmissionBlocked) as denied:
        guard.before_order(connector, order, {'database_now': 20})
    assert type(denied.value) is SubmissionBlocked
