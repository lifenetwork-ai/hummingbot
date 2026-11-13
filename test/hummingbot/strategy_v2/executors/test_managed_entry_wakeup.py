"""Managed maker entries must not add an idle tick after native terminal proof."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill, setup_checkpoint
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


def entry(monkeypatch, *, managed=True):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
    executor.config = executor.config.model_copy(update={
        'use_configured_entry_price': True, 'mm_session_id': 'entry-session' if managed else None,
        'mm_generation': 1 if managed else None})
    executor.update_interval = 60
    executor.validate_sufficient_balance = AsyncMock()
    executor.control_open_order = Mock()
    executor.control_barriers = Mock()
    executor.get_in_flight_order = lambda *args: order
    return orchestrator, executor, order, recorder


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('partial', ['0', '.2'])
async def test_managed_entry_stop_wakes_idle_loop_and_retains_exact_held_fills(monkeypatch, side, partial):
    _, executor, order, _ = entry(monkeypatch)
    executor.config.side, order.trade_type = side, side
    if Decimal(partial):
        fill(order, 'entry-partial', partial)
    idle = asyncio.Event()
    wait = executor._wait_for_control_tick

    async def waiting():
        idle.set()
        await wait()

    executor._wait_for_control_tick = waiting
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(idle.wait(), 1)
        # Native terminal evidence is already applied; only idle scheduling remains.
        order.current_state = OrderState.CANCELED
        executor.early_stop(keep_position=True)
        await asyncio.wait_for(executor.terminated.wait(), .25)
        await asyncio.wait_for(asyncio.shield(task), .25)
        assert executor.close_type == (CloseType.POSITION_HOLD if Decimal(partial) else CloseType.EARLY_STOP)
        assert sum((Decimal(row['executed_amount_base']) for row in executor._held_position_orders), Decimal(0)) == Decimal(partial)
        executor._strategy.buy.assert_not_called()
        executor._strategy.sell.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_terminal_callback_does_not_incur_an_outer_tick_after_shutdown_wait(monkeypatch):
    _, executor, order, _ = entry(monkeypatch)
    cancel_sent = asyncio.Event()
    executor._strategy.cancel.side_effect = lambda **kwargs: cancel_sent.set()
    executor.early_stop(keep_position=True)
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(cancel_sent.wait(), 1)
        assert executor.status == RunnableStatus.SHUTTING_DOWN and not order.is_done
        fill(order, 'late-entry-fill', '.2')
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        await asyncio.wait_for(executor.terminated.wait(), .25)
        await asyncio.wait_for(asyncio.shield(task), .25)
        assert executor.close_type == CloseType.POSITION_HOLD
        assert len(executor._held_position_orders) == 1
        assert Decimal(executor._held_position_orders[0]['executed_amount_base']) == Decimal('.2')
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('excluded', ['unmanaged', 'unconfigured', 'taker'])
async def test_other_entries_do_not_change_their_periodic_cadence(monkeypatch, excluded):
    _, executor, order, _ = entry(monkeypatch, managed=excluded != 'unmanaged')
    if excluded == 'unconfigured':
        executor.config.use_configured_entry_price = False
    elif excluded == 'taker':
        executor.config.triple_barrier_config.open_order_type = OrderType.MARKET
    idle = asyncio.Event()
    wait = executor._wait_for_control_tick

    async def waiting():
        idle.set()
        await wait()

    executor._wait_for_control_tick = waiting
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(idle.wait(), 1)
        order.current_state = OrderState.CANCELED
        executor.early_stop(keep_position=True)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(executor.terminated.wait(), .05)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_managed_entry_foreign_cancel_and_duplicate_stop_cannot_release_pending_order(monkeypatch):
    _, executor, order, _ = entry(monkeypatch)
    cancel_sent = asyncio.Event()
    executor._strategy.cancel.side_effect = lambda **kwargs: cancel_sent.set()
    executor.early_stop(keep_position=True)
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(cancel_sent.wait(), 1)
        executor.early_stop(keep_position=True)
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id='foreign'))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(executor.terminated.wait(), .05)
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        assert executor._open_order.order is order and not order.is_done
        executor._strategy.cancel.assert_called_once()
        fill(order, 'last-fill', '.2')
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        await asyncio.wait_for(executor.terminated.wait(), .25)
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        assert len(executor._held_position_orders) == 1
        assert Decimal(executor._held_position_orders[0]['executed_amount_base']) == Decimal('.2')
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
