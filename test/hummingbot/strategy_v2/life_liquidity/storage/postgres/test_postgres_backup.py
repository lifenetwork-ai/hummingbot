"""Actual pg_dump/pg_restore process round trips; fake exchange, real database."""
import asyncio
import json
import os
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
    LIMITS,
    request as restore_request,
)
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import (
    BackupBundle,
    PgArchiveTools,
    PgLogicalBackup,
    restore_archive,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine


def tools(database_name):
    container = os.environ['LIFE_PG_TEST_CONTAINER']
    return PgArchiveTools(dump_command=('docker', 'exec', '-i', container, 'pg_dump', '-U', 'life_pg2_migrator', '-d', database_name),
                          restore_command=('docker', 'exec', '-i', container, 'pg_restore', '-U', 'life_pg2_migrator', '-d', database_name),
                          timeout_ms=30000)


@pytest.mark.asyncio
async def test_real_dump_restore_preserves_all_rows_then_quarantines_before_runtime_connect(database, tmp_path, request):
    f, cfg, owner = await spot(database)
    await f.prepare(order(owner), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    c = database[0]
    source_name = await c.fetchval('SELECT current_database()')
    original_image = await capture_database(c, limits=LIMITS)
    transport = tools(source_name)
    original_dump = transport.dump

    async def commit_after_export(*args):
        await f.prepare(order(owner, operation_id='operation2', intent_id='intent2', client_order_id='wire2', quote_level=1),
                        cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        await original_dump(*args)
    transport.dump = commit_after_export
    started = perf_counter()
    try:
        bundle = await PgLogicalBackup(c, tools=transport, limits=LIMITS).create(tmp_path / 'backup')
    finally:
        await f.close()
    backup_ms = (perf_counter() - started) * 1000
    assert bundle.image == original_image
    assert bundle.image != await capture_database(c, limits=LIMITS)
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(root + '/life_pg2_test')
    name = 'life_restore_' + uuid4().hex
    restored = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        started = perf_counter()
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=LIMITS)
        restore_ms = (perf_counter() - started) * 1000
        assert await capture_database(restored, limits=LIMITS) == bundle.image
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        started = perf_counter()
        receipt = await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(restore_request(bundle.image), manifest=bundle.image)
        quarantine_ms = (perf_counter() - started) * 1000
        assert not receipt.trading_permission
        assert await restored.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 2
        assert await restored.fetchval('SELECT sum(reserved) FROM life_financial.balances') > 0
        # Source DB and source authority were never changed by target quarantine.
        assert str(await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')) == bundle.image.lineage
        assert await c.fetchval("SELECT mode FROM life_financial.accounts") == 'ACTIVE'
        request.node.user_properties.append(('life_pg6_logical_rehearsal', json.dumps(dict(
            backup_ms=round(backup_ms, 3), restore_ms=round(restore_ms, 3), quarantine_ms=round(quarantine_ms, 3),
            archive_bytes=bundle.archive_bytes, tables=len(bundle.image.tables), rows=sum(t.rows for t in bundle.image.tables),
            profile='local PostgreSQL16 / 2 CPU / 1 GiB / synthetic small fixture', rpo_rto_qualified=False))))
    finally:
        if restored is not None:
            await restored.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_backup_publication_rejects_corruption_overwrite_and_symlink(database, tmp_path):
    c = database[0]
    source_name = await c.fetchval('SELECT current_database()')
    writer = PgLogicalBackup(c, tools=tools(source_name), limits=LIMITS)
    bundle = await writer.create(tmp_path / 'backup')
    assert await bundle.verify(limits=LIMITS) == bundle.image
    with pytest.raises(StorageError, match='EXISTS'):
        await writer.create(tmp_path / 'backup')
    with bundle.archive_path.open('ab') as handle:
        handle.write(b'corruption')
    with pytest.raises(StorageError, match='DIGEST'):
        await bundle.verify(limits=LIMITS)
    bundle.archive_path.unlink()
    original = tmp_path / 'foreign'
    original.write_bytes(b'not an archive')
    bundle.archive_path.symlink_to(original)
    with pytest.raises(StorageError):
        await bundle.verify(limits=LIMITS)
    with pytest.raises(StorageError):
        await BackupBundle.open(bundle.directory, expected_digest='a' * 64, limits=LIMITS)


@pytest.mark.asyncio
async def test_cancelled_backup_retains_exported_snapshot_and_physical_slot(database, tmp_path, monkeypatch):
    c = database[0]
    name = await c.fetchval('SELECT current_database()')
    transport = tools(name)
    original = transport.dump
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        await release.wait()
        await original(*args)
    monkeypatch.setattr(transport, 'dump', blocked)
    writer = PgLogicalBackup(c, tools=transport, limits=LIMITS)
    task = asyncio.create_task(writer.create(tmp_path / 'backup'))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert c.is_in_transaction()
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await writer.create(tmp_path / 'other')
    finally:
        release.set()
        await writer.close()
    assert not c.is_in_transaction()
    bundle = await BackupBundle.open(tmp_path / 'backup', expected_digest=writer.bundle.content_digest, limits=LIMITS)
    assert bundle.content_digest == writer.bundle.content_digest


@pytest.mark.asyncio
async def test_nonempty_restore_target_refused_before_any_transport(database, tmp_path, monkeypatch):
    c = database[0]
    name = await c.fetchval('SELECT current_database()')
    transport = tools(name)
    bundle = await PgLogicalBackup(c, tools=transport, limits=LIMITS).create(tmp_path / 'backup')

    async def forbidden(*args):
        pytest.fail('restoring over existing financial namespace')
    monkeypatch.setattr(transport, 'restore', forbidden)
    with pytest.raises(StorageError):
        await restore_archive(c, tools=transport, bundle=bundle, limits=LIMITS)


@pytest.mark.asyncio
async def test_dump_capacity_is_enforced_during_streaming_without_publishing_manifest(database, tmp_path):
    c = database[0]
    name = await c.fetchval('SELECT current_database()')
    # Explicit synthetic bound: table rows fit, custom archive/schema does not.
    limits = replace(LIMITS, max_total_bytes=262144, max_row_bytes=65536)
    writer = PgLogicalBackup(c, tools=tools(name), limits=limits)
    folder = tmp_path / 'too-small'
    with pytest.raises(StorageError, match='CAPACITY'):
        await writer.create(folder)
    assert (folder / 'database.dump').stat().st_size <= limits.max_total_bytes
    assert not (folder / 'manifest.json').exists()
    assert not c.is_in_transaction()


@pytest.mark.asyncio
async def test_transport_failure_does_not_disclose_raw_child_stderr(tmp_path):
    import sys
    secret = 'raw-credential-not-for-output'
    tool = PgArchiveTools(dump_command=(sys.executable, '-c', "import sys;sys.stderr.write('raw-credential-not-for-output');sys.exit(1)"),
                          restore_command=(sys.executable,), timeout_ms=1000)
    with (tmp_path / 'partial').open('wb') as target:
        with pytest.raises(StorageError, match='PG_ARCHIVE_TOOL_FAILED') as raised:
            await tool.dump('synthetic-snapshot', target, 1024)
    assert secret not in str(raised.value) and secret not in repr(tool)


@pytest.mark.asyncio
async def test_cancelled_transport_joins_original_disk_write_before_releasing_descriptor(tmp_path):
    import sys
    import threading
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / 'slow-disk'
    with path.open('wb') as destination:
        class SlowDisk:
            def write(self, chunk):
                entered.set()
                if not release.wait(5):
                    raise AssertionError('disk fixture not released')
                return destination.write(chunk)
        tool = PgArchiveTools(dump_command=(sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'PGDMPfixture')"),
                              restore_command=(sys.executable,), timeout_ms=1000)
        task = asyncio.create_task(tool.dump('snapshot-fixture', SlowDisk(), 1024))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not destination.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        destination.flush()
        assert path.read_bytes() == b'PGDMPfixture'
