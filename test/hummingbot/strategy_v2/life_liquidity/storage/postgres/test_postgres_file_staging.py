"""Resumable transport of complete stopped File artifacts, never activation."""
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import LIMITS, setup
from uuid import UUID, uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_staging import FileStageRequest, PgFileStaging
from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import pack_stopped_source


async def fixture(database, tmp_path, *, profile='empty'):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_target_import import paid_history
    from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
    service, _, target, _ = await setup(tmp_path, joint=profile == 'segmented', before_stage=paid_history if profile == 'paid' else None)
    try:
        if profile == 'segmented':
            await service.store._call(service.joint._recovery.capital.journal.migrate_archive, capacity())
        source = await service.control.stopped_postgres_source(**LIMITS)
    finally:
        await service.close()
    raw = pack_stopped_source(source, **LIMITS)
    lineage = UUID(str(await database[0].fetchval('SELECT lineage FROM life_storage.storage_metadata')))
    command = FileStageRequest('file-cut', uuid4(), lineage, target, hashlib.sha256(raw).hexdigest(),
                               LIMITS['max_bytes'], LIMITS['max_rows'], 4096)
    return raw, source, command


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['empty', 'paid', 'segmented'])
async def test_staging_cold_resume_has_exact_bytes_and_no_financial_authority(database, tmp_path, monkeypatch, profile):
    raw, source, command = await fixture(database, tmp_path, profile=profile)
    writer = PgFileStaging(database[0], timeout_ms=10000)
    original = writer._write_chunk
    calls = 0

    async def interrupted(*args):
        nonlocal calls
        await original(*args)
        calls += 1
        if calls == 2:
            raise OSError('synthetic process boundary after durable chunk')
    monkeypatch.setattr(writer, '_write_chunk', interrupted)
    with pytest.raises(OSError):
        await writer.stage(command, raw)
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_import_chunks') == 2
    cold = PgFileStaging(database[0], timeout_ms=10000)
    result = await cold.stage(command, raw)
    assert result.state == 'SEALED' and not result.trading_permission
    assert await cold.read(command) == source
    if profile == 'paid':
        resources = json.loads(source.base.core.checkpoint_bytes)['state']['resources']
        assert 'trade1' in resources['fills'] and 'nav1' in resources['nav_operations']
        assert json.loads(dict(source.base.core.journal_images)['reservations'])['usdt_balance'] == '8.98'
    if profile == 'segmented':
        assert source.archives
    assert (await cold.stage(command, raw)).replayed
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
    assert await cold.discard(command) == 'DISCARDED'
    assert await cold.discard(command) == 'DISCARDED'
    with pytest.raises(StorageError, match='DISCARDED'):
        await cold.stage(command, raw)
    with pytest.raises(StorageError, match='DISCARDED'):
        await cold.read(command)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['digest', 'scope', 'lineage', 'identity', 'limits'])
async def test_staging_rejects_changed_origin_and_target(database, tmp_path, fault):
    raw, source, command = await fixture(database, tmp_path)
    writer = PgFileStaging(database[0], timeout_ms=10000)
    if fault == 'digest':
        raw += b' '
    elif fault == 'scope':
        command = replace(command, target_binding=replace(command.target_binding, account_uid='999999999'))
    elif fault == 'lineage':
        command = replace(command, database_lineage=uuid4())
    elif fault == 'identity':
        await writer.stage(command, raw)
        command = replace(command, operation_id='different-operation')
    else:
        command = replace(command, max_bytes=1024)
    with pytest.raises(StorageError):
        await writer.stage(command, raw)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_staging_lost_seal_reply_replays_original_bytes(database, tmp_path, monkeypatch, committed):
    raw, _, command = await fixture(database, tmp_path)
    writer = PgFileStaging(database[0], timeout_ms=10000)
    original = writer._commit

    async def lost(tx):
        if committed:
            await original(tx)
        raise OSError('lost seal reply')
    monkeypatch.setattr(writer, '_commit', lost)
    with pytest.raises(CommitUnknown):
        await writer.stage(command, raw)
    receipt = await PgFileStaging(database[0], timeout_ms=10000).stage(command, raw)
    assert receipt.replayed is committed and not receipt.trading_permission


@pytest.mark.asyncio
async def test_cancelled_staging_retains_one_physical_slot(database, tmp_path, monkeypatch):
    raw, _, command = await fixture(database, tmp_path)
    writer = PgFileStaging(database[0], timeout_ms=10000)
    entered, release = asyncio.Event(), asyncio.Event()
    original = writer._write_chunk

    async def blocked(*args):
        entered.set()
        await release.wait()
        await original(*args)
    monkeypatch.setattr(writer, '_write_chunk', blocked)
    task = asyncio.create_task(writer.stage(command, raw))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await writer.stage(command, raw)
    finally:
        release.set()
        await writer.close()
    assert (await writer.stage(command, raw)).replayed


