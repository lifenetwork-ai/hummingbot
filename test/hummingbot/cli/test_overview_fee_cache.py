"""Independent optional fee reads: bounded cache, retries and cancellation."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.cli.overview_fee_cache import OverviewFeeCache


def scope(account='17'):
    return SimpleNamespace(session_id='session', session_started_at=datetime.fromtimestamp(1000, timezone.utc),
                           account_scope=SimpleNamespace(identity_verified=True, account_ids=[account], environment='demo', site='www'))


def row():
    return dict(bot_id='bot', database_now=1010, owner_uuid='owner', heartbeat_age_s=1)


def result():
    return dict(value=dict(total_usdt='0.123456789012345678', by_market=None, conversion_basis='at_fill',
                           history_complete=False, through_at=1010), observed_at=1010, stale=False,
                unavailable_reason='session_fee_history_incomplete')


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_query_and_results_are_detached():
    cache = OverviewFeeCache()
    called = asyncio.Event()
    release = asyncio.Event()

    async def query(**kwargs):
        called.set()
        assert kwargs['session_id'] == 'session' and kwargs['account_ids'] == ['www:17']
        assert kwargs['start_ms'] == 1000000 and kwargs['end_ms'] == 1010000
        await release.wait()
        return result()

    task = asyncio.create_task(cache.read(row(), scope(), query))
    await asyncio.wait_for(called.wait(), .5)
    second = asyncio.create_task(cache.read(row(), scope(), query))
    release.set()
    first, other = await asyncio.gather(task, second)
    assert first == other
    first['value']['total_usdt'] = 'corrupted'
    assert (await cache.read(row(), scope(), AsyncMock()))['value']['total_usdt'] == '0.123456789012345678'
    await cache.close()


@pytest.mark.asyncio
async def test_transient_and_persistent_failure_are_independent_and_retry_after_short_ttl():
    clock = [0]
    cache = OverviewFeeCache(clock=lambda: clock[0])
    query = AsyncMock(side_effect=[OSError('offline'), OSError('offline'), result()])
    first = await cache.read(row(), scope(), query)
    assert first['value'] is None and first['unavailable_reason'] == 'session_fees_unavailable'
    assert (await cache.read(row(), scope(), query))['value'] is None
    assert query.await_count == 1
    clock[0] = 4
    assert (await cache.read(row(), scope(), query))['value'] is None
    clock[0] = 8
    assert (await cache.read(row(), scope(), query))['value']['history_complete'] is False
    assert query.await_count == 3
    await cache.close()


@pytest.mark.asyncio
async def test_cancelled_client_does_not_cancel_shared_query_or_other_reader():
    cache = OverviewFeeCache()
    running, release = asyncio.Event(), asyncio.Event()

    async def query(**kwargs):
        running.set()
        await release.wait()
        return result()

    first = asyncio.create_task(cache.read(row(), scope(), query))
    await running.wait()
    second = asyncio.create_task(cache.read(row(), scope(), query))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert (await second)['value']['total_usdt'] == '0.123456789012345678'
    await cache.close()


@pytest.mark.asyncio
async def test_account_rotation_is_new_scope_unknown_scope_never_queries():
    cache = OverviewFeeCache()
    query = AsyncMock(return_value=result())
    await cache.read(row(), scope(), query)
    await cache.read(row(), scope('other'), query)
    assert query.await_count == 2
    unknown = scope()
    unknown.account_scope.identity_verified = False
    assert (await cache.read(row(), unknown, query))['value'] is None
    assert query.await_count == 2
    await cache.close()


@pytest.mark.asyncio
async def test_invalid_observation_does_not_escape_and_shutdown_rejects_new_work():
    cache = OverviewFeeCache()
    source = row()
    before = deepcopy(source)
    assert (await cache.read(source, scope(), AsyncMock(return_value={'invented': True})))['value'] is None
    assert source == before
    await cache.close()
    query = AsyncMock(return_value=result())
    assert (await cache.read(source, scope(), query))['value'] is None
    query.assert_not_called()


@pytest.mark.asyncio
async def test_slow_heavy_query_cannot_hold_the_status_response_open():
    cache = OverviewFeeCache()
    cache.READ_WAIT_SECONDS = .001
    release = asyncio.Event()

    async def query(**kwargs):
        await release.wait()
        return result()

    try:
        response = await asyncio.wait_for(cache.read(row(), scope(), query), timeout=.1)
        assert response['value'] is None
        assert response['unavailable_reason'] == 'session_fees_refreshing'
        assert len(cache.flights) == 1
        release.set()
        assert (await cache.read(row(), scope(), query))['value']['total_usdt'] == '0.123456789012345678'
    finally:
        release.set()
        await cache.close()


@pytest.mark.asyncio
async def test_backfill_continues_without_additional_status_polling():
    cache = OverviewFeeCache()
    cache.CATCHUP_SECONDS = .001
    completed = asyncio.Event()
    calls = 0

    async def query(**scope):
        nonlocal calls
        calls += 1
        value = result()
        value['value']['processing_state'] = 'catching_up' if calls == 1 else 'ready'
        value['value']['processed_fill_count'] = calls * 500
        if calls == 2:
            completed.set()
        return value

    try:
        first = await cache.read(row(), scope(), query)
        assert first['value']['processing_state'] == 'catching_up'
        await asyncio.wait_for(completed.wait(), .5)
        final = await cache.read(row(), scope(), query)
        assert final['value']['processing_state'] == 'ready'
        assert final['value']['processed_fill_count'] == 1000
        assert calls == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_usable_partial_total_labelled_stale():
    clock = [0]
    cache = OverviewFeeCache(clock=lambda: clock[0])
    query = AsyncMock(side_effect=[result(), OSError('reporting outage')])
    try:
        await cache.read(row(), scope(), query)
        clock[0] = 16
        failed = await cache.read(row(), scope(), query)
        assert failed['value']['total_usdt'] == '0.123456789012345678'
        assert failed['stale'] is True
        assert failed['unavailable_reason'] == 'session_fees_unavailable'
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_current_scope_is_discovered_without_any_ui_request():
    from test.hummingbot.cli.test_admin_overview import row as overview_row

    cache = OverviewFeeCache()
    queried = asyncio.Event()

    async def query(**args):
        assert args['session_id'] == 'session'
        queried.set()
        return result()

    try:
        cache.watch(AsyncMock(return_value=overview_row()), query, interval=.01)
        await asyncio.wait_for(queried.wait(), .5)
        assert len(cache.cache) <= 1
    finally:
        await cache.close()
    assert not cache.flights and not cache.maintainers and cache.observer is None


@pytest.mark.asyncio
async def test_site_qualified_binding_uses_captured_site_and_never_guesses_unknown_site():
    cache = OverviewFeeCache()
    query = AsyncMock(return_value=result())
    current = scope()
    try:
        await cache.read(row(), current, query)
        assert query.call_args.kwargs['account_ids'] == ['www:17']
        current.account_scope.site = 'my'
        await cache.read(row(), current, query)
        assert query.call_args.kwargs['account_ids'] == ['my:17']
        assert query.await_count == 2
        current.account_scope.site = None
        missing = await cache.read(row(), current, query)
        assert missing['value'] is None
        assert query.await_count == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_cached_observation_freshness_is_checked_against_each_database_clock():
    cache = OverviewFeeCache()
    query = AsyncMock(return_value=result())
    try:
        assert (await cache.read(row(), scope(), query))['stale'] is False
        expired = await cache.read({**row(), 'database_now': 1026}, scope(), query)
        assert expired['value']['total_usdt'] == '0.123456789012345678'
        assert expired['stale'] is True
        assert expired['unavailable_reason'] == 'observation_stale'
        assert query.await_count == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_pending_refresh_retains_last_usable_total_as_stale():
    clock = [0]
    cache = OverviewFeeCache(clock=lambda: clock[0])
    cache.READ_WAIT_SECONDS = .001
    release = asyncio.Event()

    async def blocked(**kwargs):
        await release.wait()
        return result()

    try:
        first = await cache.read(row(), scope(), AsyncMock(return_value=result()))
        clock[0] = 16
        pending = await cache.read(row(), scope(), blocked)
        assert pending['value'] == first['value']
        assert pending['observed_at'] == first['observed_at']
        assert pending['stale'] is True
        assert pending['unavailable_reason'] == 'session_fees_refreshing'
    finally:
        release.set()
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('query_stamp,stale', [(1010.01, False), (1012, True)])
async def test_query_clock_can_follow_row_capture_without_hiding_a_future_clock_error(query_stamp, stale):
    cache = OverviewFeeCache()
    value = result()
    value['observed_at'] = query_stamp
    try:
        observation = await cache.read(row(), scope(), AsyncMock(return_value=value))
        assert observation['stale'] is stale
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_failed_backfill_keeps_partial_total_but_uses_error_backoff():
    clock = [0]
    cache = OverviewFeeCache(clock=lambda: clock[0])
    value = result()
    value['value']['processing_state'] = 'catching_up'
    query = AsyncMock(side_effect=[value, OSError('reporting outage')])
    try:
        await cache.read(row(), scope(), query)
        clock[0] = 1
        failed = await cache.read(row(), scope(), query)
        assert failed['stale'] and failed['value']['processing_state'] == 'catching_up'
        assert next(iter(cache.cache.values()))[0] - clock[0] == 3
        clock[0] = 1.1
        assert (await cache.read(row(), scope(), query))['value'] == failed['value']
        assert query.await_count == 2
    finally:
        await cache.close()
