"""Same-volume local process crash proof only; no Cloud SQL/HA/PITR claim."""
import asyncio
import os
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations import docker

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance, PgRuntime


async def restart():
    name = os.environ['LIFE_PG_TEST_CONTAINER']
    assert name.startswith('life-pg2-')
    await asyncio.to_thread(docker, 'kill', '--signal=KILL', name)
    await asyncio.to_thread(docker, 'start', name)
    for _ in range(100):
        try:
            await asyncio.to_thread(docker, 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'life_pg2_test')
            return
        except Exception:
            await asyncio.sleep(.1)
    raise AssertionError('disposable DB did not recover')


@pytest.mark.asyncio
async def test_committed_operation_and_holds_survive_sigkill_and_same_volume_restart(database):
    store, cfg, owner = await backend(database)
    original = await store.allocate(AllocateBalance('durable', 0, 'USDT', D('2')), owner)
    await store.close()
    await restart()
    cold = await PgRuntime.open(cfg)
    try:
        assert (await cold.operation('durable')).version == original.version
        assert (await cold.balances())[1].reserved == 2
        assert (await cold.allocate(AllocateBalance('durable', 0, 'USDT', D('2')), owner)).replayed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_unsafe_fsync_configuration_denies_new_runtime(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    store, cfg, owner = await backend(database)
    await store.close()
    admin = await asyncpg.connect(database[1])
    try:
        await admin.execute('ALTER SYSTEM SET fsync=off')
    finally:
        await admin.close()
    await restart()
    with pytest.raises(StorageError, match='STARTUP_UNQUALIFIED'):
        await PgRuntime.open(cfg)