@pytest.mark.asyncio
async def test_killed_importer_resumes_from_durable_chunks_in_a_fresh_process(database, tmp_path):
    raw, source, command = await fixture(database, tmp_path, profile='paid')
    path = tmp_path / 'stopped-source.json'
    path.write_bytes(raw)
    code = '''
import asyncio,json,os,sys
from pathlib import Path
from uuid import UUID
import asyncpg
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_staging import FileStageRequest,PgFileStaging
async def main():
    body=json.loads(sys.argv[2])
    for key in ('account_id','database_lineage'): body[key]=UUID(body[key])
    body['target_binding']=StateBinding.from_json(body['target_binding'])
    c=await asyncpg.connect(sys.argv[1])
    writer=PgFileStaging(c,timeout_ms=10000)
    original=writer._write_chunk
    async def die(command,sequence,raw):
        await original(command,sequence,raw)
        if sequence==1: os._exit(91)
    writer._write_chunk=die
    await writer.stage(FileStageRequest(**body),Path(sys.argv[3]).read_bytes())
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, database[2], json.dumps(command.json()), str(path),
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 91, (output, error)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_import_chunks') == 2
    cold = PgFileStaging(database[0], timeout_ms=10000)
    assert not (await cold.stage(command, raw)).trading_permission
    assert await cold.read(command) == source


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['chunk', 'missing', 'metadata'])
async def test_sealed_staging_detects_corruption_without_adopting_it(database, tmp_path, fault):
    import asyncpg
    raw, _, command = await fixture(database, tmp_path)
    c = database[0]
    writer = PgFileStaging(c, timeout_ms=10000)
    await writer.stage(command, raw)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match='IMMUTABLE_HISTORY'):
        await c.execute('DELETE FROM life_storage.file_import_chunks')
    if fault == 'metadata':
        with pytest.raises(asyncpg.RaiseError):
            await c.execute('UPDATE life_storage.file_imports SET artifact_bytes=artifact_bytes+1')
        return
    await c.execute('ALTER TABLE life_storage.file_import_chunks DISABLE TRIGGER immutable_chunk')
    try:
        if fault == 'missing':
            await c.execute('DELETE FROM life_storage.file_import_chunks WHERE sequence=0')
        else:
            await c.execute("UPDATE life_storage.file_import_chunks SET data=decode('00','hex'),sha256=encode(sha256(decode('00','hex')),'hex') WHERE sequence=0")
    finally:
        await c.execute('ALTER TABLE life_storage.file_import_chunks ENABLE TRIGGER immutable_chunk')
    with pytest.raises(StorageError):
        await writer.read(command)
    with pytest.raises(StorageError):
        await writer.stage(command, raw)
    assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0


@pytest.mark.asyncio
async def test_decode_deadline_keeps_original_worker_until_thread_finishes(database, tmp_path, monkeypatch):
    from threading import Event
    raw, _, command = await fixture(database, tmp_path)
    writer = PgFileStaging(database[0], timeout_ms=500)
    original = writer._decode
    release, entered = Event(), asyncio.Event()
    loop = asyncio.get_running_loop()

    def blocked(*args):
        loop.call_soon_threadsafe(entered.set)
        release.wait(5)
        return original(*args)
    monkeypatch.setattr(writer, '_decode', blocked)
    task = asyncio.create_task(writer.stage(command, raw))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(.7)
        assert not task.done()
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await writer.stage(command, raw)
    finally:
        release.set()
        with pytest.raises(TimeoutError):
            await task
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_imports') == 0


@pytest.mark.asyncio
async def test_restored_staging_preserves_original_bytes_but_cannot_cross_database_lineage(database, tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
        LIMITS as BACKUP_LIMITS,
        request,
    )

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import PgLogicalBackup, restore_archive
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    c = database[0]
    await PgFileStaging(c, timeout_ms=10000).stage(command, raw)
    chunks = await c.fetch('SELECT * FROM life_storage.file_import_chunks ORDER BY sequence')
    bundle = await PgLogicalBackup(c, tools=tools(await c.fetchval('SELECT current_database()')), limits=BACKUP_LIMITS).create(tmp_path / 'backup')
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(root + '/life_pg2_test')
    name = 'staging_restore_' + uuid4().hex
    restored = None
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
    try:
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=BACKUP_LIMITS)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        await PgRestoreQuarantine(restored, limits=BACKUP_LIMITS).quarantine(request(bundle.image), manifest=bundle.image)
        assert await restored.fetch('SELECT * FROM life_storage.file_import_chunks ORDER BY sequence') == chunks
        cold = PgFileStaging(restored, timeout_ms=10000)
        with pytest.raises(StorageError, match='DATABASE_SCOPE'):
            await cold.stage(command, raw)
        with pytest.raises(StorageError, match='DATABASE_SCOPE'):
            await cold.read(command)
        assert await restored.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    finally:
        if restored is not None:
            await restored.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
