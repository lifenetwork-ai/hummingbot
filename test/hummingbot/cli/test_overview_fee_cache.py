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
                           account_scope=SimpleNamespace(identity_verified=True, account_ids=[account], environment='demo'))


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
        assert kwargs['session_id'] == 'session' and kwargs['account_ids'] == ['17']
        assert kwargs['start_ms'] == 1000000 and kwargs['end_ms'] == 1010000
        called.set()
        await release.wait()
        return result()

    task = asyncio.create_task(cache.read(row(), scope(), query))
    await called.wait()
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
