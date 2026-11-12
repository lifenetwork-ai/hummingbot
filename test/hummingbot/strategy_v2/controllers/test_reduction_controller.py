"""Close-first quotes use owned checkpoints, fresh venue state and shared capacity."""
import asyncio
from decimal import ROUND_DOWN, Decimal
from test.hummingbot.client.config.test_mm_reduction_configuration import GUARDS, POLICY
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.okx_reduction_fee import FeeObservation
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.utils.perpetual_reduction import ClosingFeeBound, HedgeCostBasis
from hummingbot.strategy_v2.utils.perpetual_reduction_runtime import ExecutorQuoteState, ReductionRuntime

D = Decimal


def maker(long='0', short='0', *, levels=1, **updates):
    values = dict(id='maker', connector_name='okx_perpetual', trading_pair='BTC-USDT', initial_anchor='100',
                  min_price='1', max_price='1000', total_amount_quote='100',
                  max_long_base='2', max_short_base='2', max_gross_base='4',
                  buy_spreads=[.01] * levels, sell_spreads=[.01] * levels,
                  buy_amounts_pct=[1] * levels, sell_amounts_pct=[1] * levels,
                  inventory_reduction=POLICY, quote_guards=GUARDS, spread_policy={'fee_floor': True},
                  runtime_config_managed=True, mm_session_id='session', mm_generation=0)
    values.update(updates)
    controller, _ = make_controller(configuration(**values))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.name, connector.position_mode = 'okx_perpetual', PositionMode.HEDGE
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    connector._poll_notifier = asyncio.Event()
    connector._position_refresh_notifier = asyncio.Event()
    connector.request_position_refresh.side_effect = lambda: OkxPerpetualDerivative.request_position_refresh(connector)
    connector.position_snapshot_times = {'BTC-USDT': 1000}
    connector.position_observations = {}
    connector.account_positions = {}
    for key, side, amount in (('long', PositionSide.LONG, long), ('short', PositionSide.SHORT, short)):
        if D(amount):
            connector.account_positions[key] = SimpleNamespace(trading_pair='BTC-USDT', position_side=side,
                                                               amount=D(amount) * (1 if side == PositionSide.LONG else -1))
            connector.position_observations[key] = dict(observed_at=1000, margin_mode='cross')
    rule = TradingRule('BTC-USDT', min_order_size=D('.001'), min_base_amount_increment=D('.001'),
                       min_price_increment=D('.01'), min_notional_size=D('.01'),
                       buy_order_collateral_token='USDT', sell_order_collateral_token='USDT')
    connector.trading_rules = {'BTC-USDT': rule}
    connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(D('.01'), rounding=ROUND_DOWN)
    connector.quantize_order_amount.side_effect = lambda pair, amount: amount.quantize(D('.001'), rounding=ROUND_DOWN)
    connector.get_order_price_quantum.return_value = D('.01')
    connector.get_fee.return_value = AddedToCostTradeFee(percent=D('.001'))
    state = [ReductionRuntime(
        HedgeCostBasis(TradeType.BUY, D(long), D(long) * 100, D(long) * D('.1')),
        HedgeCostBasis(TradeType.SELL, D(short), D(short) * 100, D(short) * D('.1')), (), True)]
    controller.bind_reduction_runtime(lambda: state[0])
    controller._reduction_clock = lambda: 50
    controller._reduction_fee_observation = FeeObservation(ClosingFeeBound(D('.001'), D(0)), 50)
    controller._account_ready = True
    controller.admit_session('session', 0)
    controller.processed_data = dict(ready=True, reference_price=D('100'), spread_multiplier=D('1'))
    controller._effective_spreads = ([D('.01')] * levels, [D('.01')] * levels)
    # These tests isolate reduction admission; the existing guard tests retain
    # the complete external-book/benchmark implementation.
    controller.quote_price_valid = lambda price, **kw: D('95') <= price <= D('105')
    controller.spread_quote_valid = lambda side, price, amount, **kw: (
        price <= D('99') if side == TradeType.BUY else price >= D('101'))
    return controller, connector, state


