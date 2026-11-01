import asyncio
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from multidict import MultiDict

from hummingbot.cli.admin_api import AdminAPI, APIUnavailable
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_reporting import day_range


@pytest.mark.asyncio
async def test_cached_market_does_not_wait_for_another_markets_aggregate():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def aggregate(**filters):
        if filters["market"] == "perpetual":
            started.set()
            assert release.wait(5)
        start, end, *_ = day_range(None, None, "UTC")
        return dict(start_day=start, end_day=end, reporting_timezone="UTC",
                    server_time=datetime.now(timezone.utc), coverage="observed_recorder_events",
                    balance_scope="account_last_observed", balances=[], markets=[])

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone="UTC", statistics_cache_ttl_seconds=60), store=storage)
    spot = SimpleNamespace(query=MultiDict(market="spot"))
    warm = await api.daily(spot)
    miss = asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict(market="perpetual"))))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        hit = await asyncio.wait_for(api.daily(spot), timeout=.2)
        assert hit.text == warm.text
        assert storage.daily.call_count == 2
        assert not miss.done()
    finally:
        release.set()
        await miss


@pytest.mark.asyncio
async def test_daily_polling_coalesces_misses_preserves_observation_time_and_expires():
    storage = MagicMock()
    observation = datetime.now(timezone.utc)

    def aggregate(**filters):
        time.sleep(.02)
        start, end, *_ = day_range(None, None, "UTC")
        return dict(start_day=start, end_day=end, reporting_timezone="UTC", server_time=observation,
                    coverage="observed_recorder_events", balance_scope="account_last_observed", balances=[], markets=[])
    storage.daily.side_effect = aggregate
    settings = AdminSettings(reporting_timezone="UTC", statistics_cache_ttl_seconds=.1)
    api = AdminAPI(None, settings, store=storage)
    request = SimpleNamespace(query=MultiDict())
    responses = await asyncio.gather(*(api.daily(request) for _ in range(10)))
    assert storage.daily.call_count == 1
    assert len({response.text for response in responses}) == 1
    assert observation.isoformat().replace("+00:00", "Z") in responses[0].text
    await api.daily(SimpleNamespace(query=MultiDict(market="spot")))
    assert storage.daily.call_count == 2
    await asyncio.sleep(.12)
    await api.daily(request)
    assert storage.daily.call_count == 3


@pytest.mark.asyncio
async def test_failed_aggregate_is_not_cached():
    storage = MagicMock()
    storage.daily.side_effect = ValueError("failure")
    api = AdminAPI(None, AdminSettings(), store=storage)
    for _ in range(2):
        with pytest.raises(ValueError):
            await api.daily(SimpleNamespace(query={}))
    assert storage.daily.call_count == 2


@pytest.mark.asyncio
async def test_zero_ttl_disables_daily_cache():
    storage = MagicMock()
    start, end, *_ = day_range(None, None, "UTC")
    storage.daily.return_value = dict(start_day=start, end_day=end, reporting_timezone="UTC",
                                      server_time=datetime.now(timezone.utc), coverage="observed_recorder_events",
                                      balance_scope="account_last_observed", balances=[], markets=[])
    api = AdminAPI(None, AdminSettings(statistics_cache_ttl_seconds=0), store=storage)
    for _ in range(2):
        await api.daily(SimpleNamespace(query=MultiDict()))
    assert storage.daily.call_count == 2


def daily_snapshot():
    start, end, *_ = day_range(None, None, 'UTC')
    return dict(start_day=start, end_day=end, reporting_timezone='UTC', server_time=datetime.now(timezone.utc),
                coverage='observed_recorder_events', balance_scope='account_last_observed', balances=[], markets=[])


@pytest.mark.asyncio
async def test_independent_uncached_filters_do_not_wait_for_a_slow_aggregate():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def aggregate(**filters):
        if filters['market'] == 'perpetual':
            started.set()
            assert release.wait(5)
        return daily_snapshot()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    slow = asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict(market='perpetual'))))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(api.daily(SimpleNamespace(query=MultiDict(market='spot'))), .5)
        assert not slow.done() and storage.daily.call_count == 2
    finally:
        release.set()
        await asyncio.gather(slow, return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_cancelled_leading_waiter_does_not_duplicate_same_key_physical_work():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def aggregate(**filters):
        started.set()
        assert release.wait(5)
        return daily_snapshot()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    request = SimpleNamespace(query=MultiDict(market='spot'))
    first = asyncio.create_task(api.daily(request))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(api.daily(request))
        await asyncio.sleep(.05)
        release.set()
        await second
        assert storage.daily.call_count == 1
        assert not api._daily_flights
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_daily_key_registry_is_bounded_and_releases_completed_keys():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def aggregate(**filters):
        started.set()
        assert release.wait(5)
        return daily_snapshot()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    tasks = []
    try:
        for n in range(api._database_executor.MAX_REPORTS):
            tasks.append(asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict(pair=f'X{n}-USDT')))))
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(.02)
        with pytest.raises(APIUnavailable):
            await asyncio.wait_for(api.daily(SimpleNamespace(query=MultiDict(pair='OVER-USDT'))), .5)
        assert len(api._daily_flights) == api._database_executor.MAX_REPORTS
        release.set()
        await asyncio.gather(*tasks)
        assert not api._daily_flights
        assert api._database_executor.pending == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_zero_ttl_does_not_share_concurrent_observations():
    storage = MagicMock()
    both_started, release, lock = threading.Event(), threading.Event(), threading.Lock()
    count = 0

    def aggregate(**filters):
        nonlocal count
        with lock:
            count += 1
            if count == 2:
                both_started.set()
        assert release.wait(5)
        return daily_snapshot()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=0), store=storage)
    tasks = [asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict()))) for _ in range(2)]
    try:
        assert await asyncio.to_thread(both_started.wait, 2)
        release.set()
        await asyncio.gather(*tasks)
        assert storage.daily.call_count == 2 and not api._daily_flights
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_shutdown_cleans_shared_tasks_without_abandoning_physical_workers():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def aggregate(**filters):
        started.set()
        assert release.wait(5)
        return daily_snapshot()

    storage.daily.side_effect = aggregate
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    tasks = [asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict(pair=f'X{i}-USDT')))) for i in range(3)]
    close = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        close = asyncio.create_task(api.close(None))
        await asyncio.sleep(.02)
        assert not close.done()
        with pytest.raises(APIUnavailable):
            await api.daily(SimpleNamespace(query=MultiDict(pair='AFTER-USDT')))
        release.set()
        await close
        await asyncio.gather(*tasks, return_exceptions=True)
        assert not api._daily_flights and api._database_executor.pending == 0
        assert api._database_executor.closed
    finally:
        release.set()
        await asyncio.gather(*tasks, *([close] if close else []), return_exceptions=True)
        if close is None:
            await api.close(None)


@pytest.mark.asyncio
async def test_many_completed_filter_keys_leave_only_the_existing_bounded_cache():
    storage = MagicMock()
    storage.daily.side_effect = lambda **filters: daily_snapshot()
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=60), store=storage)
    try:
        for n in range(100):
            await api.daily(SimpleNamespace(query=MultiDict(pair=f'X{n}-USDT')))
        assert not api._daily_flights and len(api._daily_cache.entries) == 32
        assert api._database_executor.pending == 0
    finally:
        await api.close(None)
