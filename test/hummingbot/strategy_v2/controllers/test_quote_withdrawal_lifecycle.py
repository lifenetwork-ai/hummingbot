"""Protective withdrawal and data recovery cannot undo explicit session revocation."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import (
    controller_actions,
    fill,
    observe_account,
    setup_lifecycle,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import OrderCancelledEvent
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType

CASES = [
    pytest.param(connector, side, reason, '100', id=f'{connector}-{side.name.lower()}-{reason}')
    for connector in ('okx', 'okx_perpetual')
    for side in (TradeType.BUY, TradeType.SELL)
    for reason in ('stale_book', 'provider_unready', 'invalid_balance', 'reference_out_of_bounds', 'session_revoked',
                   'account_unconfirmed')
    if reason != 'account_unconfirmed' or connector == 'okx_perpetual'
] + [
    pytest.param(connector, side, 'stale_book', '0', id=f'{connector}-{side.name.lower()}-unfilled')
    for connector in ('okx', 'okx_perpetual')
    for side in (TradeType.BUY, TradeType.SELL)
]


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name,side,reason,initial_fill', CASES)
async def test_withdrawal_then_data_recovery_does_not_reopen_a_revoked_session(
        monkeypatch, connector_name, side, reason, initial_fill):
    state = await setup_lifecycle(
        monkeypatch, connector_name, side, (1000, 200 if connector_name == 'okx_perpetual' else 0),
        runtime_config_managed=True, mm_session_id='session', mm_generation=3,
        scenario='follow_token', references=[dict(pair='BTC-USDT', weight='1')])
    controller, executor, order = state.controller, state.executor, state.order
    provider = controller.market_data_provider
    assert not any(isinstance(action, StopExecutorAction)
                   for action in await controller_actions(controller, executor))
    balances = [Decimal('1000'), Decimal('200') if connector_name == 'okx_perpetual' else Decimal('0')]
    if Decimal(initial_fill):
        assert fill(order, 'partial', initial_fill)
        order.current_state = OrderState.PARTIALLY_FILLED
        if connector_name == 'okx':
            balances[0] += Decimal(initial_fill) * (1 if side == TradeType.BUY else -1)
        else:
            balances[0 if side == TradeType.BUY else 1] += Decimal(initial_fill)
    observe_account(state.connector, connector_name, balances)

    original_book = provider.get_order_book.side_effect, provider.get_order_book.return_value
    if reason == 'stale_book':
        provider.get_order_book.side_effect = None
        provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=0, last_diff_uid=0)
    elif reason == 'provider_unready':
        provider.ready = False
    elif reason == 'invalid_balance':
        observe_account(state.connector, connector_name, (Decimal('NaN'), 0))
    elif reason == 'reference_out_of_bounds':
        state.prices['BTC-USDT'] = Decimal('10000')
    elif reason == 'account_unconfirmed':
        controller._account_ready = False
    else:
        controller.revoke_session()
    # A protective withdrawal must not wait for the normal proposal/report gate.
    actions = await controller_actions(controller, executor, report_ready=False)
    assert len(actions) == 1 and isinstance(actions[0], StopExecutorAction)
    assert actions[0].executor_id == executor.config.id and actions[0].keep_position
    assert await controller_actions(controller, executor, report_ready=False) == []
    state.orchestrator.execute_actions(actions)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == CloseType.POSITION_HOLD

    entered, deadline = asyncio.Event(), asyncio.Event()

    async def delayed_ack(seconds):
        assert seconds == 5
        entered.set()
        await deadline.wait()

    executor._sleep = AsyncMock(side_effect=delayed_ack)
    shutdown = asyncio.create_task(executor.control_shutdown_process())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not shutdown.done()
        state.strategy.cancel.assert_called_once_with(
            connector_name=connector_name, trading_pair=order.trading_pair, order_id=order.client_order_id)
        # All environmental causes recover while the actual cancellation is
        # still pending. Stop/session revocation remains independently binding.
        provider.get_order_book.side_effect, provider.get_order_book.return_value = original_book
        provider.ready = True
        state.prices['BTC-USDT'] = Decimal('100')
        controller._account_ready = True
        observe_account(state.connector, connector_name, balances)
        controller.revoke_session()
        assert await controller_actions(controller, executor) == []
        assert controller.processed_data['ready'] and not controller.session_admitted
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        assert state.orchestrator.positions_held[controller.config.id] == []

        late = Decimal('.000000000000000019') if Decimal(initial_fill) else Decimal('0')
        if late:
            assert fill(order, 'late', late)
            assert not fill(order, 'late', late)
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(
            0, state.connector, OrderCancelledEvent(1002, order.client_order_id))
        await asyncio.wait_for(shutdown, timeout=1)
        await executor.control_shutdown_process()
    finally:
        deadline.set()
        if not shutdown.done():
            shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)

    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == (CloseType.POSITION_HOLD if Decimal(initial_fill) else CloseType.EARLY_STOP)
    state.orchestrator._update_positions_from_done_executors()
    state.orchestrator._update_positions_from_done_executors()
    holds = state.orchestrator.positions_held[controller.config.id]
    if Decimal(initial_fill):
        assert len(holds) == 1
        assert holds[0].net_amount_base == (Decimal(initial_fill) + late) * (1 if side == TradeType.BUY else -1)
        assert holds[0].cum_fees_quote == Decimal('.000002')
        if connector_name == 'okx':
            balances[0] += late * (1 if side == TradeType.BUY else -1)
        else:
            balances[0 if side == TradeType.BUY else 1] += late
    else:
        assert holds == [] and executor._held_position_orders == []
    observe_account(state.connector, connector_name, balances)
    assert await controller_actions(controller, executor) == []
    with pytest.raises(ValueError, match='identity'):
        controller.admit_session('session', 2)
    assert not controller.session_admitted
    controller.admit_session('session', 3)  # Explicit valid host admission, not automatic data recovery.
    actions = await controller_actions(controller, executor)
    assert actions and all(isinstance(action, CreateExecutorAction) for action in actions)
    assert controller._exposure() == tuple(balances)
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()
