"""Terminal readback stays bounded, complete and cancellation-safe per order."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.core.bot_ownership import BotOwnership


def fixture_orders(count):
    orders = [SimpleNamespace(client_order_id=str(i), update_exchange_order_id=Mock()) for i in range(count)]
    tracker = SimpleNamespace(process_trade_update=Mock(), process_order_update=AsyncMock())
    return orders, SimpleNamespace(_order_tracker=tracker)


@pytest.mark.asyncio
async def test_slow_terminal_reads_make_progress_without_exceeding_three_requests():
    orders, connector = fixture_orders(15)
    release, three_entered = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def status(order):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 3:
            three_entered.set()
        try:
            await release.wait()
            return SimpleNamespace(exchange_order_id='venue-' + order.client_order_id)
        finally:
            active -= 1

    connector._request_order_status = AsyncMock(side_effect=status)
    connector._all_trade_updates_for_order = AsyncMock(return_value=[])
    task = asyncio.create_task(BotOwnership._refresh_orders(connector, orders))
    try:
        await asyncio.wait_for(three_entered.wait(), 0.5)
        release.set()
        await task
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert peak == 3
    assert connector._request_order_status.await_count == 15
    assert connector._all_trade_updates_for_order.await_count == 15
    assert connector._order_tracker.process_order_update.await_count == 15
    for order in orders:
        order.update_exchange_order_id.assert_called_once_with('venue-' + order.client_order_id)


@pytest.mark.asyncio
async def test_failed_read_cancels_and_drains_siblings_before_raising():
    orders, connector = fixture_orders(3)
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0

    async def status(order):
        nonlocal active
        active += 1
        if active == 3:
            entered.set()
        try:
            await entered.wait()
            if order.client_order_id == '0':
                raise OSError('terminal read unavailable')
            await release.wait()
        finally:
            active -= 1

    connector._request_order_status = AsyncMock(side_effect=status)
    connector._all_trade_updates_for_order = AsyncMock(return_value=[])
    with pytest.raises(OSError, match='terminal read unavailable'):
        await asyncio.wait_for(BotOwnership._refresh_orders(connector, orders), 1)
    assert active == 0
    connector._order_tracker.process_order_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_read_drains_requests_before_returning_control():
    orders, connector = fixture_orders(3)
    entered = asyncio.Event()
    active = 0

    async def status(order):
        nonlocal active
        active += 1
        if active == 3:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    connector._request_order_status = AsyncMock(side_effect=status)
    connector._all_trade_updates_for_order = AsyncMock(return_value=[])
    task = asyncio.create_task(BotOwnership._refresh_orders(connector, orders))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert active == 0
    connector._order_tracker.process_order_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_fill_is_applied_before_its_terminal_order_update():
    orders, connector = fixture_orders(3)
    trace = []

    async def status(order):
        trace.append(('status', order.client_order_id))
        return SimpleNamespace(exchange_order_id='venue-' + order.client_order_id)

    async def fills(order):
        trace.append(('fills', order.client_order_id))
        return [order.client_order_id]

    async def terminal(row):
        trace.append(('terminal', row.exchange_order_id.removeprefix('venue-')))

    connector._request_order_status = AsyncMock(side_effect=status)
    connector._all_trade_updates_for_order = AsyncMock(side_effect=fills)
    connector._order_tracker.process_trade_update.side_effect = lambda trade: trace.append(('trade', trade))
    connector._order_tracker.process_order_update.side_effect = terminal
    await BotOwnership._refresh_orders(connector, orders)
    for order in orders:
        assert [stage for stage, identity in trace if identity == order.client_order_id] == ['status', 'fills', 'trade', 'terminal']


@pytest.mark.asyncio
async def test_empty_readback_has_no_requests():
    _, connector = fixture_orders(0)
    connector._request_order_status = AsyncMock()
    connector._all_trade_updates_for_order = AsyncMock()
    await BotOwnership._refresh_orders(connector, [])
    connector._request_order_status.assert_not_awaited()
