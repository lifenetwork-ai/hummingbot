"""Restore recovery starts with verified ancestry, never marker removal."""
import asyncio
import json
import sys
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
    LIMITS,
    captured,
    isolate,
    request,
)
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_recovery import PgRestoreRecoveryContext


async def quarantined(database):
    verifier = CredentialExclusionVerifier('offline-credential-provider', b'v' * 32)
    f, cfg, owner, _, lease, *_ = await managed(database, handoff_role=True, configuration_extra={'credential_authority': verifier.policy()})
    await lease.claim('original-process')
    await f.close()
    manifest = await captured(database)
    command = request(manifest)
    await isolate(database)
    await PgRestoreQuarantine(database[0], limits=LIMITS).quarantine(command, manifest=manifest)
    return cfg, owner, manifest, command, verifier


@pytest.mark.asyncio
async def test_recovery_context_cold_read_binds_original_owner_lease_and_target_without_mutation(database):
    cfg, owner, manifest, command, verifier = await quarantined(database)
    before = await captured(database)
    reader = PgRestoreRecoveryContext(database[0], limits=LIMITS)
    result = await reader.challenge(command, manifest=manifest, account_id=cfg.account_id)
    body = result.json()
    assert not result.trading_permission
    assert body['format'] == 'LIFE_RESTORE_RECOVERY_CONTEXT_V1'
    assert body['restore'] == command.json()
    assert body['binding'] == cfg.binding.json()
    assert body['account_id'] == str(cfg.account_id)
    assert body['source_owner'] == owner.json()
    assert body['predecessor']['owner'] == owner.json()
    assert body['predecessor']['lease_epoch'] == 1
    assert body['credential_authority'] == verifier.policy()
    assert body['target_account']['mode'] == 'UNARMED' and body['target_account']['halted']
    assert body['target_account']['database_lineage'] == str(command.target_lineage)
    assert result == await PgRestoreRecoveryContext(database[0], limits=LIMITS).challenge(command, manifest=manifest, account_id=cfg.account_id)
    assert await captured(database) == before
    assert not await database[0].fetchval('SELECT life_financial.can_access($1,true)', cfg.account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['manifest', 'restore', 'account', 'money', 'audit', 'marker'])
async def test_recovery_context_refuses_unverified_or_foreign_target_and_never_unlocks_it(database, fault):
    cfg, owner, manifest, command, _ = await quarantined(database)
    account = cfg.account_id
    c = database[0]
    if fault == 'manifest':
        command = replace(command, manifest_sha256='0' * 64)
    elif fault == 'restore':
        command = replace(command, operation_id='foreign-restore')
    elif fault == 'account':
        account = uuid4()
    elif fault == 'money':
        await c.execute("UPDATE life_financial.balances SET total=total+1,version=version+1 WHERE asset='USDT'")
    elif fault == 'audit':
        await c.execute('ALTER TABLE life_storage.restore_history DISABLE TRIGGER USER')
        try:
            await c.execute("UPDATE life_storage.restore_history SET before_accounts='[]'::jsonb")
        finally:
            await c.execute('ALTER TABLE life_storage.restore_history ENABLE TRIGGER USER')
    else:
        await c.execute('UPDATE life_storage.storage_metadata SET restore_operation_id=NULL')
    before = await captured(database)
    with pytest.raises(StorageError):
        await PgRestoreRecoveryContext(c, limits=LIMITS).challenge(command, manifest=manifest, account_id=account)
    assert await captured(database) == before
    assert await c.fetchval("SELECT mode='UNARMED' AND halted FROM life_financial.accounts WHERE account_id=$1", cfg.account_id)


@pytest.mark.asyncio
async def test_context_reader_requires_schema_owner_before_inspecting_restore_history(database):
    f, cfg, *_ = await managed(database)
    await f.close()
    manifest = await captured(database)
    c = await asyncpg.connect(database[1].replace('postgres@', 'life_pg2_runtime@'))
    try:
        with pytest.raises(StorageError, match='OWNER_REQUIRED'):
            await PgRestoreRecoveryContext(c, limits=LIMITS).challenge(request(manifest), manifest=manifest, account_id=cfg.account_id)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_cancelled_recovery_context_retains_original_physical_slot(database, monkeypatch):
    cfg, _, manifest, command, _ = await quarantined(database)
    reader = PgRestoreRecoveryContext(database[0], limits=LIMITS)
    entered, release = asyncio.Event(), asyncio.Event()
    original = reader._context

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(reader, '_context', blocked)
    task = asyncio.create_task(reader.challenge(command, manifest=manifest, account_id=cfg.account_id))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await reader.challenge(command, manifest=manifest, account_id=cfg.account_id)
    finally:
        release.set()
        await reader.close()
    assert not (await reader.challenge(command, manifest=manifest, account_id=cfg.account_id)).trading_permission


