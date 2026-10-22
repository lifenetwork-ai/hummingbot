"""PG6 logical restore quarantine on a real primary, never exchange authority."""
import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from uuid import UUID, uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import BackupLimits, capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine, RestoreRequest
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime

LIMITS = BackupLimits(max_tables=512, max_rows=2048, max_row_bytes=2**20, max_total_bytes=16 * 2**20, timeout_ms=10000)


async def isolate(database):
    admin = await asyncpg.connect(database[1].rsplit('/', 1)[0] + '/life_pg2_test')
    name = database[1].rsplit('/', 1)[1]
    await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
    await admin.close()


async def captured(database):
    return await capture_database(database[0], limits=LIMITS)


def request(manifest, **changes):
    return RestoreRequest(operation_id='restore-one', source_lineage=UUID(manifest.lineage),
                          target_lineage=uuid4(), backup_sha256='c' * 64, manifest_sha256=manifest.content_digest, **changes)


@pytest.mark.asyncio
async def test_quarantine_rotates_only_database_identity_preserves_money_and_all_history(database):
    f, cfg, owner = await spot(database)
    before = await f.snapshot(cfg.binding)
    await f.prepare(order(owner), cut=before.cut, owner=owner)
    before = await f.snapshot(cfg.binding)
    await f.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await isolate(database)
    restore = PgRestoreQuarantine(database[0], limits=LIMITS)
    receipt = await restore.quarantine(cmd, manifest=manifest)
    assert receipt.operation_id == cmd.operation_id and not receipt.replayed and not receipt.trading_permission
    assert receipt.target_lineage == cmd.target_lineage
    c = database[0]
    row = await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    assert row['mode'] == 'UNARMED' and row['halted']
    assert all(row[k] is None for k in ('owner_process', 'owner_generation', 'current_session', 'owner_since', 'owner_until'))
    assert row['owner_epoch'] == owner.epoch and row['version'] == before.cut.checkpoint.revision
    assert str(row['database_lineage']) == str(cmd.target_lineage) and str(row['lineage']) == str(cfg.binding.lineage)
    assert await c.fetchval("SELECT reserved FROM life_financial.balances WHERE account_id=$1 AND asset='USDT'", cfg.account_id) == D('2.2')
    after = await captured(database)
    original = {t.name: t for t in manifest.tables}
    current = {t.name: t for t in after.tables}
    for name, table in original.items():
        if name not in ('life_financial.accounts', 'life_storage.storage_metadata', 'life_storage.restore_history'):
            assert current[name] == table, name
    audit = json.loads(await c.fetchval('SELECT before_accounts FROM life_storage.restore_history'))
    assert audit[0]['owner_generation'] == str(owner.generation)
    assert row['risk_epoch'] == audit[0]['risk_epoch'] + 1
    assert not await c.fetchval('SELECT life_financial.can_access($1,true)', cfg.account_id)
    assert (await restore.quarantine(cmd, manifest=manifest)).replayed
    with pytest.raises(StorageError, match='CONFLICT'):
        await restore.quarantine(replace(cmd, backup_sha256='d' * 64), manifest=manifest)
    assert await c.fetchval('SELECT count(*) FROM life_storage.restore_history') == 1
    ancestor = replace(cmd, operation_id='restore-ancestor', source_lineage=cmd.target_lineage,
                       target_lineage=cmd.source_lineage, manifest_sha256=after.content_digest)
    with pytest.raises(StorageError, match='LINEAGE_REUSE'):
        await restore.quarantine(ancestor, manifest=after)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['connections_allowed', 'other_connection', 'changed_money', 'changed_manifest', 'wrong_lineage'])
