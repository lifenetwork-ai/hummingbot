"""Only an independently verified close may bypass opening risk/collateral."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_reduction_controller import maker
from types import SimpleNamespace

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.utils.perpetual_reduction_runtime import ExecutorQuoteState, ReductionRuntime


def closing_host():
    controller, connector, state = maker('1', '0', max_long_base='.1', max_short_base='.1', max_gross_base='.2')
    controller.update_reference = lambda **kwargs: None
    config = controller.get_executor_config('sell_0', Decimal('101'), Decimal('.1'))
    order = InFlightOrder(client_order_id='close', trading_pair='BTC-USDT', order_type=OrderType.LIMIT_MAKER,
                          trade_type=TradeType.SELL, amount=config.amount, price=config.price,
                          creation_timestamp=1000, position=PositionAction.CLOSE)
    state[0] = ReductionRuntime(state[0].long, state[0].short, (
        ExecutorQuoteState(config, RunnableStatus.RUNNING, False, ('close',), (order,)),), True)
    host = object.__new__(ServiceHost)
    host.owner = SimpleNamespace(volatility_checkpoints=None)
    host.mm_session = SimpleNamespace(id='session', generation=0, catalogue=SimpleNamespace(drawdown_policy=lambda: None))
    host._mm_controllers = lambda: [controller]
    return host, controller, connector, state, config, order


def test_verified_closes_work_above_cap_without_opening_admission_becoming_true():
    host, controller, connector, state, config, order = closing_host()
    assert not controller.exposure_within_caps()
    assert host.mm_runtime_ready()  # The market can reduce, including a reserved working close.
    assert not host.mm_runtime_ready(opening=True)
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        assert host.mm_runtime_order_ready(connector, order)
        host.mm_quote_valid(connector, order)
    claimed = config.model_dump(mode='json')
    claimed['id'] = 'not-a-resident-executor'
    with session_order_scope('session', 0, level_id=config.level_id, executor=claimed):
        assert not host.mm_runtime_order_ready(connector, order)
        with pytest.raises(SubmissionBlocked):
            host.mm_quote_valid(connector, order)


def test_guard_and_stale_fee_still_block_a_valid_closing_direction():
    host, controller, connector, _, config, order = closing_host()
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        controller.processed_data['ready'] = False
        assert not host.mm_runtime_order_ready(connector, order)
        controller.processed_data['ready'] = True
        controller._reduction_clock = lambda: 81
        assert not host.mm_runtime_order_ready(connector, order)


def test_closing_configuration_is_not_counted_as_opening_margin():
    host, controller, _, _, _, _ = closing_host()
    config = next(iter(controller._reduction_runtime().executors)).config
    controller.executors_info = [SimpleNamespace(is_active=True, is_trading=False, config=config)]
    host._mm_available_quote = lambda controllers: Decimal('0')
    assert host.mm_runtime_ready()


def test_zero_free_funds_does_not_hide_a_positive_closing_fee_requirement():
    host, _, connector, _, config, order = closing_host()
    connector.get_available_balance.side_effect = lambda asset: Decimal('0')
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        assert not host.mm_runtime_order_ready(connector, order)


def test_policy_disabled_does_not_accept_a_claimed_close():
    host, controller, connector, _, config, order = closing_host()
    controller.config = controller.config.model_copy(update={
        'inventory_reduction': controller.config.inventory_reduction.model_copy(update={'mode': 'disabled', 'price_mode': None})})
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        with pytest.raises(SubmissionBlocked):
            host.mm_quote_valid(connector, order)


def test_opening_margin_cannot_spend_the_fees_reserved_by_an_existing_close():
    host, controller, _, state, _, _ = closing_host()
    controller.config = controller.config.model_copy(update={
        'max_long_base': Decimal(2), 'max_short_base': Decimal(2), 'max_gross_base': Decimal(4)})
    opened = controller._get_open_executor_config('buy_0', Decimal(99), Decimal('.1')).model_copy(update={'controller_id': 'maker'})
    rows = state[0].executors + (ExecutorQuoteState(opened, RunnableStatus.RUNNING, False),)
    state[0] = ReductionRuntime(state[0].long, state[0].short, rows, True)
    host._mm_available_quote = lambda controllers: opened.amount * opened.entry_price
    assert not host.mm_runtime_ready(opening=True)
    assert host.mm_runtime_ready()  # Fee-paying reductions still have independent admission.


def test_stale_position_evidence_is_reported_separately_from_a_real_exposure_breach():
    host, controller, connector, _, _, _ = closing_host()
    controller.market_data_provider.time.return_value = 1011
    connector.position_observations['long']['observed_at'] = 1011
    assert not host.mm_runtime_ready()
    assert host.mm_readiness_reason == 'reduction_position_unavailable'


def test_rejected_native_quantity_retains_its_controlled_reason_for_the_send_gate_log():
    host, _, connector, _, config, order = closing_host()
    connector.account_positions['long'].amount = Decimal('.01')
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        assert not host.mm_runtime_order_ready(connector, order)
    assert host.owner.mm_order_readiness_reason == 'reduction_quantity_unavailable'