def effect(config):
    return getattr(config, 'position_action', PositionAction.OPEN)


@pytest.mark.parametrize('long,short,effects', [
    ('0', '0', [PositionAction.OPEN, PositionAction.OPEN]),
    ('1', '0', [PositionAction.OPEN, PositionAction.CLOSE]),
    ('0', '1', [PositionAction.CLOSE, PositionAction.OPEN]),
    ('1', '1', [PositionAction.CLOSE, PositionAction.CLOSE]),
])
def test_nonflat_effect_matrix_uses_native_maker_order_executors(long, short, effects):
    controller, _, _ = maker(long, short)
    actions = controller.create_actions_proposal()
    assert [effect(action.executor_config) for action in actions] == effects
    for action in actions:
        config = action.executor_config
        if effect(config) == PositionAction.CLOSE:
            assert isinstance(config, OrderExecutorConfig)
            assert config.execution_strategy == ExecutionStrategy.LIMIT_MAKER
            assert (config.mm_session_id, config.mm_generation, config.controller_id) == ('session', 0, 'maker')


def test_two_levels_cannot_close_the_same_quantity_or_grow_exposure_at_caps():
    controller, _, _ = maker('.1', '.1', levels=2, max_long_base='.1', max_short_base='.1', max_gross_base='.2')
    actions = controller.create_actions_proposal()
    assert len(actions) == 2
    assert all(effect(action.executor_config) == PositionAction.CLOSE for action in actions)
    for side in (TradeType.BUY, TradeType.SELL):
        assert sum((a.executor_config.amount for a in actions if a.executor_config.side == side), D(0)) == D('.1')
    assert controller.create_actions_proposal() == []  # Queue handoff has not happened yet.


@pytest.mark.parametrize('defect,reason', [
    ('missing', 'reduction_basis_unavailable'), ('stale_position', 'reduction_position_unavailable'),
    ('stale_fee', 'reduction_fee_unavailable'), ('foreign', 'reduction_ownership_unavailable'),
    ('unknown_basis', 'reduction_basis_unavailable'), ('wrong_margin', 'reduction_position_unavailable'),
])
def test_unproven_opposing_inventory_does_not_fall_back_to_open(defect, reason):
    controller, connector, state = maker('1', '1')
    if defect == 'missing':
        controller._reduction_runtime_source = None
    elif defect == 'stale_position':
        connector.position_observations['long']['observed_at'] = 900
    elif defect == 'stale_fee':
        controller._reduction_clock = lambda: 81
    elif defect == 'wrong_margin':
        connector.position_observations['long']['margin_mode'] = 'isolated'
    elif defect == 'unknown_basis':
        state[0] = ReductionRuntime(None, None, (), True)
    elif defect == 'foreign':
        state[0] = ReductionRuntime(HedgeCostBasis(TradeType.BUY, D(0), D(0), D(0)),
                                    HedgeCostBasis(TradeType.SELL, D(0), D(0), D(0)), (), True)
    assert controller.create_actions_proposal() == []
    reasons = controller.quote_diagnostics['reasons']
    assert any(reason in reasons[side] for side in ('BUY', 'SELL'))


def test_fee_inclusive_boundary_is_clipped_outwards_and_rejected_outside_guard():
    controller, _, state = maker('1', '0')
    state[0] = ReductionRuntime(HedgeCostBasis(TradeType.BUY, D('1'), D('103'), D('.1')), state[0].short, (), True)
    configs = [a.executor_config for a in controller.create_actions_proposal()]
    closing = next(c for c in configs if c.side == TradeType.SELL)
    assert closing.price == D('103.21')
    assert state[0].long.covers(closing.price, closing.amount, ClosingFeeBound(D('.001'), D(0)))
    controller._reduction_queued.clear()
    state[0] = ReductionRuntime(HedgeCostBasis(TradeType.BUY, D('1'), D('110'), D('.1')), state[0].short, (), True)
    assert all(a.executor_config.side == TradeType.BUY for a in controller.create_actions_proposal())
    assert 'reduction_price_not_profitable' in controller.quote_diagnostics['reasons']['SELL']