async def test_restore_refuses_unisolated_or_unproven_target_without_mutating_it(database, fault):
    f, cfg, owner = await spot(database)
    await f.close()
    manifest = await captured(database)
    cmd = request(manifest)
    extra = None
    if fault == 'other_connection':
        extra = await asyncpg.connect(database[2])
    if fault != 'connections_allowed':
        await isolate(database)
    if fault == 'changed_money':
        await database[0].execute("UPDATE life_financial.balances SET total=total+1,version=version+1 WHERE asset='USDT'")
    if fault == 'changed_manifest':
        cmd = replace(cmd, manifest_sha256='d' * 64)
    if fault == 'wrong_lineage':
        cmd = replace(cmd, source_lineage=uuid4())
    try:
        with pytest.raises(StorageError):
            await PgRestoreQuarantine(database[0], limits=LIMITS).quarantine(cmd, manifest=manifest)
        assert await database[0].fetchval('SELECT count(*) FROM life_storage.restore_history') == 0
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'ACTIVE'
        assert str(await database[0].fetchval('SELECT lineage FROM life_storage.storage_metadata')) == manifest.lineage
    finally:
        if extra is not None:
            await extra.close()


@pytest.mark.asyncio
async def test_runtime_and_reporter_cannot_invoke_restore_or_relabel_accounts(database):
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    for role in ('life_pg2_runtime', 'life_pg2_reporter'):
        c = await asyncpg.connect(database[1].replace('postgres@', role + '@'))
        try:
            with pytest.raises(StorageError, match='OWNER_REQUIRED'):
                await PgRestoreQuarantine(c, limits=LIMITS).quarantine(cmd, manifest=manifest)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('UPDATE life_storage.storage_metadata SET lineage=$1', uuid4())
        finally:
            await c.close()


@pytest.mark.asyncio
async def test_restore_marker_blocks_new_writer_even_if_owner_is_manually_relabelled(database):
    runtime, cfg, owner = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await isolate(database)
    await PgRestoreQuarantine(database[0], limits=LIMITS).quarantine(cmd, manifest=manifest)
    # Re-enable the isolated test database only to exercise runtime admission.
    # An operator changing account fields cannot clear the independent barrier.
    admin_dsn = database[1].rsplit('/', 1)[0] + '/life_pg2_test'
    admin = await asyncpg.connect(admin_dsn)
    name = database[1].rsplit('/', 1)[1]
    await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
    await admin.close()
    with pytest.raises(StorageError):
        await PgRuntime.open(cfg)
    with pytest.raises(StorageError, match='STARTUP_UNQUALIFIED'):
        await PgRuntime.open(replace(cfg, database_lineage=cmd.target_lineage))


@pytest.mark.asyncio
async def test_manifest_has_explicit_capacity_and_exact_identity_sets(database):
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    a = await captured(database)
    b = await captured(database)
    assert a == b and a.content_digest == b.content_digest
    assert any(t.name == 'life_financial.operations' and t.rows == 0 for t in a.tables)
    with pytest.raises(StorageError, match='CAPACITY'):
        await capture_database(database[0], limits=replace(LIMITS, max_tables=1))
    with pytest.raises(StorageError, match='CAPACITY'):
        await capture_database(database[0], limits=replace(LIMITS, max_row_bytes=1))


