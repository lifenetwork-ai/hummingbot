"""Control stays ahead of pending reports; canceled clients cannot grow DB work."""
import asyncio
import threading
from test.hummingbot.cli.test_admin_database_executor import api

import pytest

from hummingbot.cli.admin_api import APIUnavailable


@pytest.mark.asyncio
async def test_control_overtakes_reports_without_extra_workers():
    instance = api()
    release = threading.Event()
    ready = threading.Event()
    lock = threading.Lock()
    started = 0
    order = []

    def blocking_read():
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                ready.set()
        assert release.wait(5)

    tasks = []
    try:
        tasks = [asyncio.create_task(instance.run_database(blocking_read)) for _ in range(2)]
        assert await asyncio.to_thread(ready.wait, 2)
        queued = [asyncio.create_task(instance.run_database(lambda: order.append('report'))) for _ in range(7)]
        tasks.extend(queued)
        await asyncio.sleep(.01)
        control = asyncio.create_task(instance.run_control_database(lambda: order.append('control')))
        tasks.append(control)
        await asyncio.sleep(.01)
        release.set()
        await asyncio.gather(*tasks)
        # Either running thread may finish first, but the next dispatched job is control.
        assert order.index('control') <= 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await instance.close(None)


@pytest.mark.asyncio
async def test_overload_is_bounded_and_reserves_control_admission():
    instance = api()
    release = threading.Event()
    ready = threading.Event()
    lock = threading.Lock()
    started = 0

    def blocked():
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                ready.set()
        assert release.wait(5)

    tasks = []
    try:
        tasks = [asyncio.create_task(instance.run_database(blocked)) for _ in range(12)]
        assert await asyncio.to_thread(ready.wait, 2)
        await asyncio.sleep(.01)
        excess = asyncio.create_task(instance.run_database(lambda: None))
        tasks.append(excess)
        await asyncio.sleep(.01)
        assert excess.done(), 'Excess reporting must be rejected instead of queued'
        with pytest.raises(APIUnavailable):
            await excess
        # Disconnected running requests continue occupying actual worker slots.
        for task in tasks[:2]:
            task.cancel()
        await asyncio.gather(*tasks[:2], return_exceptions=True)
        control = asyncio.create_task(instance.run_control_database(lambda: 'accepted'))
        tasks.append(control)
        await asyncio.sleep(.01)
        assert not control.done()
        release.set()
        assert await control == 'accepted'
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await instance.close(None)


@pytest.mark.asyncio
async def test_database_failure_releases_admission_and_close_cancels_pending():
    instance = api()
    release = threading.Event()
    ready = threading.Event()
    lock = threading.Lock()
    active = 0

    def blocked():
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                ready.set()
        assert release.wait(5)

    def fail():
        raise ValueError('synthetic-database-failure')

    tasks = []
    closing = None
    try:
        with pytest.raises(ValueError, match='synthetic-database-failure'):
            await instance.run_control_database(fail)
        assert await instance.run_database(lambda: 'still available') == 'still available'
        tasks = [asyncio.create_task(instance.run_database(blocked)) for _ in range(2)]
        assert await asyncio.to_thread(ready.wait, 2)
        queued = asyncio.create_task(instance.run_control_database(lambda: pytest.fail('Closed queue executed')))
        tasks.append(queued)
        await asyncio.sleep(.01)
        closing = asyncio.create_task(instance.close(None))
        await asyncio.sleep(.01)
        assert queued.cancelled() and not closing.done()
        release.set()
        await closing
        assert instance._database_executor.pending == instance._database_executor.active == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await instance.close(None)
