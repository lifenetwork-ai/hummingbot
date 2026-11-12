"""Managed report publications wake the existing serialized controller loop."""
import asyncio
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from test.hummingbot.strategy_v2.controllers.test_reduction_controller import maker
from unittest.mock import AsyncMock, MagicMock

import pytest


def controlled_loop(controller):
    controller.update_interval = 60  # A publication must wake it, not shorten the timer.
    controller.on_start = AsyncMock()
    controller.on_stop = AsyncMock()
    controller.processed_data = {'ready': True}
    controller._session_admitted = True
    controller.exposure_within_caps = MagicMock(return_value=True)
    controller.determine_executor_actions = MagicMock(return_value=[])
    return controller


@pytest.mark.asyncio
async def test_report_wakes_managed_loop_and_bursts_do_not_spin():
    controller = controlled_loop(maker('1', '1')[0])
    first, second = asyncio.Event(), asyncio.Event()
    calls = []

    async def update():
        calls.append(len(calls))
        (first if len(calls) == 1 else second).set()

    controller.update_processed_data = update
    controller.start()
    try:
        await asyncio.wait_for(first.wait(), .5)
        await asyncio.sleep(.02)
        assert len(calls) == 1  # Readiness remains set; it is not a wake request.
        for _ in range(100):
            controller.executors_update_event.set()
        await asyncio.wait_for(second.wait(), .25)
        await asyncio.sleep(.02)
        assert len(calls) == 2
    finally:
        controller.stop()
        await controller.wait_closed()


@pytest.mark.asyncio
async def test_publication_during_control_work_is_retained_without_concurrent_cycles():
    controller = controlled_loop(maker('1', '1')[0])
    started, release, following = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, active = [], []

    async def update():
        assert not active
        active.append(True)
        calls.append(True)
        try:
            if len(calls) == 1:
                started.set()
                await release.wait()
            else:
                following.set()
        finally:
            active.pop()

    controller.update_processed_data = update
    controller.start()
    try:
        await asyncio.wait_for(started.wait(), .5)
        for _ in range(20):
            controller.executors_update_event.set()
        release.set()
        await asyncio.wait_for(following.wait(), .25)
        await asyncio.sleep(.02)
        assert len(calls) == 2
    finally:
        controller.stop()
        await controller.wait_closed()


@pytest.mark.asyncio
async def test_unmanaged_controller_retains_periodic_wait():
    controller = controlled_loop(make_controller(configuration())[0])
    seen = asyncio.Event()
    controller.update_processed_data = AsyncMock(side_effect=lambda: seen.set())
    controller.start()
    try:
        await asyncio.wait_for(seen.wait(), .5)
        controller.executors_update_event.set()
        await asyncio.sleep(.03)
        controller.update_processed_data.assert_awaited_once()
    finally:
        controller.stop()
        await controller.wait_closed()


@pytest.mark.asyncio
async def test_new_publication_survives_a_blocked_action_queue_handoff():
    controller = maker('1', '1')[0]
    controller.actions_queue = asyncio.Queue(maxsize=1)
    await controller.actions_queue.put('previous-batch')
    controller.executors_update_event.set()
    pending = asyncio.create_task(controller.send_actions(['current-batch']))
    try:
        await asyncio.sleep(0)
        assert not pending.done()
        controller.executors_update_event.set()  # A newer committed report during put().
        assert await controller.actions_queue.get() == 'previous-batch'
        await pending
        assert controller.executors_update_event.is_set()
        assert await controller.actions_queue.get() == ['current-batch']
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
