import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.mm_quote_observation import collect_quote_observation


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
