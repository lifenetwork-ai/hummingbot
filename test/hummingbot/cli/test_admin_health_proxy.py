"""Readiness failures retain their source and never acquire trading authority."""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, ClientTimeout, web

from hummingbot.cli.admin_service import AdminService


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,reason', [
    ('timeout', 'engine_probe_timeout'), ('invalid', 'engine_probe_invalid_response'),
    ('not_ready', 'engine_probe_http_status'),
])
async def test_real_engine_readiness_failure_is_bounded_observable_and_recovers_without_a_restart(caplog, failure, reason):
    mode, release = [failure], asyncio.Event()

    async def engine_health(request):
        if mode[0] == 'timeout':
            await release.wait()
        if mode[0] == 'invalid':
            return web.Response(text='private upstream response')
        return web.json_response({'state': 'halted' if mode[0] == 'not_ready' else 'running'},
                                 status=503 if mode[0] == 'not_ready' else 200)

    app = web.Application()
    app.router.add_get('/ready', engine_health)
    runner = web.AppRunner(app, shutdown_timeout=.1)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    host = AdminService(SimpleNamespace(), engine_port=site._server.sockets[0].getsockname()[1], stop=asyncio.Event())
    host.bootstrapped, host.phase = True, 'api_running'
    try:
        async with ClientSession(timeout=ClientTimeout(total=.025)) as client:
            host.client = client
            with caplog.at_level(logging.INFO):
                for _ in range(2):
                    response = await host.health(SimpleNamespace(path='/ready'))
                    assert response.status == 503
                    assert response.headers.get('X-Hbot-Health-Source') == 'api'
                    assert json.loads(response.text)['reason_code'] == reason
                # Reporting/readiness loss is not liveness loss or a trading
                # mutation. The subsequent actual probe must succeed afresh.
                assert (await host.health(SimpleNamespace(path='/live'))).status == 200
                assert not host.stop.is_set() and host.engine is host.store is None
                mode[0] = 'healthy'
                release.set()
                response = await host.health(SimpleNamespace(path='/ready'))
                assert response.status == 200 and json.loads(response.text)['state'] == 'running'
            failures = [r for r in caplog.records if getattr(r, 'event', None) == 'engine_health_proxy_unavailable']
            recoveries = [r for r in caplog.records if getattr(r, 'event', None) == 'engine_health_proxy_recovered']
            assert len(failures) == len(recoveries) == 1
            assert failures[0].reason_code == reason and 'elapsed_ms=' in failures[0].getMessage()
            assert 'private upstream response' not in caplog.text
    finally:
        release.set()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_engine_connection_failure_is_distinct_and_logs_no_exception_text(caplog):
    from unittest.mock import MagicMock
    host = AdminService(SimpleNamespace(), engine_port=1, stop=asyncio.Event())
    host.bootstrapped = True
    host.client = MagicMock()
    host.client.get.side_effect = ConnectionRefusedError('private network address or secret')
    with caplog.at_level(logging.WARNING):
        response = await host.health(SimpleNamespace(path='/ready'))
    assert response.status == 503 and json.loads(response.text)['reason_code'] == 'engine_probe_connection_error'
    assert 'private network address or secret' not in caplog.text
