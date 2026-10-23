"""Operational telemetry cannot grant authority or erase physical uncertainty."""
import asyncio
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_diagnostics import runtime, transaction

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError


@pytest.mark.asyncio
async def test_unknown_total_survives_ring_eviction_and_never_means_an_unresolved_balance():
    store, connection = runtime()
    connection.commit_fault = ConnectionError('secret-payload')
    with pytest.raises(CommitUnknown):
        await transaction(store)
    connection.commit_fault = None
    for _ in range(140):
        await store._admit(lambda c: asyncio.sleep(0), safety=True)
    state = store.operational_metrics
    assert state['completed_total'] == 141
    assert state['commit_unknown_total'] == 1
    assert state['outcomes_total']['success'] == 140
    assert state['window_samples'] == 128
    assert state['trading_permission'] is False
    assert 'unresolved_holds' not in state
    assert 'secret-payload' not in json.dumps(state)
    state['outcomes_total']['success'] = -1
    assert store.operational_metrics['outcomes_total']['success'] == 140


@pytest.mark.asyncio
async def test_cancelled_waiter_remains_inflight_until_physical_completion():
    store, _ = runtime()
    entered, release = asyncio.Event(), asyncio.Event()

    async def physical(connection):
        entered.set()
        await release.wait()
    task = asyncio.create_task(store._admit(physical, safety=False))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = store.operational_metrics
    assert state['risk_pending'] == 1 and state['safety_pending'] == 0
    assert state['completed_total'] == state['caller_cancelled_total'] == 0
    release.set()
    await asyncio.gather(*tuple(store._tasks))
    state = store.operational_metrics
    assert state['risk_pending'] == 0 and state['completed_total'] == state['caller_cancelled_total'] == 1
    assert state['risk_pending_limit'] == store.config.max_pending
    assert state['safety_pending_limit'] == store.config.safety_max_pending


@pytest.mark.asyncio
async def test_backpressure_and_unqualified_are_distinct_from_completed_physical_work():
    store, _ = runtime()
    store._counts[False] = store.config.max_pending
    with pytest.raises(StorageError, match='PG_BACKPRESSURE'):
        await transaction(store)
    store._closing = True
    with pytest.raises(StorageError, match='PG_CLOSED_OR_UNQUALIFIED'):
        await transaction(store)
    state = store.operational_metrics
    assert state['completed_total'] == 0
    assert state['outcomes_total']['backpressure'] == state['outcomes_total']['unqualified'] == 1
    assert state['risk_pending'] == store.config.max_pending
    assert state['healthy'] is False


@pytest.mark.asyncio
async def test_export_requires_no_pool_call_and_keeps_only_fixed_metric_keys():
    store, _ = runtime()
    await store._admit(lambda c: asyncio.sleep(0), safety=True, operation='sensitive_arbitrary_operation')
    store._pools.clear()
    state = store.operational_metrics
    assert state['window_samples'] == 1
    assert 0 <= state['elapsed_p95_ms'] <= state['elapsed_max_ms']
    assert state['phase_max_ms']['acquire'] >= 0
    assert all(v >= 0 for v in state['phase_max_ms'].values())
    assert 'sensitive_arbitrary_operation' not in json.dumps(state)
    assert 'private-test-password' not in json.dumps(state)
