"""Optional HTTP reporting must leave the shared pool available to its owner."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.exc import TimeoutError as PoolTimeout

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
async def test_optional_api_queries_leave_room_for_owner_and_background_work(pg_engine):
    release = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    arrived = 0
    all_arrived = asyncio.Event()

    def read_page(**filters):
        nonlocal active, peak
        with pg_engine.connect() as connection:
            connection.exec_driver_sql('SELECT 1')
            with lock:
                active += 1
                peak = max(peak, active)
                if active >= 2:
                    entered.set()
            try:
                assert release.wait(5), 'Test did not release optional reads'
                return dict(items=[], next_cursor=None)
            finally:
                with lock:
                    active -= 1

    def owner_read():
        try:
            with pg_engine.connect() as connection:
                return connection.exec_driver_sql('SELECT 1').scalar_one() == 1
        except PoolTimeout:
            return False

    @web.middleware
    async def arrival(request, handler):
        nonlocal arrived
        arrived += 1
        if arrived == 5:
            all_arrived.set()
        return await handler(request)

    settings = AdminSettings(True, 'operator', 'test-password')
    app = web.Application(middlewares=[arrival, admin_middleware(settings)])
    AdminAPI(None, settings, store=SimpleNamespace(orders=read_page)).register(app)
    # Hold two connections to represent concurrent funding/outflow database work.
    with pg_engine.connect(), pg_engine.connect():
        async with TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'test-password').encode()}) as client:
            requests = [asyncio.create_task(client.get(ADMIN_PREFIX + '/orders')) for _ in range(5)]
            try:
                await asyncio.wait_for(all_arrived.wait(), timeout=3)
                assert await asyncio.to_thread(entered.wait, 3)
                # Yield to all submitted jobs; the assertion is about resource
                # capacity, not a microsecond performance threshold.
                await asyncio.sleep(.05)
                owner_available = await asyncio.to_thread(owner_read)
            finally:
                release.set()
                responses = await asyncio.gather(*requests, return_exceptions=True)
            assert owner_available, 'Reporting exhausted the owner connection budget'
            assert peak <= 2
            assert all(not isinstance(r, Exception) and r.status == 200 for r in responses)
    assert pg_engine.pool.checkedout() == 0
