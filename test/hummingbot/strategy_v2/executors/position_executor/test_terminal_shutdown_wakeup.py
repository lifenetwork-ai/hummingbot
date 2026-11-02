import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.event.events import OrderCancelledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig, TripleBarrierConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType, TrackedOrder


@pytest.fixture
def executor():
    strategy = MagicMock(spec=StrategyV2Base)
    strategy.current_timestamp = 100
    strategy.connectors = {'binance': MagicMock(spec=ExchangePyBase)}
    config = PositionExecutorConfig(id='shutdown', timestamp=99, trading_pair='ETH-USDT', connector_name='binance',
                                    side=TradeType.BUY, amount=Decimal('1'), entry_price=Decimal('100'),
                                    triple_barrier_config=TripleBarrierConfig())
    with patch.object(PositionExecutor, 'get_trading_rules'):
        actor = PositionExecutor(strategy, config)
    actor._status, actor.close_type = RunnableStatus.SHUTTING_DOWN, CloseType.POSITION_HOLD
    actor._open_order = tracked('open')
    return actor


def tracked(identity):
    order = TrackedOrder(identity)
    order.order = InFlightOrder(client_order_id=identity, exchange_order_id='ex-' + identity, trading_pair='ETH-USDT',
                                order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY, amount=Decimal('1'),
                                price=Decimal('100'), creation_timestamp=99, initial_state=OrderState.OPEN)
    return order


def cancel(actor, identity):
    actor.process_order_canceled_event(0, actor.connectors['binance'], OrderCancelledEvent(100, identity))


async def pending_shutdown(actor):
    entered, deadline = asyncio.Event(), asyncio.Event()

    async def sleep(seconds):
        assert seconds == 5
        entered.set()
        await deadline.wait()
    actor._sleep = AsyncMock(side_effect=sleep)
    task = asyncio.create_task(actor.control_shutdown_process())
    await asyncio.wait_for(entered.wait(), .5)
    return task, deadline


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['cancel', 'filled', 'failure'])
async def test_terminal_callback_ends_wait_before_five_second_deadline(executor, terminal):
    task, deadline = await pending_shutdown(executor)
    try:
        if terminal == 'cancel':
            cancel(executor, 'open')
        elif terminal == 'filled':
            executor._open_order.order.current_state = OrderState.FILLED
            executor.update_tracked_orders_with_order_id = MagicMock()
            executor.process_order_completed_event(0, None, SimpleNamespace(order_id='open', base_asset_amount=Decimal('1')))
        else:
            executor.process_order_failed_event(0, None, SimpleNamespace(order_id='open', error_type='SessionSubmissionBlocked', error_message=None))
        done, _ = await asyncio.wait({task}, timeout=.1)
        assert task in done, 'Confirmed terminal orders still wait for the fixed five-second sleep'
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        assert executor.all_orders_completed()
    finally:
        deadline.set()
        await task


@pytest.mark.asyncio
async def test_partial_ack_or_unrelated_event_does_not_retry_or_finalize_pending_order(executor):
    executor._take_profit_limit_order = tracked('profit')
    task, deadline = await pending_shutdown(executor)
    try:
        count = executor._strategy.cancel.call_count
        cancel(executor, 'unrelated')
        assert not executor._shutdown_orders_changed.is_set()
        cancel(executor, 'open')
        await asyncio.sleep(.02)
        assert not task.done() and executor.status == RunnableStatus.SHUTTING_DOWN
        assert executor._strategy.cancel.call_count == count
        cancel(executor, 'profit')
        done, _ = await asyncio.wait({task}, timeout=.1)
        assert task in done
    finally:
        deadline.set()
        await task


@pytest.mark.asyncio
async def test_no_ack_keeps_existing_timeout_and_no_additional_cancel_attempt(executor):
    task, deadline = await pending_shutdown(executor)
    count = executor._strategy.cancel.call_count
    await asyncio.sleep(.02)
    assert not task.done()
    deadline.set()
    await asyncio.wait_for(task, .5)
    assert executor._strategy.cancel.call_count == count
    assert executor.status == RunnableStatus.SHUTTING_DOWN and not executor.all_orders_completed()
    executor._sleep.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
async def test_known_terminal_before_wait_preserves_partial_fill_and_skips_idle_timer(executor):
    executor._open_order.order.executed_amount_base = Decimal('0.123456789012345678')
    executor._open_order.order.executed_amount_quote = Decimal('12.3456789012345678')
    cancel(executor, 'open')
    executor._sleep = AsyncMock()
    await executor.control_shutdown_process()
    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == CloseType.POSITION_HOLD
    assert Decimal(executor._held_position_orders[0]['executed_amount_base']) == Decimal('0.123456789012345678')
    executor._sleep.assert_not_called()
    executor._strategy.sell.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_wait_cancellation_drains_its_children(executor):
    task, deadline = await pending_shutdown(executor)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith('position-shutdown-')]
    assert executor.status == RunnableStatus.SHUTTING_DOWN


@pytest.mark.asyncio
async def test_terminal_callback_before_wait_registration_is_not_lost(executor):
    executor.cancel_open_orders = MagicMock(side_effect=lambda: cancel(executor, 'open'))
    executor._sleep = AsyncMock()
    await executor.control_shutdown_process()
    assert executor.all_orders_completed() and executor.status == RunnableStatus.SHUTTING_DOWN
    executor._sleep.assert_not_called()


@pytest.mark.asyncio
async def test_deadline_failure_is_propagated_and_waiter_drained(executor):
    executor._sleep = AsyncMock(side_effect=RuntimeError('deadline failed'))
    with pytest.raises(RuntimeError, match='deadline failed'):
        await executor.control_shutdown_process()
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith('position-shutdown-')]
    assert executor.status == RunnableStatus.SHUTTING_DOWN


@pytest.mark.asyncio
async def test_notification_failure_is_propagated_instead_of_spinning(executor):
    executor._shutdown_orders_changed.wait = AsyncMock(side_effect=RuntimeError('notification failed'))
    task, deadline = await pending_shutdown(executor)
    try:
        done, _ = await asyncio.wait({task}, timeout=.05)
        assert task in done, 'Failed notification task was discarded and restarted repeatedly'
        with pytest.raises(RuntimeError, match='notification failed'):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        deadline.set()
