"""Daily reporting may degrade explicitly; trading/current account reads may not."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from multidict import MultiDict
from sqlalchemy.exc import OperationalError, ProgrammingError

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_reporting import day_range


def snapshot(age=0):
    now = datetime.now(timezone.utc) - timedelta(seconds=age)
    start, end, *_ = day_range(None, None, 'UTC', now)
    return dict(start_day=start, end_day=end, reporting_timezone='UTC', server_time=now,
                coverage='observed_recorder_events', balance_scope='account_last_observed', balances=[], markets=[])


def request(**query):
    return SimpleNamespace(query=MultiDict(query))


@pytest_asyncio.fixture
async def daily_api():
    storage = MagicMock()
    storage.daily.return_value = snapshot()
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=.01), store=storage)
    try:
        yield api, storage
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_transient_failure_returns_explicit_last_good_without_changing_observation(daily_api, caplog):
    api, storage = daily_api
    good = json.loads((await api.daily(request())).text)
    await asyncio.sleep(.02)
    storage.daily.side_effect = TimeoutError('not exposed to operators')
    stale = json.loads((await api.daily(request())).text)
    assert stale['freshness'] == 'stale'
    assert stale['server_time'] == good['server_time']
    assert 0 < stale['observation_age_seconds'] <= 60
    assert stale['retry_after_seconds'] > 0
    assert 'last successful observation' in stale['message']
    assert 'not exposed to operators' not in stale['message'] + caplog.text
    assert any(getattr(row, 'event', None) == 'admin_daily_stale' for row in caplog.records)


@pytest.mark.asyncio
async def test_parallel_failed_refresh_is_coalesced_and_never_relabels_stale_as_fresh(daily_api):
    api, storage = daily_api
    good = json.loads((await api.daily(request())).text)
    await asyncio.sleep(.02)
    storage.daily.side_effect = TimeoutError()
    responses = await asyncio.gather(*(api.daily(request()) for _ in range(10)))
    assert storage.daily.call_count == 2
    for response in responses:
        body = json.loads(response.text)
        assert body['freshness'] == 'stale' and body['server_time'] == good['server_time']


@pytest.mark.asyncio
@pytest.mark.parametrize('filters', [{'market': 'spot'}, {'pair': 'BTC-USDT'}, {'from': '2026-01-01', 'to': '2026-01-02'}])
async def test_last_good_is_never_borrowed_from_other_filters(daily_api, filters):
    api, storage = daily_api
    await api.daily(request())
    await asyncio.sleep(.02)
    storage.daily.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await api.daily(request(**filters))


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    ValueError('bad data'), ProgrammingError('query', {}, Exception()),
    OperationalError('query', {}, SimpleNamespace(pgcode='28P01')),
])
async def test_permanent_failure_is_never_hidden_by_a_successful_snapshot(daily_api, error):
    api, storage = daily_api
    await api.daily(request())
    await asyncio.sleep(.02)
    storage.daily.side_effect = error
    with pytest.raises(type(error)):
        await api.daily(request())


@pytest.mark.asyncio
@pytest.mark.parametrize('age', [61, -10])
async def test_old_or_future_observation_cannot_be_used_as_fallback(daily_api, age):
    api, storage = daily_api
    storage.daily.return_value = snapshot(age)
    await api.daily(request())
    await asyncio.sleep(.02)
    storage.daily.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await api.daily(request())


@pytest.mark.asyncio
async def test_first_read_failure_has_no_fabricated_snapshot(daily_api):
    api, storage = daily_api
    storage.daily.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await api.daily(request())


@pytest.mark.asyncio
async def test_disabled_cache_also_disables_fallback():
    storage = MagicMock()
    storage.daily.return_value = snapshot()
    api = AdminAPI(None, AdminSettings(statistics_cache_ttl_seconds=0), store=storage)
    try:
        await api.daily(request())
        storage.daily.side_effect = TimeoutError()
        with pytest.raises(TimeoutError):
            await api.daily(request())
    finally:
        await api.close(None)


@pytest.mark.asyncio
async def test_cancelled_refresh_does_not_convert_last_good_into_stale(daily_api):
    import threading

    api, storage = daily_api
    await api.daily(request())
    await asyncio.sleep(.02)
    started, release = threading.Event(), threading.Event()

    def blocked(**filters):
        started.set()
        assert release.wait(5)
        return snapshot()

    storage.daily.side_effect = blocked
    task = asyncio.create_task(api.daily(request()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        key = api._daily_cache.key({}, 'UTC')
        assert api._daily_cache.get(key) is None
        assert api._daily_cache.entries[key].failed_monotonic is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def controlled_snapshot(api, storage, instant):
    from hummingbot.cli.admin_daily_cache import DailyStatisticsCache
    clock = SimpleNamespace(wall=instant.timestamp(), monotonic=100)
    api._daily_cache = DailyStatisticsCache(.01, wall_clock=lambda: clock.wall, monotonic_clock=lambda: clock.monotonic)
    start, end, *_ = day_range(None, None, 'UTC', instant)
    storage.daily.return_value = dict(
        start_day=start, end_day=end, reporting_timezone='UTC', server_time=instant,
        coverage='observed_recorder_events', balance_scope='account_last_observed', balances=[], markets=[])
    return clock


@pytest.mark.asyncio
async def test_retry_cooldown_recovers_on_a_new_successful_database_observation(daily_api):
    api, storage = daily_api
    clock = controlled_snapshot(api, storage, datetime.now(timezone.utc))
    old = json.loads((await api.daily(request())).text)
    clock.wall += 1
    clock.monotonic += 1
    storage.daily.side_effect = TimeoutError()
    assert json.loads((await api.daily(request())).text)['freshness'] == 'stale'
    clock.wall += 5
    clock.monotonic += 5
    storage.daily.side_effect = None
    storage.daily.return_value = {**storage.daily.return_value, 'server_time': datetime.fromtimestamp(clock.wall, timezone.utc)}
    body = json.loads((await api.daily(request())).text)
    assert body['freshness'] == 'fresh' and body['server_time'] != old['server_time']
    assert body['retry_after_seconds'] is body['message'] is None
    assert storage.daily.call_count == 3


@pytest.mark.asyncio
async def test_lock_wait_crossing_midnight_cannot_borrow_yesterdays_fallback(daily_api):
    api, storage = daily_api
    clock = controlled_snapshot(api, storage, datetime(2026, 10, 29, 23, 59, 58, tzinfo=timezone.utc))
    await api.daily(request())
    clock.wall += 1
    clock.monotonic += 1
    storage.daily.side_effect = TimeoutError()
    async with api._daily_lock:
        task = asyncio.create_task(api.daily(request()))
        await asyncio.sleep(0)
        clock.wall += 2
        clock.monotonic += 2
    with pytest.raises(TimeoutError):
        await task


@pytest.mark.asyncio
async def test_failure_crossing_midnight_does_not_use_the_previous_day_range(daily_api):
    api, storage = daily_api
    clock = controlled_snapshot(api, storage, datetime(2026, 10, 29, 23, 59, 58, tzinfo=timezone.utc))
    await api.daily(request())
    clock.wall += 1
    clock.monotonic += 1

    def fail(**filters):
        clock.wall += 2
        clock.monotonic += 2
        raise TimeoutError()

    storage.daily.side_effect = fail
    with pytest.raises(TimeoutError):
        await api.daily(request())
