"""Control admission with the deployment template's eight concurrent requests."""
import asyncio
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
async def test_control_priority_accepts_stop_and_receipt_before_pending_slow_reads(pg_engine):
    arrived = 0
    all_arrived = asyncio.Event()
    entered = threading.Event()
    active = 0
    lock = threading.Lock()

    def orders(**filters):
        nonlocal active
        with pg_engine.connect() as connection:
            with lock:
                active += 1
                if active >= 2:
                    entered.set()
            # This remains below the unchanged two-second server statement budget.
            connection.exec_driver_sql('SELECT pg_sleep(1.8)')
            return dict(items=[], next_cursor=None)

    def command(key, payload, **kwargs):
        now = datetime.now(timezone.utc)
        return dict(id='stop-1', action='stop', session_id='audit-session', expected_version=0,
                    effective_version=1, status='accepted', payload=payload, created_at=now, updated_at=now)

    @web.middleware
    async def arrivals(request, handler):
        nonlocal arrived
        if request.path.endswith('/orders'):
            arrived += 1
            if arrived == 7:
                all_arrived.set()
        return await handler(request)

    settings = AdminSettings(True, 'operator', 'audit-password')
    app = web.Application(middlewares=[arrivals, admin_middleware(settings)])
    stored = dict(action='stop', expected_version=0, session_id='audit-session')
    storage = SimpleNamespace(orders=orders, command=command, command_by_id=lambda identity: command('', stored))
    AdminAPI(None, settings, store=storage).register(app)
    async with TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'audit-password').encode()}) as client:
        reads = [asyncio.create_task(client.get(ADMIN_PREFIX + '/orders')) for _ in range(7)]
        try:
            await asyncio.wait_for(all_arrived.wait(), 3)
            assert await asyncio.to_thread(entered.wait, 3)
            with pg_engine.connect() as connection:
                owner_available = connection.exec_driver_sql('SELECT 1').scalar_one() == 1
            started = time.monotonic()
            response = await client.post(ADMIN_PREFIX + '/commands', json=dict(action='stop', expected_version=0, session_id='audit-session'),
                                         headers={'Idempotency-Key': 'audit-stop'})
            elapsed = time.monotonic() - started
            assert (await response.json())['status'] == 'accepted'
            assert response.status == 202 and owner_available
            assert elapsed < 2.7, 'Stop must wait for at most the running reads, not the report backlog'
            receipt_started = time.monotonic()
            receipt = await client.get(ADMIN_PREFIX + '/commands/stop-1')
            assert receipt.status == 200
            assert time.monotonic() - receipt_started < 2.7
        finally:
            responses = await asyncio.gather(*reads, return_exceptions=True)
        assert all(not isinstance(row, Exception) and row.status == 200 for row in responses)
