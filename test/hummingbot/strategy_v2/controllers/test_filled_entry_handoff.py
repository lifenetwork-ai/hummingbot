"""Completed MM entries hand off held exposure before the scheduled quote refresh."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import (
    controller_actions,
    fill,
    observe_account,
    setup_lifecycle,
)

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType


def account_observed(state, side, *, at=1002, initial=(1000, 0)):
    name = state.controller.config.connector_name
    long, short = map(Decimal, initial)
    if name == 'okx':
        long += state.order.amount * (1 if side == TradeType.BUY else -1)
    elif side == TradeType.BUY:
        long += state.order.amount
    else:
        short += state.order.amount
    observe_account(state.connector, name, (long, short))
    state.connector.account_balance_observations = {
        asset: dict(cash_balance=amount, observed_at=at)
        for asset, amount in [('LIFE', long), ('USDT', Decimal('10000'))]}
    state.connector.position_observations = {key: dict(observed_at=at)
                                             for key in state.connector.account_positions}


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_full_entry_handoff_does_not_wait_sixty_seconds_or_lose_fees(monkeypatch, connector_name, side):
    state = await setup_lifecycle(monkeypatch, connector_name, side, initial=(2000, 0), executor_refresh_time=60)
    controller, executor, order = state.controller, state.executor, state.order
    assert fill(order, 'full-fill', str(order.amount))
    assert not fill(order, 'full-fill', str(order.amount))
    order.current_state = OrderState.FILLED
    account_observed(state, side, initial=(2000, 0))
    controller.market_data_provider.time.return_value = 1003
    state.strategy.current_timestamp = 1003
    assert controller.config.executor_refresh_time == 60
    actions = await controller_actions(controller, executor)
    assert len(actions) == 1 and isinstance(actions[0], StopExecutorAction)
    assert actions[0].executor_id == executor.config.id and actions[0].keep_position
    repeated = await controller_actions(controller, executor)
    assert not any(isinstance(a, StopExecutorAction) and a.executor_id == executor.config.id for a in repeated)
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == side for a in repeated)
    state.orchestrator.execute_actions(actions)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == side
                   for a in await controller_actions(controller, executor))
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED and executor.close_type == CloseType.POSITION_HOLD
    state.orchestrator._update_positions_from_done_executors()
    state.orchestrator._update_positions_from_done_executors()
    holds = state.orchestrator.positions_held[controller.config.id]
    assert len(holds) == 1
    assert holds[0].net_amount_base == order.amount * (1 if side == TradeType.BUY else -1)
    assert holds[0].cum_fees_quote == Decimal('.000001')
    actions = await controller_actions(controller, executor)
    assert any(isinstance(a, CreateExecutorAction) and a.executor_config.side == side for a in actions)
    state.strategy.cancel.assert_not_called()
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('case', [
    'unfilled', 'partial', 'terminal_without_fills', 'nearly_full', 'no_exchange_id',
    'account_missing', 'account_before_fill', 'account_future', 'account_stale',
    'account_nan', 'account_bool_time', 'invalid_fill_time', 'bool_fill_time',
])
async def test_incomplete_fill_or_unconfirmed_account_keeps_the_old_level(monkeypatch, connector_name, case):
    state = await setup_lifecycle(monkeypatch, connector_name, TradeType.BUY, executor_refresh_time=60)
    controller, executor, order = state.controller, state.executor, state.order
    if case not in ('unfilled', 'terminal_without_fills'):
        amount = order.amount if case not in ('partial', 'nearly_full') else (
            order.amount / 2 if case == 'partial' else order.amount - Decimal('.000000000000000001'))
        assert fill(order, 'fill', str(amount))
    order.current_state = OrderState.FILLED if case != 'unfilled' else OrderState.OPEN
    observed_at = {'account_before_fill': 1000, 'account_future': 1005, 'account_stale': 980}.get(case, 1002)
    account_observed(state, TradeType.BUY, at=observed_at)
    if case == 'no_exchange_id':
        order.exchange_order_id = None
    if case == 'account_missing':
        state.connector.account_balance_observations = {}
        state.connector.position_observations = {}
    if case in ('account_nan', 'account_bool_time'):
        stamp = float('nan') if case == 'account_nan' else True
        for observation in state.connector.account_balance_observations.values():
            observation['observed_at'] = stamp
        for observation in state.connector.position_observations.values():
            observation['observed_at'] = stamp
    if case in ('invalid_fill_time', 'bool_fill_time'):
        order.order_fills['fill'] = order.order_fills['fill']._replace(
            fill_timestamp=float('nan') if case == 'invalid_fill_time' else True)
    controller.market_data_provider.time.return_value = 1003
    actions = await controller_actions(controller, executor)
    assert not any(isinstance(a, StopExecutorAction) and a.executor_id == executor.config.id for a in actions)
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)
    assert executor.status == RunnableStatus.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('stop_loss', Decimal('.01')), ('take_profit', Decimal('.01')), ('time_limit', 60)])
async def test_explicit_exit_barriers_keep_the_original_executor_lifecycle(monkeypatch, field, value):
    state = await setup_lifecycle(monkeypatch, 'okx_perpetual', TradeType.BUY,
                                  executor_refresh_time=60)
    # Preserve an independently configured/restored PositionExecutor's exits;
    # the normal LifeMM catalogue itself forbids exit barriers.
    state.executor.config = state.executor.config.model_copy(update={
        'triple_barrier_config': state.executor.config.triple_barrier_config.model_copy(update={field: value})})
    assert fill(state.order, 'full', str(state.order.amount))
    state.order.current_state = OrderState.FILLED
    account_observed(state, TradeType.BUY)
    state.controller.market_data_provider.time.return_value = 1003
    assert state.executor.get_custom_info()['entry_order_filled'] is True
    assert not any(isinstance(a, StopExecutorAction) for a in await controller_actions(state.controller, state.executor))


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
async def test_handoff_waits_for_account_update_and_new_entry_still_obeys_caps(monkeypatch, connector_name):
    state = await setup_lifecycle(monkeypatch, connector_name, TradeType.BUY,
                                  executor_refresh_time=60, max_base_inventory='1700', max_long_base='1700')
    assert fill(state.order, 'full', str(state.order.amount))
    state.order.current_state = OrderState.FILLED
    state.controller.market_data_provider.time.return_value = 1003
    # The account has not yet reflected the fill: reserve the whole old entry.
    assert not any(isinstance(a, StopExecutorAction) for a in await controller_actions(state.controller, state.executor))
    assert state.controller._exposure()[0] == Decimal('1600')
    account_observed(state, TradeType.BUY)
    actions = await controller_actions(state.controller, state.executor)
    assert len(actions) == 1 and actions[0].keep_position
    state.orchestrator.execute_actions(actions)
    await state.executor.control_task()
    actions = await controller_actions(state.controller, state.executor)
    assert state.controller._exposure()[0] == Decimal('1600')  # Full account exposure, entry no longer reserved twice.
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)


@pytest.mark.asyncio
async def test_session_revocation_during_handoff_cannot_restart_quotes(monkeypatch):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.SELL,
                                  executor_refresh_time=60, runtime_config_managed=True,
                                  mm_session_id='session', mm_generation=3)
    assert fill(state.order, 'full', str(state.order.amount))
    state.order.current_state = OrderState.FILLED
    account_observed(state, TradeType.SELL)
    state.controller.market_data_provider.time.return_value = 1003
    actions = await controller_actions(state.controller, state.executor)
    state.orchestrator.execute_actions(actions)
    state.controller.revoke_session()
    await state.executor.control_task()
    assert state.executor.status == RunnableStatus.TERMINATED
    assert await controller_actions(state.controller, state.executor) == []


@pytest.mark.asyncio
async def test_both_spot_sell_levels_filled_resume_before_refresh_and_merge_each_fill_once(monkeypatch):
    from hummingbot.core.data_type.in_flight_order import InFlightOrder
    from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
    from hummingbot.strategy_v2.models.executors import TrackedOrder

    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.SELL, initial=(6000, 0),
                                  executor_refresh_time=60, total_amount_quote='400',
                                  sell_spreads=[.01, .02], sell_amounts_pct=[1, 1])
    controller = state.controller
    config = controller.get_executor_config('sell_1', Decimal('.102'), Decimal('600'))
    second = PositionExecutor(state.strategy, config)
    order = InFlightOrder('second', 'LIFE-USDT', state.order.order_type, TradeType.SELL,
                          Decimal('600'), 1000, Decimal('.102'), exchange_order_id='second-exchange', initial_state=OrderState.OPEN)
    second._open_order = TrackedOrder(order.client_order_id)
    second._open_order.order = order
    second._status = RunnableStatus.RUNNING
    executors = [state.executor, second]
    state.orchestrator.active_executors[controller.config.id] = executors
    for actor in executors:
        entry = actor._open_order.order
        assert fill(entry, entry.client_order_id, str(entry.amount))
        entry.current_state = OrderState.FILLED
    observe_account(state.connector, 'okx', (4800, 0))
    state.connector.account_balance_observations = {
        asset: dict(cash_balance=Decimal(amount), observed_at=1002)
        for asset, amount in [('LIFE', '4800'), ('USDT', '10000')]}
    controller.market_data_provider.time.return_value = state.strategy.current_timestamp = 1003
    controller.executors_info = [actor.executor_info for actor in executors]
    actions = controller.determine_executor_actions()
    assert len(actions) == 2 and all(isinstance(a, StopExecutorAction) and a.keep_position for a in actions)
    state.orchestrator.execute_actions(actions)
    controller.executors_info = [actor.executor_info for actor in executors]
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.SELL
                   for a in controller.determine_executor_actions())
    for actor in executors:
        await actor.control_task()
    state.orchestrator._update_positions_from_done_executors()
    state.orchestrator._update_positions_from_done_executors()
    holds = state.orchestrator.positions_held[controller.config.id]
    assert len(holds) == 1 and holds[0].net_amount_base == Decimal('-1200')
    assert holds[0].cum_fees_quote == Decimal('.000002')
    controller.executors_info = [actor.executor_info for actor in executors]
    actions = controller.determine_executor_actions()
    assert len([a for a in actions if isinstance(a, CreateExecutorAction)
                and a.executor_config.side == TradeType.SELL]) == 2
    assert state.strategy.current_timestamp == 1003  # Three seconds since creation, not sixty.
