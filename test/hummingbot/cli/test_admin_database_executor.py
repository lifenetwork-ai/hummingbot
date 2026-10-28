import asyncio
import contextvars
import threading

import pytest

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings


def api():
    return AdminAPI(None, AdminSettings(True, 'operator', 'test-password'))


@pytest.mark.asyncio
async def test_database_workers_keep_request_context_and_run_off_the_loop():
    instance = api()
    request = contextvars.ContextVar('database_request')
    loop_thread = threading.get_ident()
    request.set('receipt-identity')
    try:
        result = await instance.run_database(lambda: (request.get(), threading.get_ident()))
        assert result[0] == 'receipt-identity' and result[1] != loop_thread
    finally:
        await instance.close(None)


@pytest.mark.asyncio
async def test_cancellation_does_not_allow_more_database_workers_or_lose_shutdown():
    instance = api()
    release = threading.Event()
    ready = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    third_started = threading.Event()

    def blocked():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                ready.set()
        try:
            assert release.wait(5)
        finally:
            with lock:
                active -= 1

    tasks = []
    try:
        tasks = [asyncio.create_task(instance.run_database(blocked)) for _ in range(2)]
        assert await asyncio.to_thread(ready.wait, 2)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        third = asyncio.create_task(instance.run_database(third_started.set))
        await asyncio.sleep(.02)
        assert not third_started.is_set(), 'Canceled awaiters must not free running worker capacity'
        release.set()
        await third
        assert third_started.is_set() and peak == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await instance.close(None)
    assert active == 0
    with pytest.raises(RuntimeError):
        await instance.run_database(lambda: None)