@pytest.mark.asyncio
async def test_lost_commit_ack_reads_original_restore_without_second_epoch_increment(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await isolate(database)
    restorer = PgRestoreQuarantine(database[0], limits=LIMITS)

    async def lost(tx):
        await tx.commit()
        raise ConnectionResetError('synthetic ACK loss')
    monkeypatch.setattr(restorer, '_commit', lost)
    with pytest.raises(CommitUnknown, match='PG_RESTORE_COMMIT_UNKNOWN'):
        await restorer.quarantine(cmd, manifest=manifest)
    cold = PgRestoreQuarantine(database[0], limits=LIMITS)
    assert (await cold.quarantine(cmd, manifest=manifest)).replayed
    assert await database[0].fetchval('SELECT risk_epoch FROM life_financial.accounts') == 1
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.restore_history') == 1


@pytest.mark.asyncio
async def test_cancelled_restore_retains_connection_and_reenables_identity_trigger(database, monkeypatch):
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await isolate(database)
    restore = PgRestoreQuarantine(database[0], limits=LIMITS)
    entered, release = asyncio.Event(), asyncio.Event()
    original = restore._commit

    async def blocked(tx):
        entered.set()
        await release.wait()
        await original(tx)
    monkeypatch.setattr(restore, '_commit', blocked)
    task = asyncio.create_task(restore.quarantine(cmd, manifest=manifest))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await restore.quarantine(cmd, manifest=manifest)
    finally:
        release.set()
        await restore.close()
    assert (await restore.quarantine(cmd, manifest=manifest)).replayed
    assert await database[0].fetchval("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid='life_financial.accounts'::regclass AND tgname='account_identity'") == 'O'
    with pytest.raises(asyncpg.RaiseError):
        await database[0].execute('UPDATE life_financial.accounts SET lineage=$1', uuid4())


@pytest.mark.asyncio
async def test_failed_restore_commit_rolls_back_metadata_money_and_trigger_ddl(database, monkeypatch):
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await isolate(database)
    restore = PgRestoreQuarantine(database[0], limits=LIMITS)

    async def fail(tx):
        raise ConnectionResetError('synthetic precommit failure')
    monkeypatch.setattr(restore, '_commit', fail)
    with pytest.raises(StorageError):
        await restore.quarantine(cmd, manifest=manifest)
    assert await captured(database) == manifest
    assert await database[0].fetchval("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid='life_financial.accounts'::regclass AND tgname='account_identity'") == 'O'


@pytest.mark.asyncio
async def test_killed_process_rolls_back_restore_identity_and_resumes_original_operation(database):
    import os
    import sys
    from dataclasses import asdict
    runtime, cfg, _ = await backend(database)
    await runtime.close()
    manifest = await captured(database)
    cmd = request(manifest)
    await database[0].close()
    child = await asyncio.create_subprocess_exec(sys.executable, '-m',
                                                 'test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_process',
                                                 stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                 env={**os.environ, 'LIFE_PG_RESTORE_TEST_DSN': database[2]})
    admin = restored = None
    try:
        assert await asyncio.wait_for(child.stdout.readline(), 10) == b'CONNECTED\n'
        admin = await asyncpg.connect(database[1].rsplit('/', 1)[0] + '/life_pg2_test')
        name = database[1].rsplit('/', 1)[1]
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        child.stdin.write(json.dumps(dict(image=manifest.json(), request=cmd.json(), limits=asdict(LIMITS))).encode() + b'\n')
        await child.stdin.drain()
        assert await asyncio.wait_for(child.stdout.readline(), 10) == b'BEFORE_COMMIT\n'
        child.kill()
        await child.wait()
        for _ in range(100):
            active = await admin.fetchval('SELECT count(*) FROM pg_stat_activity WHERE datname=$1', name)
            if active == 0:
                break
            await asyncio.sleep(.02)
        assert active == 0
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        restored = await asyncpg.connect(database[2])
        assert await capture_database(restored, limits=LIMITS) == manifest
        assert await restored.fetchval("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid='life_financial.accounts'::regclass AND tgname='account_identity'") == 'O'
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        receipt = await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(cmd, manifest=manifest)
        assert not receipt.replayed and not receipt.trading_permission
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        if restored is not None:
            await restored.close()
        if admin is not None:
            await admin.close()


@pytest.mark.asyncio
async def test_restore_preserves_paid_fees_loss_and_trade_ids_and_detects_replay_corruption(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled, sent
    f, cfg, owner = await sent(database)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    await f.apply_fills(filled(owner, at), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    view = await f.snapshot(cfg.binding)
    assert view.budget.session_execution_loss_usdt == D('.12')
    assert view.balance.total_quote_usdt == D('8.98')
    await f.close()
    image = await captured(database)
    cmd = request(image)
    await isolate(database)
    restore = PgRestoreQuarantine(database[0], limits=LIMITS)
    await restore.quarantine(cmd, manifest=image)
    assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.12')
    assert await database[0].fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('8.98')
    assert await database[0].fetchval('SELECT trade_id FROM life_financial.fills') == 'trade1'
    assert (await restore.quarantine(cmd, manifest=image)).replayed
    await database[0].execute("UPDATE life_financial.balances SET total=total+1,version=version+1 WHERE asset='USDT'")
    with pytest.raises(StorageError, match='PARITY'):
        await restore.quarantine(cmd, manifest=image)
