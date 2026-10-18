"""Actual primary backend processes: abort, deadlock, expiry and lost COMMIT replies."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance, PgRuntime

asyncpg = pytest.importorskip('asyncpg')


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('error_kind', ['transport', 'server_shutdown'])
async def test_real_backend_disconnect_at_commit_is_unknown_until_primary_readback(database, monkeypatch, committed, error_kind):
    store, cfg, owner = await backend(database)
    admin = await asyncpg.connect(database[1])
    original = store._commit

    async def disconnected(tx):
        c = tx._connection  # Test-only injection at the named physical boundary.
        pid = c.get_server_pid()
        if committed:
            await original(tx)
        await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        if error_kind == 'server_shutdown':
            raise asyncpg.AdminShutdownError('synthetic lost shutdown reply')
        raise ConnectionError('synthetic lost COMMIT reply')
    monkeypatch.setattr(store, '_commit', disconnected)
    try:
        with pytest.raises(CommitUnknown):
            await store.allocate(AllocateBalance('unknown', 0, 'USDT', D('2')), owner)
        sample = store.diagnostics[-1]
        assert sample.operation == 'allocate_balance' and sample.error_phase == 'commit'
        assert sample.commit_attempted and not sample.commit_confirmed and sample.outcome == 'commit_unknown'
        assert sample.sqlstate == ('57P01' if error_kind == 'server_shutdown' else None)
        assert not store.healthy
        receipt = await store.operation('unknown')
        assert (receipt is not None) is committed
        assert (await store.balances())[1].reserved == (D('2') if committed else D('0'))
    finally:
        await admin.close()
        await store.close()
    cold = await PgRuntime.open(cfg)
    try:
        result = await cold.allocate(AllocateBalance('unknown', 0, 'USDT', D('2')), owner)
        assert result.replayed is committed
        assert (await cold.balances())[1].reserved == 2
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_real_serializable_conflict_retries_original_command_without_repricing(database, monkeypatch):
    store, cfg, owner = await backend(database)
    original_transaction = asyncpg.Connection.transaction
    original_fetch = asyncpg.Connection.fetchval
    attempts = []

    def serializable(c, *args, **kwargs):
        # Fault qualification only; deployed policy remains READ COMMITTED.
        kwargs['isolation'] = 'serializable'
        return original_transaction(c, *args, **kwargs)

    async def concurrent_update(c, query, *args, **kwargs):
        if query.startswith('SELECT life_financial.allocate_balance'):
            attempts.append(await original_fetch(c, 'SHOW transaction_isolation'))
            if len(attempts) == 1:
                # A genuine concurrent committed row version, with identical
                # business values, invalidates this transaction's snapshot.
                await database[0].execute('UPDATE life_financial.accounts SET halted=halted WHERE account_id=$1', cfg.account_id)
        return await original_fetch(c, query, *args, **kwargs)
    monkeypatch.setattr(asyncpg.Connection, 'transaction', serializable)
    monkeypatch.setattr(asyncpg.Connection, 'fetchval', concurrent_update)
    try:
        result = await store.allocate(AllocateBalance('serialized', 0, 'USDT', D('2')), owner)
        sample = store.diagnostics[-1]
        assert sample.outcome == 'success' and sample.sqlstate == '40001'
        assert sample.error_phase == 'execute' and sample.commit_confirmed
        assert result.amount == 2 and attempts == ['serializable', 'serializable']
        assert (await store.balances())[1].reserved == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expiry_at_physical_commit_rolls_back_operation_and_all_holds(database, monkeypatch):
    store, cfg, owner = await backend(database)
    expires = await database[0].fetchval("SELECT clock_timestamp()+interval '1 second'")
    owner = replace(owner, expires_at=expires)
    await database[0].execute('UPDATE life_financial.accounts SET owner_until=$2 WHERE account_id=$1', cfg.account_id, expires)
    original = store._commit

    async def expired(tx):
        await asyncio.sleep(1.1)
        await original(tx)
    monkeypatch.setattr(store, '_commit', expired)
    try:
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await store.allocate(AllocateBalance('expired', 0, 'USDT', D('2')), owner)
        assert await store.operation('expired') is None
        assert (await store.balances())[1].reserved == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_statement_timeout_aborts_and_closes_admission_but_safety_pool_still_works(database):
    store, cfg, owner = await backend(database, statement_timeout_ms=200, transaction_timeout_ms=2000)
    lock = database[0].transaction()
    await lock.start()
    await database[0].execute('SELECT 1 FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
    try:
        with pytest.raises(StorageError):
            await store.allocate(AllocateBalance('timeout', 0, 'USDT', D('2')), owner)
        assert not store.healthy
        assert await store.operation('timeout') is None
        assert (await store.balances())[1].reserved == 0
    finally:
        await lock.rollback()
        await store.close()


@pytest.mark.asyncio
async def test_generation_changed_during_account_lock_wait_is_checked_after_wait(database):
    from uuid import uuid4
    store, cfg, owner = await backend(database)
    tx = database[0].transaction()
    await tx.start()
    await database[0].execute('UPDATE life_financial.accounts SET owner_generation=$2 WHERE account_id=$1', cfg.account_id, uuid4())
    task = asyncio.create_task(store.allocate(AllocateBalance('late', 0, 'USDT', D('2')), owner))
    await asyncio.sleep(.05)
    await tx.commit()
    try:
        with pytest.raises(StorageError, match='OWNER_OR_BINDING_INVALID'):
            await task
        assert await store.operation('late') is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_actual_deadlock_retries_whole_database_command_once_without_duplicate_hold(database, monkeypatch):
    admin = await asyncpg.connect(database[1])
    await admin.execute("ALTER ROLE life_pg2_runtime SET deadlock_timeout='50ms'")
    store, cfg, owner = await backend(database, statement_timeout_ms=4000, transaction_timeout_ms=8000)
    first = asyncio.Event()
    original = store._commit
    attempts = []
    lock = admin.transaction()
    await lock.start()
    await admin.execute("SET LOCAL deadlock_timeout='5s'")
    await admin.execute('SELECT pg_advisory_xact_lock(771123)')

    async def contend(tx):
        attempts.append(1)
        if len(attempts) == 1:
            first.set()
            await tx._connection.execute('SELECT pg_advisory_xact_lock(771123)')
        await original(tx)
    monkeypatch.setattr(store, '_commit', contend)
    task = asyncio.create_task(store.allocate(AllocateBalance('deadlock', 0, 'USDT', D('2')), owner))
    try:
        await asyncio.wait_for(first.wait(), 3)
        await admin.execute('SELECT 1 FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
        await lock.commit()
        assert (await task).amount == 2
        assert len(attempts) == 2
        assert (await store.balances())[1].reserved == 2
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='deadlock'") == 1
    finally:
        if admin.is_in_transaction():
            await lock.rollback()
        await asyncio.gather(task, return_exceptions=True)
        await store.close()
        await admin.execute('ALTER ROLE life_pg2_runtime RESET deadlock_timeout')
        await admin.close()
