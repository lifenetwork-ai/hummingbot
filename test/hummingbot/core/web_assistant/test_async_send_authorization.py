"""An awaited DB gate after throttling must precede the final synchronous fence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant


def assistant(throttler=None):
    response = SimpleNamespace(status=200, content_type="application/json", json=AsyncMock(return_value={"ok": True}))
    session = SimpleNamespace(request=AsyncMock(return_value=response))
    return RESTAssistant(RESTConnection(session), throttler or AsyncThrottler([RateLimit("order", 10000, 1)])), session


@pytest.mark.asyncio
async def test_awaited_authorization_finishes_before_sync_fence_and_network():
    api, session = assistant()
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def authorize():
        order.append("db-start")
        started.set()
        await release.wait()
        order.append("db-finish")

    def fence():
        order.append("fence")
        assert not session.request.called

    task = asyncio.create_task(api.execute_request(url="https://synthetic.invalid/order", throttler_limit_id="order",
                                                   async_pre_send_check=authorize, pre_send_check=fence))
    try:
        await asyncio.wait_for(started.wait(), .5)
        assert not session.request.called
        release.set()
        assert await task == {"ok": True}
        assert order == ["db-start", "db-finish", "fence"]
        session.request.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_final_sync_fence_rejects_stop_during_db_wait():
    api, session = assistant()
    stopped = False

    async def authorize():
        nonlocal stopped
        await asyncio.sleep(.01)
        stopped = True

    def fence():
        if stopped:
            raise PermissionError("STOPPED")

    with pytest.raises(PermissionError):
        await api.execute_request(url="https://synthetic.invalid/order", throttler_limit_id="order",
                                  async_pre_send_check=authorize, pre_send_check=fence)
    session.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "timeout", "cancel"])
async def test_failed_cancelled_or_timed_out_authorization_never_sends(failure):
    api, session = assistant()
    entered = asyncio.Event()

    async def authorize():
        entered.set()
        if failure == "exception":
            raise PermissionError("DB_DENIED")
        await asyncio.sleep(10)

    task = asyncio.create_task(api.execute_request(url="https://synthetic.invalid/order", throttler_limit_id="order",
                                                   async_pre_send_check=authorize, timeout=.05))
    try:
        if failure == "cancel":
            await asyncio.wait_for(entered.wait(), .5)
            task.cancel()
        error = {"exception": PermissionError, "timeout": TimeoutError, "cancel": asyncio.CancelledError}[failure]
        with pytest.raises(error):
            await task
        session.request.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_database_gate_runs_after_real_throttler_wait():
    throttle = AsyncThrottler([RateLimit("order", 1, .1)], retry_interval=.01, safety_margin_pct=0)
    async with throttle.execute_task("order"):
        pass
    api, session = assistant(throttle)
    entered = asyncio.Event()

    async def authorize():
        entered.set()

    task = asyncio.create_task(api.execute_request(url="https://synthetic.invalid/order", throttler_limit_id="order",
                                                   async_pre_send_check=authorize))
    try:
        await asyncio.sleep(.02)
        assert not entered.is_set() and not session.request.called
        assert await task == {"ok": True}
        assert entered.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
