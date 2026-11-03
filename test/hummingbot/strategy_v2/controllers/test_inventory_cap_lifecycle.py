"""Native controller -> executor cancellation -> held exposure -> admission checks.

Only exchange/account observations, strategy I/O and recorder persistence are
faked. No database, network, background trading loop or real sleep is required.
"""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PositionMode, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import OrderCancelledEvent
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType, TrackedOrder

CASES = [
    pytest.param('okx', TradeType.BUY, dict(max_base_inventory='1700'),
                 (1000, 0), (1200, 0), (1800, 0), id='spot-base-cap'),
    pytest.param('okx_perpetual', TradeType.BUY, dict(max_long_base='1700', max_gross_base='10000'),
                 (1000, 200), (1200, 200), (1800, 200), id='perpetual-long-cap'),
    pytest.param('okx_perpetual', TradeType.SELL, dict(max_short_base='1700', max_gross_base='10000'),
                 (200, 1000), (200, 1200), (200, 1800), id='perpetual-short-cap'),
    pytest.param('okx_perpetual', TradeType.BUY, dict(max_gross_base='1700'),
                 (500, 500), (650, 500), (900, 900), id='perpetual-gross-cap'),
]


def observe_account(connector, connector_name, amounts):
    """Supply authoritative external balances/hedge legs; never net long/short."""
    long, short = map(Decimal, amounts)
    connector.get_balance.side_effect = lambda asset: long if asset == 'LIFE' else Decimal('10000')
    connector.get_available_balance.side_effect = connector.get_balance.side_effect
    if connector_name == 'okx_perpetual':
        connector.account_positions = {
            'long': SimpleNamespace(trading_pair='LIFE-USDT', position_side=PositionSide.LONG, amount=long),
            'short': SimpleNamespace(trading_pair='LIFE-USDT', position_side=PositionSide.SHORT, amount=-short),
        }


def fill(order, trade_id, amount):
    amount = Decimal(amount)
    return order.update_with_trade_update(TradeUpdate(
        trade_id=trade_id, client_order_id=order.client_order_id, exchange_order_id=order.exchange_order_id,
        trading_pair=order.trading_pair, fill_timestamp=1001, fill_price=order.price,
        fill_base_amount=amount, fill_quote_amount=amount * order.price,
        fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.000001'))]),
    ))


async def controller_actions(controller, executor, *, report_ready=True):
    controller.executors_info = [executor.executor_info]
    if report_ready:
        controller.executors_update_event.set()
    else:
        controller.executors_update_event.clear()
    await controller.control_task()
    actions = []
    while not controller.actions_queue.empty():
        actions.extend(controller.actions_queue.get_nowait())
    return actions


