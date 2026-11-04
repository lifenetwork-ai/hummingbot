"""A transient read timeout may retry once; stale data never becomes fresh."""
import asyncio
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from multidict import MultiDict
from sqlalchemy.exc import OperationalError

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_reporting import day_range


class CancelledRead(Exception):
    def __init__(self, code='57014', message='canceling statement due to statement timeout'):
        super().__init__('Safe test error')
        self.pgcode = code
        self.diag = SimpleNamespace(message_primary=message)


def timed_out(*, code='57014', message='canceling statement due to statement timeout', invalidated=False):
    return OperationalError(None, None, CancelledRead(code, message), connection_invalidated=invalidated)


def observation():
    start, end, *_ = day_range(None, None, 'UTC')
    return dict(start_day=start, end_day=end, reporting_timezone='UTC', server_time=datetime.now(timezone.utc),
                coverage='observed_recorder_events', balance_scope='account_last_observed', balances=[], markets=[])


@pytest.mark.asyncio
async def test_single_timeout_then_complete_new_observation_is_shared_and_fresh():
    storage = MagicMock()
    good = observation()
    storage.daily.side_effect = [timed_out(), good]
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    try:
        responses = await asyncio.gather(*(api.daily(SimpleNamespace(query=MultiDict())) for _ in range(10)))
        assert storage.daily.call_count == 2
        assert len({response.text for response in responses}) == 1
        body = json.loads(responses[0].text)
        assert body['freshness'] == 'fresh'
        assert body['server_time'] == good['server_time'].isoformat().replace('+00:00', 'Z')
        assert not api._daily_flights and api._database_executor.pending == 0
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_two_timeouts_preserve_stale_timestamp_and_existing_cooldown():
    storage = MagicMock()
    storage.daily.return_value = observation()
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=.01), store=storage)
    request = SimpleNamespace(query=MultiDict())
    try:
        warm = json.loads((await api.daily(request)).text)
        await asyncio.sleep(.02)
        storage.daily.side_effect = [timed_out(), timed_out()]
        stale = json.loads((await api.daily(request)).text)
        assert storage.daily.call_count == 3  # Warmup plus at most two attempts.
        assert stale['freshness'] == 'stale' and stale['server_time'] == warm['server_time']
        for _ in range(5):
            assert json.loads((await api.daily(request)).text)['freshness'] == 'stale'
        assert storage.daily.call_count == 3
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_two_cold_timeouts_do_not_invent_an_observation_or_loop():
    storage = MagicMock()
    storage.daily.side_effect = timed_out()
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    try:
        with pytest.raises(OperationalError):
            await api.daily(SimpleNamespace(query=MultiDict()))
        assert storage.daily.call_count == 2
        assert not api._daily_cache.entries and not api._daily_flights
    finally:
        await api.close(None)


@pytest.mark.parametrize('error', [
    timed_out(message='canceling statement due to user request'),
    timed_out(message=None),
    timed_out(code='40001'),
    timed_out(code='08006'),
    timed_out(invalidated=True),
])
@pytest.mark.asyncio
async def test_retry_excludes_operator_cancel_other_sql_errors_and_discarded_sessions(error):
    storage = MagicMock()
    storage.daily.side_effect = error
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    try:
        with pytest.raises(OperationalError):
            await api.daily(SimpleNamespace(query=MultiDict()))
        assert storage.daily.call_count == 1
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_retry_is_not_admitted_after_reporting_shutdown_starts():
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=MagicMock())

    async def stop_then_fail(*args, **kwargs):
        api._daily_closing = True
        raise timed_out()

    api.run_database = AsyncMock(side_effect=stop_then_fail)
    try:
        with pytest.raises(OperationalError):
            await api._refresh_daily({})
        assert api.run_database.call_count == 1
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_disconnected_waiter_does_not_duplicate_an_active_retry():
    started, release = threading.Event(), threading.Event()
    storage = MagicMock()

    def aggregate(**filters):
        if storage.daily.call_count == 1:
            raise timed_out()
        started.set()
        assert release.wait(5)
        return observation()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    request = SimpleNamespace(query=MultiDict())
    first = asyncio.create_task(api.daily(request))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(api.daily(request))
        await asyncio.sleep(.02)
        assert storage.daily.call_count == 2 and api._database_executor.active == 1
        release.set()
        assert json.loads((await second).text)['freshness'] == 'fresh'
        assert storage.daily.call_count == 2 and api._database_executor.pending == 0
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_control_dispatches_before_retry_with_one_physical_worker(monkeypatch):
    monkeypatch.setattr(AdminDatabaseExecutor, 'WORKERS', 1)
    started, release = threading.Event(), threading.Event()
    calls = []
    storage = MagicMock()

    def aggregate(**filters):
        calls.append('first' if not calls else 'retry')
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
            raise timed_out()
        return observation()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    read = asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict())))
    control = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        control = asyncio.create_task(api.run_control_database(lambda: calls.append('control')))
        await asyncio.sleep(.01)
        release.set()
        await control
        await read
        assert calls == ['first', 'control', 'retry']
        assert api._database_executor.pending == 0
    finally:
        release.set()
        await asyncio.gather(read, *([control] if control else []), return_exceptions=True)
        await api.close(None)