def test_pending_cancel_and_unapplied_terminal_close_reserve_capacity():
    controller, _, state = maker('.1', '0')
    close = OrderExecutorConfig(id='closing', timestamp=1000, controller_id='maker', connector_name='okx_perpetual',
                                trading_pair='BTC-USDT', side=TradeType.SELL, amount=D('.1'), price=D('101'),
                                position_action=PositionAction.CLOSE, execution_strategy=ExecutionStrategy.LIMIT_MAKER,
                                level_id='sell_0', mm_session_id='session', mm_generation=0)
    for status in (RunnableStatus.SHUTTING_DOWN, RunnableStatus.TERMINATED):
        state[0] = ReductionRuntime(state[0].long, state[0].short, (ExecutorQuoteState(close, status, False),), True)
        actions = controller.create_actions_proposal()
        assert all(a.executor_config.side != TradeType.SELL for a in actions)
        controller._reduction_queued.clear()


def test_zero_opening_collateral_does_not_charge_margin_to_a_reduction():
    controller, connector, _ = maker('1', '1')
    connector.get_available_balance.side_effect = lambda asset: D(0)
    controller._reduction_fee_observation = FeeObservation(ClosingFeeBound(D(0), D(0)), 50)
    actions = controller.create_actions_proposal()
    assert len(actions) == 2 and all(effect(a.executor_config) == PositionAction.CLOSE for a in actions)


def test_native_close_is_not_added_to_opening_exposure_and_pending_open_is_not_released():
    controller, _, state = maker('1', '1')
    closing = controller.get_executor_config('sell_0', D('101'), D('.1'))
    assert effect(closing) == PositionAction.CLOSE
    state[0] = ReductionRuntime(state[0].long, state[0].short,
                                (ExecutorQuoteState(closing, RunnableStatus.RUNNING, False),), True)
    assert controller._exposure() == (D('1'), D('1'))


def test_identity_is_revalidated_against_venue_shrink_and_price_before_transport():
    controller, connector, state = maker('1', '0')
    config = controller.get_executor_config('sell_0', D('101'), D('.1'))
    assert effect(config) == PositionAction.CLOSE
    order = InFlightOrder(client_order_id='close', exchange_order_id=None, trading_pair='BTC-USDT',
                          trade_type=TradeType.SELL, order_type=OrderType.LIMIT_MAKER, amount=config.amount,
                          price=config.price, creation_timestamp=1000, position=PositionAction.CLOSE,
                          initial_state=OrderState.PENDING_CREATE)
    state[0] = ReductionRuntime(state[0].long, state[0].short, (
        ExecutorQuoteState(config, RunnableStatus.RUNNING, False, ('close',), (order,)),), True)
    assert controller.reduction_order_valid(order, config.model_dump(mode='json'))
    connector.account_positions['long'].amount = D('.05')
    assert not controller.reduction_order_valid(order, config.model_dump(mode='json'))
    connector.account_positions['long'].amount = D('1')
    order.price = D('99')
    assert not controller.reduction_order_valid(order, config.model_dump(mode='json'))


@pytest.mark.asyncio
async def test_delayed_executor_report_does_not_cancel_a_valid_close_above_opening_caps():
    controller, _, state = maker('1', '0', max_long_base='.1', max_short_base='.1', max_gross_base='.2')
    close = controller.get_executor_config('sell_0', D('101'), D('.1'))
    state[0] = ReductionRuntime(state[0].long, state[0].short, (
        ExecutorQuoteState(close, RunnableStatus.RUNNING, False),), True)
    controller.executors_update_event.clear()
    controller.update_processed_data = AsyncMock()
    controller.send_actions = AsyncMock()
    await controller.control_task()
    assert all(not call.args[0] for call in controller.send_actions.await_args_list)
    controller._reduction_clock = lambda: 81
    await controller.control_task()
    assert controller.send_actions.await_args.args[0][0].executor_id == close.id