@pytest.mark.asyncio
async def test_native_recovery_preview_produces_a_real_cut_but_rolls_back_all_tentative_state(database):
    cfg, owner, manifest, command, _ = await quarantined(database)
    before = await captured(database)
    reader = PgRestoreRecoveryContext(database[0], limits=LIMITS)
    result = await reader.preview(command, manifest=manifest, account_id=cfg.account_id)
    assert result.cut.checkpoint.binding == cfg.binding
    assert result.cut.checkpoint.revision == result.context.json()['target_account']['version']
    assert result.command.mode.value == 'RECOVERY_ONLY' and result.command.token == owner
    assert not result.trading_permission
    assert await captured(database) == before
    assert await database[0].fetchval("SELECT mode='UNARMED' AND halted FROM life_financial.accounts WHERE account_id=$1", cfg.account_id)
    assert await database[0].fetchval('SELECT restore_operation_id FROM life_storage.storage_metadata') == command.operation_id


@pytest.mark.asyncio
async def test_preview_failure_cannot_leave_a_restored_owner_or_cleared_barrier(database, monkeypatch):
    cfg, _, manifest, command, _ = await quarantined(database)
    reader = PgRestoreRecoveryContext(database[0], limits=LIMITS)
    before = await captured(database)

    async def failed(*args):
        raise StorageError('SYNTHETIC_PREVIEW_READ_FAILURE')
    monkeypatch.setattr(reader, '_native_view', failed)
    with pytest.raises(StorageError, match='SYNTHETIC_PREVIEW_READ_FAILURE'):
        await reader.preview(command, manifest=manifest, account_id=cfg.account_id)
    assert await captured(database) == before


@pytest.mark.asyncio
async def test_killed_native_preview_rolls_back_tentative_owner_and_global_marker(database):
    cfg, _, manifest, command, _ = await quarantined(database)
    before = await captured(database)
    root, name = database[1].rsplit('/', 1)
    admin = await asyncpg.connect(root + '/life_pg2_test')
    process, cold = None, None
    code = '''
import asyncio,json,sys
from uuid import UUID
import asyncpg
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import BackupLimits,DatabaseImage
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import RestoreRequest
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_recovery import PgRestoreRecoveryContext
async def main():
    c=await asyncpg.connect(sys.argv[1])
    print('CONNECTED',flush=True)
    value=json.loads(await asyncio.to_thread(sys.stdin.readline))
    body=value['request']
    for key in ('source_lineage','target_lineage'):body[key]=UUID(body[key])
    reader=PgRestoreRecoveryContext(c,limits=BackupLimits(**value['limits']))
    async def blocked(*args):
        print('TENTATIVE_NATIVE_VIEW',flush=True)
        await asyncio.Future()
    reader._native_view=blocked
    await reader.preview(RestoreRequest(**body),manifest=DatabaseImage.from_json(value['manifest']),account_id=UUID(value['account_id']))
asyncio.run(main())
'''
    try:
        # Only this disposable test database is temporarily opened to transfer
        # the dedicated owner connection to the child. Quarantine still blocks
        # all financial authority. No preview begins before isolation is restored.
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, database[2],
                                                       stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b'CONNECTED\n'
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        await database[0].close()
        value = dict(request=command.json(), manifest=manifest.json(), account_id=str(cfg.account_id), limits=LIMITS.__dict__)
        process.stdin.write(json.dumps(value).encode() + b'\n')
        await process.stdin.drain()
        process.stdin.close()
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b'TENTATIVE_NATIVE_VIEW\n'
        process.kill()
        await asyncio.wait_for(process.wait(), 10)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        cold = await asyncpg.connect(database[2])
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
        assert await capture_database(cold, limits=LIMITS) == before
        assert await cold.fetchval('SELECT restore_operation_id FROM life_storage.storage_metadata') == command.operation_id
        assert await cold.fetchval("SELECT mode='UNARMED' AND halted FROM life_financial.accounts WHERE account_id=$1", cfg.account_id)
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if cold is not None:
            await cold.close()
        await admin.close()
