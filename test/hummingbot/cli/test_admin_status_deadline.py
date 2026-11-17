"""Status deadlines cannot abandon physical DB work or claim stale readiness."""
import asyncio
import hashlib
import logging
import threading
from test.hummingbot.cli.test_admin_api import client

import pytest

from hummingbot.cli.admin_api import ADMIN_ACCESS_KEY, ADMIN_PREFIX, AdminAPI
from hummingbot.cli.admin_database import DatabaseWorkTiming


@pytest.mark.asyncio
@pytest.mark.parametrize('queued', [False, True])
async def test_status_deadline_keeps_running_capacity_and_cancels_unsent_reads(monkeypatch, caplog, queued):
    monkeypatch.setattr(AdminAPI, 'STATUS_READ_SECONDS', .03, raising=False)
    entered, release = threading.Event(), threading.Event()
    request = blocking = None

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return None

    async with client() as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        api.host.service_status = lambda: dict(observed_at=1700000000, state_source='process',
                                               persisted_state_available=False, phase='api_running',
                                               reason_code='reporting_unavailable', stale=True, incidents=[])
        if queued:
            blocking = asyncio.create_task(api.run_control_database(hold))
        else:
            api.host.store.read.side_effect = hold
        trace = 'a' * 32
        try:
            with caplog.at_level(logging.INFO, logger='hummingbot.cli.admin_api'):
                request = asyncio.create_task(http.get(ADMIN_PREFIX + '/status', headers={'X-Request-ID': trace}))
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(asyncio.shield(request), .4)
            body = await response.json()
            assert response.status == 503 and body['code'] == 'unavailable'
            assert body['service']['stale'] and not body['service']['persisted_state_available']
            assert api._database_executor.active == 1
            assert response.headers['X-Request-ID'] == body['request_id'] != trace
            assert 'status;dur=' in response.headers['Server-Timing']
            records = [r for r in caplog.records if getattr(r, 'event', None) == 'admin_status_read']
            assert len(records) == 1
            assert records[0].request_id == body['request_id']
            assert records[0].client_request_hash == hashlib.sha256(trace.encode()).hexdigest()[:16]
            assert records[0].database_phase == ('queued' if queued else 'running')
            assert records[0].reason_code == 'status_read_timeout'
            release.set()
            if blocking:
                await blocking
            await api.run_database(lambda: None)  # Join the physical job/skip expired queued work.
            assert api._database_executor.active == api._database_executor.pending == 0
            if queued:
                api.host.store.read.assert_not_called()
        finally:
            release.set()
            if blocking:
                await asyncio.gather(blocking, return_exceptions=True)
            if request:
                await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_fast_status_telemetry_uses_server_identity_and_sanitized_client_hash(caplog):
    async with client() as (http, _):
        with caplog.at_level(logging.INFO, logger='hummingbot.cli.admin_api'):
            response = await http.get(ADMIN_PREFIX + '/status', headers={
                'X-Request-ID': 'b' * 32, 'Origin': 'https://dashboard.example'})
        assert response.status == 200
        assert 'db_queue;dur=' in response.headers['Server-Timing']
        assert 'db_run;dur=' in response.headers['Server-Timing']
        assert 'Server-Timing' in response.headers['Access-Control-Expose-Headers']
        record = next(r for r in caplog.records if getattr(r, 'event', None) == 'admin_status_read')
        assert record.database_phase == 'finished' and record.reason_code == 'completed'
        assert record.request_id == response.headers['X-Request-ID'] != 'b' * 32
        assert record.queue_ms >= 0 and record.worker_ms >= 0


@pytest.mark.asyncio
async def test_untrusted_trace_and_sql_parameters_never_reach_status_diagnostics(caplog):
    from sqlalchemy.exc import SQLAlchemyError

    async with client() as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        api.host.store.read.side_effect = SQLAlchemyError('private-dsn-password-query')
        with caplog.at_level(logging.INFO, logger='hummingbot.cli.admin_api'):
            response = await http.get(ADMIN_PREFIX + '/status', headers={'X-Request-ID': 'private-client-token'})
        assert response.status == 503
        record = next(r for r in caplog.records if getattr(r, 'event', None) == 'admin_status_read')
        assert record.client_request_hash is None
        assert 'private-' not in caplog.text and 'private-' not in await response.text()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['queued', 'started', 'finished', 'snapshot'])
async def test_status_timing_failure_preserves_native_response(monkeypatch, stage):
    def fail(*args):
        raise MemoryError('diagnostic-failure')

    monkeypatch.setattr(DatabaseWorkTiming, stage, fail)
    async with client() as (http, _):
        response = await http.get(ADMIN_PREFIX + '/status')
        assert response.status == 200
        assert (await response.json())['bot_id'] == 'hummingbot'


@pytest.mark.asyncio
async def test_status_deadline_also_bounds_the_optional_recovery_lookup(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(AdminAPI, 'STATUS_READ_SECONDS', .03)
    release, entered = threading.Event(), threading.Event()
    state = dict(bot_id='hummingbot', desired_state='stopped', control_version=1, acknowledged_version=1,
                 owner_uuid='owner', owner_generation=1, heartbeat_age_s=0, database_now=1700000000,
                 status={'phase': 'halted'}, mm_session=None, recovery_summary={'halted': True})
    async with client(state=state) as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        store = MagicMock()

        def latest():
            entered.set()
            assert release.wait(3)

        store.latest.side_effect = latest
        api.recovery_store = lambda: store
        request = asyncio.create_task(http.get(ADMIN_PREFIX + '/status'))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            response = await asyncio.wait_for(asyncio.shield(request), .4)
            assert response.status == 503 and (await response.json())['code'] == 'unavailable'
            assert api._database_executor.active == 1
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_cancellation_is_not_converted_to_status_timeout(monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setattr(AdminAPI, 'STATUS_READ_SECONDS', 2)
    entered, release = threading.Event(), threading.Event()

    def hold(**kwargs):
        entered.set()
        assert release.wait(3)

    async with client() as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        api.host.store.read.side_effect = hold
        request = asyncio.create_task(api.status(make_mocked_request('GET', ADMIN_PREFIX + '/status')))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert api._database_executor.active == 1
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_rejected_status_work_is_not_reported_as_an_active_worker(caplog):
    async with client() as (http, _):
        api = http.server.app[ADMIN_ACCESS_KEY]
        api._database_executor.MAX_REPORTS = 0
        with caplog.at_level(logging.INFO, logger='hummingbot.cli.admin_api'):
            response = await http.get(ADMIN_PREFIX + '/status')
        assert response.status == 503
        api.host.store.read.assert_not_called()
        assert api._database_executor.active == api._database_executor.pending == 0
        record = next(r for r in caplog.records if getattr(r, 'event', None) == 'admin_status_read')
        assert record.database_phase == 'rejected' and record.worker_ms is None
