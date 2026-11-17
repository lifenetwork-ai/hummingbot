import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.mm_quote_observation import collect_quote_observation, read_observed_json


@pytest.mark.asyncio
async def test_both_reads_start_without_waiting_for_the_other_response():
    started = set()
    both = asyncio.Event()

    async def read(path):
        started.add(path)
        if len(started) == 2:
            both.set()
        await both.wait()
        return {'path': path}

    result = await asyncio.wait_for(collect_quote_observation(read), .5)
    assert started == {'/status', '/statistics/liquidity'}
    assert result['status']['path'] == '/status'
    assert result['liquidity']['path'] == '/statistics/liquidity'
    assert result['sample_errors'] == []
    assert set(result['reads']) == {'status', 'liquidity'}


@pytest.mark.asyncio
async def test_delayed_response_retains_old_observation_instead_of_relabeling_it_fresh():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    status = {'observation': {'observed_at': (now - timedelta(seconds=6.5)).isoformat()}}
    liquidity = {'observed_at': now.isoformat()}

    async def read(path):
        return status if path == '/status' else liquidity

    result = await collect_quote_observation(read, wall_clock=lambda: now)
    assert result['status'] is status and result['liquidity'] is liquidity
    assert result['observation_ages_seconds'] == {'status': 6.5, 'liquidity': 0.0}
    assert result['observation_skew_seconds'] == 6.5


@pytest.mark.asyncio
async def test_failure_keeps_other_read_and_exposes_only_exception_type():
    async def read(path):
        if path == '/status':
            raise OSError('private authorization or driver details')
        return {'observed_at': '2026-01-01T00:00:00Z'}

    result = await collect_quote_observation(read)
    assert result['status'] is None and result['liquidity'] is not None
    assert result['sample_errors'] == [{'source': 'status', 'error_type': 'OSError'}]
    assert 'private authorization' not in json.dumps(result)


@pytest.mark.asyncio
async def test_timeout_joins_read_without_reusing_an_old_result():
    closed = asyncio.Event()

    async def read(path):
        if path == '/statistics/liquidity':
            return {}
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    result = await collect_quote_observation(read, timeout=.02)
    assert closed.is_set()
    assert result['status'] is None and result['liquidity'] == {}
    assert result['sample_errors'] == [{'source': 'status', 'error_type': 'TimeoutError'}]


@pytest.mark.asyncio
async def test_outer_cancellation_joins_both_readers():
    started = set()
    closed = set()
    ready = asyncio.Event()

    async def read(path):
        started.add(path)
        if len(started) == 2:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.add(path)

    task = asyncio.create_task(collect_quote_observation(read))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == started


@pytest.mark.asyncio
async def test_status_http_timing_correlates_with_server_without_client_identity_authority(caplog):
    import logging
    from test.hummingbot.cli.test_admin_api import client

    async with client() as (http, _):
        async def read(path, *, timing):
            if path != '/status':
                return {}
            return await read_observed_json(http, '/api/v1/admin/status', timing=timing)

        with caplog.at_level(logging.INFO, logger='hummingbot.cli.admin_api'):
            result = await collect_quote_observation(read, read_with_timing=True)
        timing = result['reads']['status']
        assert timing['http_status'] == 200 and timing['client_phase'] == 'complete'
        assert set(timing['server_timing_ms']) == {'status', 'db_queue', 'db_run'}
        record = next(r for r in caplog.records if getattr(r, 'event', None) == 'admin_status_read')
        assert timing['server_request_id'] == record.request_id
        assert timing['client_request_hash'] == record.client_request_hash
        assert len(timing['client_request_hash']) == 16


@pytest.mark.asyncio
async def test_status_http_deadline_retains_error_headers_without_turning_error_into_data(monkeypatch):
    import threading
    from test.hummingbot.cli.test_admin_api import client

    from hummingbot.cli.admin_api import ADMIN_ACCESS_KEY, AdminAPI

    monkeypatch.setattr(AdminAPI, 'STATUS_READ_SECONDS', .02)
    release = threading.Event()

    def hold(**kwargs):
        assert release.wait(3)

    async with client() as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        api.host.store.read.side_effect = hold

        async def read(path, *, timing):
            if path != '/status':
                return {}
            return await read_observed_json(http, '/api/v1/admin/status', timing=timing)

        try:
            result = await collect_quote_observation(read, timeout=.5, read_with_timing=True)
            assert result['status'] is None and result['liquidity'] == {}
            assert result['reads']['status']['http_status'] == 503
            assert result['reads']['status']['server_request_id']
            assert result['sample_errors'] == [{'source': 'status', 'error_type': 'ClientResponseError'}]
        finally:
            release.set()


@pytest.mark.asyncio
async def test_timeout_before_headers_preserves_correlatable_hash_without_inventing_server_latency():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def handler(request):
        seen.append(request.headers['X-Request-ID'])
        entered.set()
        await release.wait()
        return web.json_response({})

    app = web.Application()
    app.router.add_get('/status', handler)
    async with TestClient(TestServer(app)) as http:
        async def read(path, *, timing):
            if path != '/status':
                return {}
            return await read_observed_json(http, path, timing=timing)

        try:
            result = await collect_quote_observation(read, timeout=.03, read_with_timing=True)
            assert entered.is_set() and result['status'] is None
            timing = result['reads']['status']
            assert timing['client_phase'] == 'awaiting_headers'
            assert 'server_timing_ms' not in timing and 'server_request_id' not in timing
            import hashlib
            assert timing['client_request_hash'] == hashlib.sha256(seen[0].encode()).hexdigest()[:16]
            assert seen[0] not in json.dumps(result)
        finally:
            release.set()


@pytest.mark.asyncio
async def test_timeout_during_body_retains_received_header_evidence():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    release = asyncio.Event()

    async def handler(request):
        response = web.StreamResponse(headers={'Content-Type': 'application/json',
                                               'X-Request-ID': 'c' * 32, 'Server-Timing': 'status;dur=12.5'})
        await response.prepare(request)
        await release.wait()
        return response

    app = web.Application()
    app.router.add_get('/status', handler)
    async with TestClient(TestServer(app)) as http:
        async def read(path, *, timing):
            return {} if path != '/status' else await read_observed_json(http, path, timing=timing)

        try:
            result = await collect_quote_observation(read, timeout=.03, read_with_timing=True)
            timing = result['reads']['status']
            assert result['status'] is None and timing['error_type'] == 'TimeoutError'
            assert timing['client_phase'] == 'body' and timing['http_status'] == 200
            assert timing['server_request_id'] == 'c' * 32
            assert timing['server_timing_ms'] == {'status': 12.5}
        finally:
            release.set()


@pytest.mark.asyncio
async def test_http_metadata_never_exports_arbitrary_headers_or_nonfinite_timings():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    app = web.Application()

    async def handler(request):
        return web.json_response({}, headers={'X-Request-ID': 'private-server-value',
                                 'Server-Timing': 'status;dur=NaN, db_queue;dur=1e999, db_run;dur=3.5;desc="finished", private-token;dur=1'})

    app.router.add_get('/status', handler)
    async with TestClient(TestServer(app)) as http:
        async def read(path, *, timing):
            return {} if path != '/status' else await read_observed_json(http, path + '?secret=private-query', timing=timing)

        result = await collect_quote_observation(read, read_with_timing=True)
        assert result['reads']['status']['server_timing_ms'] == {'db_run': 3.5}
        assert result['reads']['status']['database_phase'] == 'finished'
        assert 'server_request_id' not in result['reads']['status']
        assert 'private-' not in json.dumps(result)
