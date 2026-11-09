"""Native acknowledged quotes remain present during bounded rolling refresh."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import setup_lifecycle
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration
from unittest.mock import patch

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import TrackedOrder


async def resting_pair(monkeypatch, connector_name, side):
    state = await setup_lifecycle(monkeypatch, connector_name, side, initial=(2000, 0),
                                  executor_refresh_time=60, quote_refresh_max_delay_seconds=5,
                                  buy_spreads=[.01, .02], sell_spreads=[.01, .02])
    level = 'buy_1' if side == TradeType.BUY else 'sell_1'
    config = state.controller.get_executor_config(level, state.order.price, Decimal('600'))
    peer = PositionExecutor(state.strategy, config)
    order = InFlightOrder('peer', config.trading_pair, state.order.order_type, side, config.amount,
                          1000, config.entry_price, exchange_order_id='exchange-peer', initial_state=OrderState.OPEN)
    peer._open_order = TrackedOrder(order.client_order_id)
    peer._open_order.order = order
    peer._status = RunnableStatus.RUNNING
    state.connector.in_flight_orders = {state.order.client_order_id: state.order, order.client_order_id: order}
    state.orchestrator.active_executors[state.controller.config.id].append(peer)
    state.peer, state.peer_order = peer, order
    state.controller.market_data_provider.time.return_value = 1061
    state.strategy.current_timestamp = 1061
    state.controller.executors_info = [state.executor.executor_info, peer.executor_info]
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_ordinary_refresh_does_not_cancel_both_acknowledged_levels(monkeypatch, connector_name, side):
    state = await resting_pair(monkeypatch, connector_name, side)
    actions = state.controller.executors_to_refresh()
    assert len(actions) == 1
    assert isinstance(actions[0], StopExecutorAction) and actions[0].keep_position
    assert actions[0].executor_id == state.executor.config.id
    assert state.peer_order.current_state == OrderState.OPEN


@pytest.mark.asyncio
async def test_buy_and_sell_refresh_independently_and_deadline_can_withdraw_all(monkeypatch):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    for index in range(2):
        config = state.controller.get_executor_config(f'sell_{index}', Decimal('.101'), Decimal('600'))
        config = config.model_copy(update={'timestamp': 1000})
        executor = PositionExecutor(state.strategy, config)
        order = InFlightOrder(f'sell-{index}', config.trading_pair, state.order.order_type, TradeType.SELL,
                              config.amount, 1000, config.entry_price, exchange_order_id=f'exchange-sell-{index}',
                              initial_state=OrderState.OPEN)
        executor._open_order = TrackedOrder(order.client_order_id)
        executor._open_order.order = order
        executor._status = RunnableStatus.RUNNING
        state.connector.in_flight_orders[order.client_order_id] = order
        state.controller.executors_info.append(executor.executor_info)
    actions = state.controller.executors_to_refresh()
    assert len(actions) == 2
    configs = {executor.id: executor.config for executor in state.controller.executors_info}
    assert {configs[action.executor_id].side for action in actions} == {TradeType.BUY, TradeType.SELL}
    state.controller.market_data_provider.time.return_value = 1065
    assert len(state.controller.executors_to_refresh()) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', ['cancelling', 'replacement_unacknowledged', 'missing_peer'])
async def test_pending_refresh_keeps_last_working_quote_only_until_original_deadline(monkeypatch, pending):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    if pending == 'cancelling':
        state.executor._status = RunnableStatus.SHUTTING_DOWN
        state.order.current_state = OrderState.PENDING_CANCEL
    elif pending == 'replacement_unacknowledged':
        state.executor.config = state.executor.config.model_copy(update={'timestamp': 1061})
        state.order.exchange_order_id = None
        state.order.current_state = OrderState.PENDING_CREATE
    else:
        state.executor._status = RunnableStatus.TERMINATED
    state.controller.executors_info = [state.executor.executor_info, state.peer.executor_info]
    assert state.controller.executors_to_refresh() == []
    state.controller.market_data_provider.time.return_value = 1065
    actions = state.controller.executors_to_refresh()
    assert any(action.executor_id == state.peer.config.id for action in actions)
    assert all(action.keep_position for action in actions)


@pytest.mark.asyncio
async def test_rolling_refresh_never_delays_a_hard_inventory_withdrawal(monkeypatch):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    state.connector.get_balance.side_effect = lambda asset: Decimal('20000')
    state.controller.executors_info = [state.executor.executor_info, state.peer.executor_info]
    actions = state.controller.determine_executor_actions()
    assert len(actions) == 2 and all(isinstance(action, StopExecutorAction) and action.keep_position for action in actions)
    assert not any(isinstance(action, CreateExecutorAction) for action in actions)


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
async def test_cancel_terminal_then_new_ack_are_required_before_retiring_peer(monkeypatch, connector_name):
    state = await resting_pair(monkeypatch, connector_name, TradeType.BUY)
    controller = state.controller
    first = controller.executors_to_refresh()
    state.orchestrator.execute_actions(first)
    assert state.executor.status == RunnableStatus.SHUTTING_DOWN
    controller.executors_info = [state.executor.executor_info, state.peer.executor_info]
    assert controller.executors_to_refresh() == []
    assert 'buy_0' not in controller.get_levels_to_execute()
    # The terminal exchange observation releases only its own price level.
    state.order.current_state = OrderState.CANCELED
    await state.executor.control_shutdown_process()
    assert state.executor.status == RunnableStatus.TERMINATED
    controller.executors_info = [state.executor.executor_info, state.peer.executor_info]
    actions = controller.determine_executor_actions()
    replacement = next(action for action in actions if isinstance(action, CreateExecutorAction)
                       and action.executor_config.level_id == 'buy_0')
    assert not any(isinstance(action, StopExecutorAction) and action.executor_id == state.peer.config.id for action in actions)
    executor = PositionExecutor(state.strategy, replacement.executor_config)
    config = executor.config
    order = InFlightOrder('replacement', config.trading_pair, state.order.order_type, TradeType.BUY,
                          config.amount, 1061, config.entry_price, initial_state=OrderState.PENDING_CREATE)
    executor._open_order = TrackedOrder(order.client_order_id)
    executor._open_order.order = order
    executor._status = RunnableStatus.RUNNING
    state.connector.in_flight_orders[order.client_order_id] = order
    controller.executors_info = [executor.executor_info, state.peer.executor_info]
    assert controller.executors_to_refresh() == []  # Proposal/sending is not working liquidity.
    order.exchange_order_id = 'exchange-replacement'
    order.current_state = OrderState.OPEN
    controller.executors_info = [executor.executor_info, state.peer.executor_info]
    actions = controller.executors_to_refresh()
    assert len(actions) == 1 and actions[0].executor_id == state.peer.config.id
    assert controller._acknowledged_refresh_entry(executor.executor_info) is order
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_spread_is_withdrawn_without_waiting_for_a_peer(monkeypatch):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    with patch.object(state.controller, 'spread_quote_valid', return_value=False):
        actions = state.controller.executors_to_refresh()
    assert len(actions) == 2 and all(action.keep_position for action in actions)


@pytest.mark.asyncio
async def test_an_invalid_peer_cannot_be_the_only_quote_left_working(monkeypatch):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    state.peer.config = state.peer.config.model_copy(update={'entry_price': Decimal('.098')})
    state.peer_order.price = Decimal('.098')
    state.controller.executors_info = [state.executor.executor_info, state.peer.executor_info]
    with patch.object(state.controller, 'spread_quote_valid', side_effect=lambda side, price, amount, **kw: price == Decimal('.099')):
        actions = state.controller.executors_to_refresh()
    assert len(actions) == 1 and actions[0].executor_id == state.peer.config.id


@pytest.mark.asyncio
@pytest.mark.parametrize('single_level', [False, True])
async def test_default_batch_and_single_level_do_not_wait(monkeypatch, single_level):
    state = await resting_pair(monkeypatch, 'okx', TradeType.BUY)
    controller = state.controller
    if single_level:
        controller.config = controller.config.model_copy(update={'buy_spreads': [.01], 'buy_amounts_pct': [Decimal('1')]})
        controller.executors_info = [state.executor.executor_info]
    else:
        controller.config = controller.config.model_copy(update={'quote_refresh_max_delay_seconds': 0})
    assert len(controller.executors_to_refresh()) == (1 if single_level else 2)


@pytest.mark.parametrize('value', [-1, 11, True, 1.5, '5'])
def test_refresh_delay_requires_an_explicit_bounded_integer(value):
    with pytest.raises(ValueError):
        configuration(quote_refresh_max_delay_seconds=value)


def test_opt_in_refresh_roundtrips_without_changing_default_catalogue_shape():
    assert 'quote_refresh_max_delay_seconds' not in configuration().model_dump()
    config = configuration(quote_refresh_max_delay_seconds=5)
    assert config.model_validate_json(config.model_dump_json()).quote_refresh_max_delay_seconds == 5
    with pytest.raises(ValueError, match='Refresh delay'):
        configuration(executor_refresh_time=2, quote_refresh_max_delay_seconds=5)