def test_pre_identity_refresh_only_shrinks_and_cannot_flip_close_into_open():
    controller, connector, state = maker('1', '0')
    controller.update_reference = lambda: None
    controller._fresh_price = lambda *args: D(100)
    original = controller.get_executor_config('sell_0', D('101'), D('.1'))
    connector.account_positions['long'].amount = D('.05')
    prepared = controller.prepare_reduction_quote(original)
    assert prepared is not None and prepared.id == original.id
    assert prepared.position_action == PositionAction.CLOSE
    assert prepared.amount == D('.05') and prepared.amount * prepared.price <= original.amount * original.price
    state[0] = ReductionRuntime(HedgeCostBasis(TradeType.BUY, D(0), D(0), D(0)), state[0].short, (), True)
    connector.account_positions.clear()
    assert controller.prepare_reduction_quote(original) is None


@pytest.mark.parametrize('remaining', ['.001', '.002'])
def test_small_close_rounds_price_before_spending_the_level_budget(remaining):
    controller, _, _ = maker(remaining, '0')
    # A non-tick-aligned model price must not donate one amount lot when the
    # actual level allocation is much larger than the remaining owned position.
    price, requested = D('101.00000000000009'), D('.1')
    close = controller.get_executor_config('sell_0', price, requested)
    assert close is not None
    assert close.position_action == PositionAction.CLOSE
    assert close.amount == D(remaining)
    assert close.price == D('101.01')
    assert close.amount * close.price <= price * requested
    controller.update_reference = lambda: None
    controller._fresh_price = lambda *args: D('100')
    controller.get_price_and_amount = lambda level: (price, requested)
    controller.market_data_provider.get_price_by_type.return_value = D('101')
    prepared = controller.prepare_reduction_quote(close)
    assert prepared is not None
    assert (prepared.id, prepared.amount, prepared.price) == (close.id, close.amount, close.price)


def test_small_close_cannot_round_up_past_its_actual_allocation():
    controller, _, _ = maker('.001', '0')
    assert controller.get_executor_config('sell_0', D('101.00000000000009'), D('.001')) is None


@pytest.mark.parametrize('long,short,level,model,maker_price', [
    ('.001', '0', 'sell_0', '101', '101.03'),
    ('0', '.001', 'buy_0', '99', '98.97'),
])
def test_small_close_budgets_the_passive_book_bound_before_identity(long, short, level, model, maker_price):
    controller, _, _ = maker(long, short)
    controller.market_data_provider.get_price_by_type.side_effect = lambda *args: D(maker_price)
    close = controller.get_executor_config(level, D(model), D('.1'))
    assert close is not None and close.price == D(maker_price)
    assert close.amount == D('.001')
    controller.update_reference = lambda: None
    controller._fresh_price = lambda *args: D('100')
    controller.get_price_and_amount = lambda level: (D(model), D('.1'))
    prepared = controller.prepare_reduction_quote(close)
    assert prepared is not None
    assert (prepared.id, prepared.price, prepared.amount) == (close.id, close.price, close.amount)


def test_reduction_status_is_cached_and_reports_capacity_separately_from_opening_limits():
    from hummingbot.cli.admin_models import InventoryReductionStatus
    controller, _, _ = maker('.1', '.1', max_long_base='.1', max_short_base='.1', max_gross_base='.2')
    actions = controller.create_actions_proposal()
    assert len(actions) == 2
    controller.bind_reduction_runtime(lambda: (_ for _ in ()).throw(AssertionError('status must not read runtime')))
    status = controller.reduction_status
    assert status['mode'] == 'close_first' and status['price_mode'] == 'breakeven'
    assert not status['stale'] and status['fee_ready']
    assert status['sides']['SELL']['effect'] == 'CLOSE'
    assert status['sides']['SELL']['target_leg'] == 'LONG'
    assert D(status['sides']['SELL']['reserved_base']) == D('.1')
    assert D(status['sides']['SELL']['available_base']) == 0
    assert InventoryReductionStatus.model_validate(status).sides['SELL'].effect == 'CLOSE'
    status['sides'].clear()
    assert controller.reduction_status['sides']
    controller.market_data_provider.time.return_value = 1006
    assert controller.reduction_status['stale']


