"""Real SQL cancellation keeps diagnostic cause without changing public refusal."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable


@pytest.mark.asyncio
async def test_server_statement_timeout_retains_sqlstate_phase_and_backend(database):
    store, cfg, owner = await backend(database)

    async def blocked(c):
        # Fault-only smaller server limit avoids racing asyncpg's unchanged
        # 1000-ms command timeout. No production deadline is relaxed.
        await c.execute("SELECT set_config('statement_timeout','50',false)")
        await c.fetchval('SELECT pg_sleep(2)')
    try:
        with pytest.raises(StorageUnavailable, match='PG_DATABASE_UNAVAILABLE'):
            await store._admit(blocked, safety=True, operation='timeout_probe')
        sample = store.diagnostics[-1]
        assert sample.operation == 'timeout_probe' and sample.error_phase == 'execute'
        assert sample.sqlstate == '57014' and sample.error_kind == 'postgres'
        assert type(sample.backend_pid) is int and sample.backend_pid > 0
        assert sample.elapsed_ms >= 50
        assert not sample.commit_attempted and not sample.commit_confirmed and not store.healthy
        assert await store.operation('unwritten') is None
    finally:
        await store.close()