async def setup_lifecycle(monkeypatch, connector_name, side, initial=(1000, 0), **updates):
    """Seed one already admitted resting quote; expose real component boundaries."""
    controller, prices = make_controller(configuration(connector_name=connector_name, **updates))
    controller._account_ready = True
    controller.admit_session(controller.config.mm_session_id, controller.config.mm_generation)
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.position_mode = PositionMode.HEDGE
    connector.get_price_by_type.return_value = Decimal('.1')
    connector.quantize_order_amount.side_effect = lambda trading_pair, amount, **kwargs: amount
    connector.quantize_order_price.side_effect = lambda trading_pair, price: price
    connector.trading_rules['LIFE-USDT'] = TradingRule(trading_pair='LIFE-USDT')
    if connector_name == 'okx_perpetual':
        # A perpetual strategy may still follow spot references. Preserve each
        # source's real timestamp units instead of returning SWAP IDs for both.
        controller.market_data_provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
            snapshot_uid=1000000 if name == 'okx_perpetual' else 1000,
            last_diff_uid=1000000 if name == 'okx_perpetual' else 1000)
    observe_account(connector, connector_name, initial)
    await controller.update_processed_data()
    assert controller.processed_data['ready'], controller.processed_data

    recorder = MagicMock()
    recorder.iter_executor_performance.side_effect = lambda *args, **kwargs: (row for row in ())
    recorder.get_all_positions.return_value = []
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: recorder)
    strategy = MagicMock()
    strategy.config = SimpleNamespace(mm_drawdown_scope=None)
    strategy.controllers = {controller.config.id: controller}
    strategy.connectors = {connector_name: connector}
    strategy.current_timestamp = 1000
    level = 'buy_0' if side == TradeType.BUY else 'sell_0'
    price = Decimal('.099') if side == TradeType.BUY else Decimal('.101')
    config = controller.get_executor_config(level, price, Decimal('600'))
    assert config is not None
    executor = PositionExecutor(strategy, config)
    order = InFlightOrder(client_order_id='entry', exchange_order_id='exchange-entry',
                          trading_pair=config.trading_pair, order_type=OrderType.LIMIT_MAKER,
                          trade_type=side, amount=config.amount, price=price,
                          creation_timestamp=1000, initial_state=OrderState.OPEN)
    executor._open_order = TrackedOrder(order.client_order_id)
    executor._open_order.order = order
    executor._status = RunnableStatus.RUNNING
    orchestrator = ExecutorOrchestrator(strategy)
    orchestrator.active_executors[controller.config.id] = [executor]
    return SimpleNamespace(controller=controller, connector=connector, strategy=strategy, order=order,
                           executor=executor, orchestrator=orchestrator, prices=prices)


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name,side,limits,initial,breached,still_breached', CASES)
@pytest.mark.parametrize('late_fill', ['0', '.000000000000000019'], ids=['no-late-fill', 'late-fill-during-cancel'])
async def test_hard_cap_cancels_remainder_preserves_fill_and_recovers_only_with_valid_exposure(
        monkeypatch, connector_name, side, limits, initial, breached, still_breached, late_fill):
    state = await setup_lifecycle(monkeypatch, connector_name, side, initial, **limits)
    controller, connector, strategy = state.controller, state.connector, state.strategy
    executor, order, orchestrator = state.executor, state.order, state.orchestrator
    config = executor.config

    # Healthy resting entry and a real partial fill do not trigger a cap stop.
    assert not any(isinstance(action, StopExecutorAction)
                   for action in await controller_actions(controller, executor))
    assert fill(order, 'first-fill', '100')
    order.current_state = OrderState.PARTIALLY_FILLED
    partial = list(map(Decimal, initial))
    filled_leg = 0 if side == TradeType.BUY else 1
    partial[filled_leg] += Decimal('100')
    observe_account(connector, connector_name, partial)
    assert not any(isinstance(action, StopExecutorAction)
                   for action in await controller_actions(controller, executor))

    # An independent account change breaches the cap including the full entry
    # reservation. The account alone is still below its cap at this stage.
    observe_account(connector, connector_name, breached)
    assert controller._within_caps(*map(Decimal, breached))
    assert not controller.exposure_within_caps()
    actions = await controller_actions(controller, executor)
    assert len(actions) == 1 and isinstance(actions[0], StopExecutorAction)
    assert actions[0].executor_id == executor.config.id and actions[0].keep_position
    assert await controller_actions(controller, executor) == []  # No repeated stop proposal.
    orchestrator.execute_actions(actions)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == CloseType.POSITION_HOLD

    # Hold the real shutdown wait open until an explicit terminal observation.
    # Losing the SHUTTING_DOWN reservation would incorrectly reopen admission.
    entered, deadline = asyncio.Event(), asyncio.Event()

    async def wait_for_deadline(seconds):
        assert seconds == 5
        entered.set()
        await deadline.wait()

    executor._sleep = AsyncMock(side_effect=wait_for_deadline)
    shutdown = asyncio.create_task(executor.control_shutdown_process())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        strategy.cancel.assert_called_once_with(connector_name=connector_name,
                                                trading_pair=config.trading_pair, order_id=order.client_order_id)
        assert not shutdown.done() and executor.status == RunnableStatus.SHUTTING_DOWN
        assert not controller.exposure_within_caps()
        assert await controller_actions(controller, executor) == []
        orchestrator._update_positions_from_done_executors()
        assert orchestrator.positions_held[controller.config.id] == []

        if Decimal(late_fill):
            assert fill(order, 'late-fill', late_fill)
            assert not fill(order, 'late-fill', late_fill)  # Duplicate trade ID is not more exposure.
            assert await controller_actions(controller, executor) == []
        held_amount = Decimal('100') + Decimal(late_fill)
        assert order.executed_amount_base == held_amount
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(
            0, connector, OrderCancelledEvent(timestamp=1002, order_id=order.client_order_id))
        await asyncio.wait_for(shutdown, timeout=1)
        # The wakeup completes the wait; the next executor cycle retires it.
        await executor.control_shutdown_process()
    finally:
        deadline.set()
        if not shutdown.done():
            shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)

    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == CloseType.POSITION_HOLD
    assert len(executor._held_position_orders) == 1
    assert Decimal(executor._held_position_orders[0]['executed_amount_base']) == held_amount
    orchestrator._update_positions_from_done_executors()
    orchestrator._update_positions_from_done_executors()
    holds = orchestrator.positions_held[controller.config.id]
    assert len(holds) == 1
    assert holds[0].net_amount_base == held_amount * (1 if side == TradeType.BUY else -1)
    assert holds[0].side == (side if connector_name == 'okx_perpetual' else None)
    assert holds[0].order_ids == {order.client_order_id}
    assert holds[0].cum_fees_quote == Decimal('.000001') * (2 if Decimal(late_fill) else 1)

    # A terminal cancel is not permission to ignore a still-breached account.
    observe_account(connector, connector_name, still_breached)
    assert not controller._within_caps(*map(Decimal, still_breached))
    assert await controller_actions(controller, executor) == []
    assert not controller.exposure_within_caps()

    # A valid later account observation retains the exact fill, releases only
    # the terminal remainder reservation and permits a replacement proposal.
    partial[filled_leg] += Decimal(late_fill)
    observe_account(connector, connector_name, partial)
    assert controller._exposure() == tuple(partial)
    actions = await controller_actions(controller, executor)
    assert actions and all(isinstance(action, CreateExecutorAction) for action in actions)
    assert any(action.executor_config.side == side for action in actions)
    assert controller.exposure_within_caps()
    assert len(holds) == 1 and abs(holds[0].net_amount_base) == held_amount
    # Native component integration stops at proposals; it does not fake venue ACKs.
    strategy.buy.assert_not_called()
    strategy.sell.assert_not_called()