@pytest.mark.asyncio
async def test_fee_worker_failure_clears_authority_and_stop_joins_the_worker(monkeypatch):
    controller, _, _ = maker('1', '0')
    reached = asyncio.Event()

    async def fail(*args):
        reached.set()
        raise OSError('simulated fee service outage')

    monkeypatch.setattr('hummingbot.strategy_v2.utils.perpetual_reduction_runtime.read_reduction_fee', fail)
    task = controller._reduction_fee_task = asyncio.create_task(controller.poll_reduction_fees())
    await asyncio.wait_for(reached.wait(), timeout=1)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert controller._reduction_fee_observation is None
    await controller.on_stop()
    assert task.done() and task.cancelled()
    assert controller._reduction_fee_task is None


def test_healthy_ws_does_not_leave_an_absent_hedge_leg_waiting_for_the_120_second_rest_cycle():
    controller, connector, _ = maker('1', '0')
    connector._poll_notifier = asyncio.Event()
    controller.market_data_provider.time.return_value = 1011
    connector.position_observations['long']['observed_at'] = 1011
    # A fresh LONG WS update says nothing about the absent SHORT leg. The full
    # snapshot has expired even though the connector's user stream is healthy.
    with pytest.raises(ValueError, match='reduction_position_unavailable'):
        controller._reduction_venue()
    assert controller.request_reduction_position_refresh()
    assert connector._poll_notifier.is_set()
    assert connector.position_snapshot_times['BTC-USDT'] == 1000
    assert not controller.request_reduction_position_refresh()  # coalesce existing work
    with pytest.raises(ValueError):
        controller._reduction_venue()
    # Only a successful native full snapshot makes the absent leg known again.
    connector.position_snapshot_times['BTC-USDT'] = 1011
    connector._poll_notifier.clear()
    assert not controller.request_reduction_position_refresh()
    assert controller._reduction_venue() == (D(1), D(0))


@pytest.mark.asyncio
async def test_position_refresh_worker_is_joined_on_stop_without_calling_rest_itself():
    controller, connector, _ = maker('1', '0')
    connector._poll_notifier = asyncio.Event()
    controller.market_data_provider.time.return_value = 1006
    task = controller._reduction_position_task = asyncio.create_task(controller.poll_reduction_positions())
    await asyncio.wait_for(connector._poll_notifier.wait(), timeout=1)
    connector._api_get.assert_not_called()
    connector._update_positions.assert_not_called()
    await controller.on_stop()
    assert task.done() and task.cancelled()
    assert controller._reduction_position_task is None


def test_large_level_catalogue_cannot_enqueue_more_than_the_resident_authority_bound(monkeypatch):
    import random

    monkeypatch.setattr(random, 'randint', lambda *_: 7)
    controller, _, _ = maker('0', '0', levels=60)
    actions = controller.create_actions_proposal()
    assert len(actions) == len(controller._reduction_queued) == 100
    assert controller.create_actions_proposal() == []
    assert 'reduction_reservation_limit' in controller.quote_diagnostics['reasons']['BUY']


def test_stale_active_dto_cannot_keep_a_proved_released_native_level_occupied():
    controller, _, state = maker('1', '0')
    config = controller.get_executor_config('sell_0', D('101'), D('.1'))
    state[0] = ReductionRuntime(state[0].long, state[0].short, (
        ExecutorQuoteState(config, RunnableStatus.TERMINATED, True),), True)
    controller.executors_info = [SimpleNamespace(id=config.id, is_active=True, config=config,
                                                 status=RunnableStatus.RUNNING, close_type=None, custom_info={'level_id': 'sell_0'})]
    assert 'sell_0' in controller.get_levels_to_execute()


def test_disabled_policy_preserves_the_existing_open_only_nonflat_proposals():
    controller, _, _ = maker('1', '1')
    disabled = controller.config.inventory_reduction.model_copy(update={'mode': 'disabled', 'price_mode': None})
    controller.config = controller.config.model_copy(update={'inventory_reduction': disabled})
    actions = controller.create_actions_proposal()
    assert len(actions) == 2
    assert all(action.executor_config.type == 'position_executor' for action in actions)
    assert all(effect(action.executor_config) == PositionAction.OPEN for action in actions)
