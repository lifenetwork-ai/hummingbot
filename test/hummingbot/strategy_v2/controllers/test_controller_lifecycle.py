"""Controller retirement must finish even while startup awaits public data."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase


def controller():
    return ControllerBase(ControllerConfigBase(id="lifecycle", controller_name="test"), MagicMock(), asyncio.Queue())


@pytest.mark.asyncio
async def test_stop_interrupts_blocked_startup_and_runs_cleanup():
    item = controller()
    entered, closed = asyncio.Event(), asyncio.Event()

    async def startup():
        entered.set()
        await asyncio.Event().wait()

    item.on_start = startup
    item.on_stop = closed.set
    before = asyncio.all_tasks()
    item.start()
    try:
        await asyncio.wait_for(entered.wait(), .5)
        item.stop()
        await asyncio.wait_for(closed.wait(), .5)
        await item.wait_closed()
        assert item._control_loop_task.done()
    finally:
        for task in asyncio.all_tasks() - before:
            task.cancel()
        await asyncio.gather(*(asyncio.all_tasks() - before), return_exceptions=True)


@pytest.mark.asyncio
async def test_twenty_immediate_restarts_do_not_overlap_old_control_work():
    item = controller()
    active, peak, starts = 0, 0, 0
    entered = asyncio.Queue()
    stopped = AsyncMock()

    async def work():
        nonlocal active, peak, starts
        active += 1
        peak = max(peak, active)
        starts += 1
        entered.put_nowait(starts)
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    item.control_task = work
    item.on_stop = stopped
    before = asyncio.all_tasks()
    try:
        item.start()
        for step in range(20):
            assert await asyncio.wait_for(entered.get(), .5) == step + 1
            item.stop()
            item.start()
        assert await asyncio.wait_for(entered.get(), .5) == 21
        item.stop()
        await asyncio.wait_for(item.wait_closed(), .5)
        assert (active, peak, starts) == (0, 1, 21)
        assert stopped.await_count == 21
    finally:
        for task in asyncio.all_tasks() - before:
            task.cancel()
        await asyncio.gather(*(asyncio.all_tasks() - before), return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_failure_still_cleans_up_once():
    item = controller()
    closed = asyncio.Event()
    item.on_start = AsyncMock(side_effect=ValueError("unavailable public data"))
    item.on_stop = closed.set
    item.start()
    await asyncio.wait_for(closed.wait(), .5)
    await item.wait_closed()


@pytest.mark.asyncio
async def test_life_reference_release_finishes_before_wait_closed_returns():
    from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
    item, _ = make_controller(configuration(scenario="follow_token", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    started, releasing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    item.on_start = AsyncMock(side_effect=started.set)

    async def release_books(*args):
        releasing.set()
        await release.wait()

    item.market_data_provider.release_order_books = AsyncMock(side_effect=release_books)
    item.start()
    await asyncio.wait_for(started.wait(), .5)
    item.stop()
    waiting = asyncio.create_task(item.wait_closed())
    try:
        await asyncio.wait_for(releasing.wait(), .5)
        assert not waiting.done()
    finally:
        release.set()
        await asyncio.wait_for(waiting, .5)
    item.market_data_provider.release_order_books.assert_awaited_once_with("okx", ["BTC-USDT"], item)
