"""Actual runner visibility and physical COMMIT loss constrain phase fusion."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capture', 'dispatch'])
@pytest.mark.parametrize('committed', [False, True])
async def test_real_executor_boundary_requires_committed_claim_and_retains_unknown_dispatch(database, monkeypatch, phase, committed):
    t, service = await configured(database)
    admin = await asyncpg.connect(database[1])
    original = PgRuntime._commit
    target = 'spot_capture_quote_action' if phase == 'capture' else 'spot_dispatch_claimed_action'
    seen = []

    async def lost(store, transaction):
        trace = _current_trace.get()
        if store is not service.runtime or trace.operation != target:
            return await original(store, transaction)
        assert not seen, 'Unknown phase COMMIT was retried'
        # Independent connection observes only committed state. The real V2
        # executor already exists at DISPATCH, but has not received acceptance.
        state = await admin.fetchval('SELECT state FROM life_financial.quote_actions WHERE account_id=$1', t.cfg.account_id)
        active = service.runner.executor_orchestrator.active_executors['main']
        assert state == (None if phase == 'capture' else 'PROPOSED')
        assert len(active) == (phase == 'dispatch')
        assert not t.service_orders
        if committed:
            await original(store, transaction)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', trace.backend_pid)
        seen.append(trace.backend_pid)
        raise ConnectionError('synthetic phase COMMIT reply loss')
    monkeypatch.setattr(PgRuntime, '_commit', lost)
    try:
        with pytest.raises(StorageError):
            await service.quote('phase-boundary-original', TradeType.BUY, 0)
        assert len(seen) == 1 and not service.runtime.healthy and service._revoked
        state = await admin.fetchval('SELECT state FROM life_financial.quote_actions WHERE account_id=$1', t.cfg.account_id)
        expected = ('PROPOSED' if committed else None) if phase == 'capture' else ('DISPATCHED' if committed else 'PROPOSED')
        assert state == expected
        assert not t.service_orders
        assert await admin.fetchval('SELECT count(*) FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id) == 0
        assert await admin.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=$1', t.cfg.account_id) == 1
        assert await admin.fetchval('SELECT sum(reserved) FROM life_financial.balances WHERE account_id=$1', t.cfg.account_id) == 0
        if phase == 'dispatch':
            entry = service.journal._entries['phase-boundary-original']
            assert not entry.accepted and entry.revoked
    finally:
        try:
            await service.close()
        except StorageError as error:
            # A reporting projection cannot adopt an executor after its original
            # DISPATCH acceptance became uncertain. Surface that refusal and still
            # close every owned resource; it cannot report clean shutdown.
            assert phase == 'dispatch'
            assert str(error) == 'PG_RECORDER_ORIGIN_INVALID'
        finally:
            await admin.close()
        assert service.runtime._counts == {False: 0, True: 0}
        assert all(pool._closed for pool in service.runtime._pools.values())
