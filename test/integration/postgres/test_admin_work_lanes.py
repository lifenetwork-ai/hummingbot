"""Operational HTTP reads and Stop retain a DB slot while history holds one."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
async def test_history_queue_leaves_real_pg_balances_and_stop_available(pg_engine):
    started, release = threading.Event(), threading.Event()
    history_calls = 0

    def history(**filters):
        nonlocal history_calls
        with pg_engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT 1').scalar_one() == 1
            history_calls += 1
            started.set()
            assert release.wait(8)
        return dict(items=[], next_cursor=None)

    def observations():
        with pg_engine.connect() as connection:
            now = connection.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
        return dict(accounts=[], server_time=now, owner_available=True)

    def command(*args, **kwargs):
        with pg_engine.connect() as connection:
            now = connection.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
        return dict(id='stop-lanes', action='stop', session_id='session', expected_version=0,
                    effective_version=1, status='accepted', payload=dict(
                        action='stop', session_id='session', expected_version=0),
                    created_at=now, updated_at=now)

    settings = AdminSettings(True, 'operator', 'test-password', basic_auth_enabled=True)
    api = AdminAPI(None, settings, store=SimpleNamespace(
        orders=history, observations=observations, command=command,
        command_by_id=lambda identity: command()))
    app = web.Application(middlewares=[admin_middleware(settings)])
    api.register(app)
    async with TestClient(TestServer(app), headers={
            'Authorization': BasicAuth('operator', 'test-password').encode()}) as client:
        pending = []
        try:
            pending.append(asyncio.create_task(client.get(ADMIN_PREFIX + '/orders?limit=500')))
            assert await asyncio.to_thread(started.wait, 2)
            pending.extend(asyncio.create_task(client.get(ADMIN_PREFIX + '/orders?limit=500')) for _ in range(5))
            await asyncio.sleep(.02)
            balance = await asyncio.wait_for(client.get(ADMIN_PREFIX + '/balances'), 1)
            assert balance.status == 200 and (await balance.json())['owner_available']
            stop = await asyncio.wait_for(client.post(ADMIN_PREFIX + '/commands', json=dict(
                action='stop', session_id='session', expected_version=0),
                headers={'Idempotency-Key': 'stop-lanes'}), 1)
            body = await stop.json()
            assert stop.status == 202 and body['status'] == 'accepted', body
            receipt = await asyncio.wait_for(client.get(ADMIN_PREFIX + '/commands/stop-lanes'), 1)
            assert receipt.status == 200
            assert history_calls == 1 and not release.is_set()
            assert pg_engine.pool.checkedout() == 1
        finally:
            release.set()
            responses = await asyncio.gather(*pending, return_exceptions=True)
        assert all(not isinstance(response, Exception) and response.status == 200 for response in responses)
    assert history_calls == 6 and pg_engine.pool.checkedout() == 0
    assert api._database_executor.pending == api._database_executor.active == 0
