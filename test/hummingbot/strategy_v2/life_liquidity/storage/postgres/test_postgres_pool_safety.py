"""Separate bounded pools; cancelled callers never detach physical commands."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance, PgRuntime


@pytest.mark.asyncio
async def test_bounded_pools_retain_backend_plans_across_idle_maker_retirement(database):
    store, cfg, owner = await backend(database, max_size=1, owner_duration=timedelta(seconds=60))

    async def pid(connection):
        return await connection.fetchval('SELECT pg_backend_pid()')
    try:
        before = {safety: await store._admit(pid, safety=safety) for safety in (False, True)}
        assert before[False] != before[True]
        # Actual elapsed idle time crosses the previous fixed 30 s eviction.
        # No mocked timer, source re-dating or extra database load is used.
        await asyncio.sleep(31)
        after = {safety: await store._admit(pid, safety=safety) for safety in (False, True)}
        assert after == before
        assert all(pool.get_size() == 1 for pool in store._pools.values())
        assert (await store.allocate(AllocateBalance('after-idle', 0, 'USDT', D('1')), owner)).amount == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retained_backend_plan_does_not_cache_account_access(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import provision_access
    store, cfg, owner = await backend(database, max_size=1)
    try:
        await store.balances()
        await provision_access(database[0], principal=cfg.principal, account_id=cfg.account_id, write=False)
        with pytest.raises(StorageError):
            await store.allocate(AllocateBalance('revoked-access', 0, 'USDT', D('1')), owner)
        assert not store.healthy
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE account_id=$1 AND asset='USDT'", cfg.account_id) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_killed_idle_backend_is_replaced_before_new_command(database):
    import asyncpg
    store, cfg, owner = await backend(database, max_size=1)
    admin = await asyncpg.connect(database[1])

    async def pid(connection):
        return await connection.fetchval('SELECT pg_backend_pid()')
    try:
        before = await store._admit(pid, safety=False)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', before)
        # Join the driver's idle-socket termination notification, before a new
        # operation exists. This is not retry of any interrupted transaction.
        async with asyncio.timeout(1):
            while store._pools[False].get_size():
                await asyncio.sleep(.001)
        after = await store._admit(pid, safety=False)
        assert after != before
        assert (await store.allocate(AllocateBalance('after-idle-disconnect', 0, 'USDT', D('1')), owner)).amount == 1
        assert store._pools[False].get_size() == 1
    finally:
        await admin.close()
        await store.close()


@pytest.mark.asyncio
async def test_full_command_pool_does_not_starve_safety_reads(database, monkeypatch):
    store, cfg, owner = await backend(database, max_size=1, max_pending=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store._commit

    async def blocked(tx):
        entered.set()
        await release.wait()
        await original(tx)
    monkeypatch.setattr(store, '_commit', blocked)
    task = asyncio.create_task(store.allocate(AllocateBalance('held', 0, 'USDT', D('2')), owner))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await store.allocate(AllocateBalance('rejected', 0, 'USDT', D('1')), owner)
        assert (await asyncio.wait_for(store.balances(), 1))[1].reserved == 0
        assert await store.operation('rejected') is None
        release.set()
        assert (await task).amount == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await store.close()


@pytest.mark.asyncio
async def test_pool_acquisition_timeout_is_bounded_and_does_not_publish_late_health(database, monkeypatch):
    store, cfg, owner = await backend(database, max_size=1, max_pending=2, acquire_timeout_ms=100)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store._commit

    async def blocked(tx):
        entered.set()
        await release.wait()
        await original(tx)
    monkeypatch.setattr(store, '_commit', blocked)
    first = asyncio.create_task(store.allocate(AllocateBalance('first', 0, 'USDT', D('2')), owner))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(StorageError):
            await asyncio.wait_for(store.allocate(AllocateBalance('acquire-timeout', 0, 'USDT', D('1')), owner), 1)
        sample = store.diagnostics[-1]
        assert sample.error_phase == 'acquire' and sample.error_kind == 'timeout' and sample.backend_pid is None
        assert not store.healthy and await store.operation('acquire-timeout') is None
        release.set()
        await first
        assert not store.healthy
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_caller_and_close_retain_physical_capacity_until_completion(database, monkeypatch):
    store, cfg, owner = await backend(database, max_size=1, max_pending=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store._commit

    async def blocked(tx):
        entered.set()
        await release.wait()
        await original(tx)
    monkeypatch.setattr(store, '_commit', blocked)
    task = asyncio.create_task(store.allocate(AllocateBalance('cancelled', 0, 'USDT', D('2')), owner))
    closer = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._counts[False] == 1 and not store.healthy
        assert await store.operation('cancelled') is None  # Not confirmation of rollback.
        closer = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closer.done()
        release.set()
        await closer
        cold = await PgRuntime.open(cfg)
        try:
            assert (await cold.operation('cancelled')).trading_permission is False
            assert (await cold.balances())[1].reserved == 2
        finally:
            await cold.close()
    finally:
        release.set()
        if closer is not None:
            await closer
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'policy', 'config', 'lineage', 'database_lineage', 'principal', 'readonly'])
async def test_primary_account_lineage_role_and_readonly_mismatch_deny_startup(database, fault):
    store, cfg, owner = await backend(database)
    await store.close()
    if fault in ('uid', 'policy', 'config', 'lineage'):
        key = {'uid': 'account_uid', 'policy': 'policy_hash', 'config': 'config_hash', 'lineage': 'lineage'}[fault]
        value = '999' if fault == 'uid' else uuid4() if fault == 'lineage' else 'c' * 64
        cfg = replace(cfg, binding=replace(cfg.binding, **{key: value}))
    elif fault == 'database_lineage':
        cfg = replace(cfg, database_lineage=uuid4())
    elif fault == 'principal':
        cfg = replace(cfg, principal='life_pg2_reporter')
    else:
        import asyncpg
        c = await asyncpg.connect(database[1])
        try:
            await c.execute("ALTER ROLE life_pg2_runtime SET default_transaction_read_only=on")
        finally:
            await c.close()
    try:
        with pytest.raises(StorageError, match='STARTUP_UNQUALIFIED'):
            await PgRuntime.open(cfg)
    finally:
        if fault == 'readonly':
            # Role defaults require role-admin credentials, not the migrator.
            import asyncpg
            c = await asyncpg.connect(database[1])
            try:
                await c.execute('ALTER ROLE life_pg2_runtime RESET default_transaction_read_only')
            finally:
                await c.close()
