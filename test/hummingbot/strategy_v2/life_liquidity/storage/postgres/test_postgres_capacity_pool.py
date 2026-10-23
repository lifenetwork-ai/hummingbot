"""The configured service's existing two-slot safety floor protects heartbeat intake."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize('slots', [1, 2])
async def test_capacity_heartbeat_pool_contention_keeps_the_original_timeout_and_fences_failure(database, slots):
    f, cfg, owner, _, lease, *_ = await managed(database, lease_ms=10000, owner_duration_ms=30000,
                                                spot_options=dict(safety_size=slots))
    c = database[0]
    try:
        await lease.claim('pool-floor-claim')
        before = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
        assert cfg.acquire_timeout_ms == cfg.statement_timeout_ms == 1000
        # Occupy the actual pool holder, not a mocked query or invented lease.
        # No statement/COMMIT deadline is extended to manufacture a pass.
        async with f._runtime._pools[True].acquire():
            if slots == 1:
                with pytest.raises(StorageUnavailable, match='PG_DATABASE_UNAVAILABLE'):
                    await lease.heartbeat('contended-heartbeat')
                assert not lease.ready
                assert await c.fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE operation_id='contended-heartbeat'") == 0
            else:
                receipt = await lease.heartbeat('contended-heartbeat')
                assert not receipt.replayed and not receipt.trading_permission and lease.ready
                assert await c.fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE operation_id='contended-heartbeat'") == 1
        assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == before
    finally:
        await f.close()
