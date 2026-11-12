"""Real loop scheduling around native cancel/complete events, without live I/O."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_order_checkpoint import closing_quote
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


async def close_task(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('partial', ['0', '.2'])
@pytest.mark.parametrize('immediate_ack', [False, True])
async def test_managed_cancel_ack_retires_without_waiting_five_seconds(monkeypatch, side, partial, immediate_ack):
    _, executor, order, _ = closing_quote(monkeypatch, side)
    executor.validate_sufficient_balance = AsyncMock()
    cancel_sent = asyncio.Event()

    def acknowledge():
        if Decimal(partial):
            fill(order, 'late-during-cancel', partial)
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))

    def cancel(**kwargs):
        cancel_sent.set()
        if immediate_ack:
            acknowledge()

    executor._strategy.cancel.side_effect = cancel
    executor.early_stop(keep_position=True)
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(cancel_sent.wait(), .5)
        if not immediate_ack:
            acknowledge()
        await asyncio.wait_for(executor.terminated.wait(), .25)
        await asyncio.wait_for(asyncio.shield(task), .25)
        assert executor.status == RunnableStatus.TERMINATED
        assert executor.close_type == (CloseType.POSITION_HOLD if Decimal(partial) else CloseType.EARLY_STOP)
        assert sum((Decimal(o['executed_amount_base']) for o in executor._held_position_orders), Decimal(0)) == Decimal(partial)
        executor._strategy.cancel.assert_called_once()
        executor._strategy.buy.assert_not_called()
        executor._strategy.sell.assert_not_called()
    finally:
        await close_task(task)


@pytest.mark.asyncio
async def test_legacy_cancel_keeps_its_existing_wait(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.config = executor.config.model_copy(update={'mm_session_id': None, 'mm_generation': None})
    executor.validate_sufficient_balance = AsyncMock()
    cancel_sent = asyncio.Event()
    executor._strategy.cancel.side_effect = lambda **kwargs: cancel_sent.set()
    executor.early_stop(keep_position=True)
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(cancel_sent.wait(), .5)
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(executor.terminated.wait(), .05)
        assert executor.status == RunnableStatus.SHUTTING_DOWN
    finally:
        await close_task(task)


@pytest.mark.asyncio
async def test_duplicate_stop_and_foreign_cancel_do_not_resend_or_release(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.validate_sufficient_balance = AsyncMock()
    cancel_sent = asyncio.Event()
    executor._strategy.cancel.side_effect = lambda **kwargs: cancel_sent.set()
    executor.early_stop(keep_position=True)
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(cancel_sent.wait(), .5)
        executor.early_stop(keep_position=True)
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id='foreign'))
        await asyncio.sleep(.03)
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        executor._strategy.cancel.assert_called_once()
        assert executor._order.order is order
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        await asyncio.wait_for(executor.terminated.wait(), .25)
    finally:
        await close_task(task)


@pytest.mark.asyncio
async def test_completion_exits_a_managed_long_period_loop_without_another_timer(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.update_interval = 60
    executor.validate_sufficient_balance = AsyncMock()
    executor.get_in_flight_order = lambda *args: order
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.sleep(.01)
        fill(order, 'complete', '1')
        order.current_state = OrderState.FILLED
        executor.process_order_completed_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        await asyncio.wait_for(asyncio.shield(task), .25)
        assert executor.close_type == CloseType.POSITION_HOLD
        assert executor.executed_amount_base == 1
    finally:
        await close_task(task)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('partial', ['0', '.2'])
async def test_managed_live_cancel_retires_quote_instead_of_resubmitting_prepared_price(monkeypatch, side, partial):
    _, executor, order, _ = closing_quote(monkeypatch, side)
    executor.validate_sufficient_balance = AsyncMock()
    executor._managed_quote_prepared = True
    if Decimal(partial):
        fill(order, 'before-venue-cancel', partial)
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    # A proved cancel can be a post-only rejection. The old preparation is not
    # new maker authority; native financial application must release this level.
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    task = asyncio.create_task(executor.control_loop())
    try:
        await asyncio.wait_for(asyncio.shield(task), .25)
        executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
        assert executor.status == RunnableStatus.TERMINATED
        assert executor.close_type == (CloseType.POSITION_HOLD if Decimal(partial) else CloseType.EARLY_STOP)
        assert sum((Decimal(x['executed_amount_base']) for x in executor._held_position_orders), Decimal(0)) == Decimal(partial)
        executor._strategy.buy.assert_not_called()
        executor._strategy.sell.assert_not_called()
    finally:
        await close_task(task)


def test_foreign_cancel_does_not_retire_a_live_managed_quote(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id='foreign'))
    assert executor.status == RunnableStatus.RUNNING
    assert executor._order.order is order


def test_unmanaged_maker_cancel_keeps_native_retry_behavior(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.config = executor.config.model_copy(update={'mm_session_id': None, 'mm_generation': None})
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    assert executor.status == RunnableStatus.RUNNING
    assert executor._order is None
