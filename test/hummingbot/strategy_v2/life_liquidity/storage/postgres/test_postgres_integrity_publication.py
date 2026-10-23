"""Real native audit and pg_dump/restore produce independent public receipts."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import capital_store
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS, request
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.integrity_receipts import IntegrityMonitor, IntegrityPolicy
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import restore_archive
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.integrity_publication import (
    FileIntegrityProvider,
    PgIntegrityPublisher,
    publish_receipt,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine


def publisher(connection, cfg, *, limits=LIMITS):
    key = Ed25519PrivateKey.generate()
    policy = IntegrityPolicy('offline-maintenance', key.public_key().public_bytes_raw(), cfg.account_id, cfg.database_lineage,
                             cfg.binding, audit_max_age_ms=60000, backup_max_age_ms=600000, restore_max_age_ms=600000)
    return PgIntegrityPublisher(connection, policy=policy, signing_key=key, limits=limits)


@pytest_asyncio.fixture
async def auditor_reader(database):
    reader = await asyncpg.connect(database[1].replace('postgres@', 'life_pg2_reporter@'))
    try:
        yield reader
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_independent_audit_preserves_all_money_history_and_never_grants_authority(database, tmp_path, auditor_reader):
    f, cfg, owner, _, _ = await capital_store(database, flows=())
    await f.prepare(order(owner), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    await f.close()
    audit = publisher(database[0], cfg)
    before = await capture_database(database[0], limits=LIMITS)
    receipt = await audit.audit(reader=auditor_reader)
    assert receipt.body['artifact_digest'] == before.content_digest
    audit.policy.verify(receipt, now=datetime.now(timezone.utc))
    path = tmp_path / 'audit-receipt.json'
    await publish_receipt(path, receipt, policy=audit.policy)
    monitor = IntegrityMonitor(audit.policy, FileIntegrityProvider((path,)), timeout_ms=1000)
    try:
        await monitor.sample()
        assert monitor.status()['audit_health'] == 'VERIFIED'
        assert monitor.status()['backup_health'] == monitor.status()['recovery_health'] == 'UNVERIFIED'
        assert not monitor.status()['trading_permission']
        assert await capture_database(database[0], limits=LIMITS) == before
        with pytest.raises(FileExistsError):
            await publish_receipt(path, receipt, policy=audit.policy)
        path.write_text('{}')
        await monitor.sample()
        assert monitor.status()['audit_health'] == 'UNVERIFIED'
    finally:
        await monitor.close()
        await audit.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wrong_account', 'wrong_lineage', 'capacity', 'money_corruption', 'missing_basis', 'write_reader'])
async def test_missing_scope_capacity_or_native_integrity_cannot_receive_a_success(database, fault, auditor_reader):
    if fault == 'missing_basis':
        f, cfg, _ = await spot(database)
    else:
        f, cfg, _, _, _ = await capital_store(database, flows=())
    await f.close()
    if fault == 'wrong_account':
        cfg = replace(cfg, account_id=uuid4())
    if fault == 'wrong_lineage':
        cfg = replace(cfg, database_lineage=uuid4())
    audit = publisher(database[0], cfg, limits=replace(LIMITS, max_rows=1) if fault == 'capacity' else LIMITS)
    if fault == 'money_corruption':
        await database[0].execute("UPDATE life_financial.balances SET total=total+1,version=version+1 WHERE asset='USDT'")
    if fault == 'write_reader':
        await database[0].execute("UPDATE life_financial.access_bindings SET can_write=true WHERE principal='life_pg2_reporter'")
    with pytest.raises(StorageError):
        await audit.audit(reader=auditor_reader)


@pytest.mark.asyncio
async def test_real_backup_and_restore_receipts_are_distinct_from_rearm(database, tmp_path):
    f, cfg, owner = await spot(database)
    await f.prepare(order(owner), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    await f.close()
    c = database[0]
    audit = publisher(c, cfg)
    source_name = await c.fetchval('SELECT current_database()')
    bundle, backup_receipt = await audit.backup(tools=tools(source_name), destination=tmp_path / 'backup')
    assert backup_receipt.body['artifact_digest'] == bundle.content_digest
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(root + '/life_pg2_test')
    name, restored = 'life_integrity_' + uuid4().hex, None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=LIMITS)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        command = replace(request(bundle.image), backup_sha256=bundle.archive_sha256)
        receipt = await audit.restored(restore=PgRestoreQuarantine(restored, limits=LIMITS), command=command,
                                       bundle=bundle, backup_receipt=backup_receipt)
        audit.policy.verify(receipt, now=datetime.now(timezone.utc))
        assert receipt.body['kind'] == 'RESTORE'
        assert await restored.fetchval("SELECT bool_and(mode='UNARMED' AND halted) FROM life_financial.accounts")
        assert await restored.fetchval('SELECT sum(reserved)>0 FROM life_financial.balances')
        assert not await restored.fetchval('SELECT life_financial.can_access($1,true)', cfg.account_id)
        assert await c.fetchval("SELECT mode FROM life_financial.accounts") == 'ACTIVE'
        with pytest.raises(StorageError, match='SOURCE_INVALID'):
            await audit.restored(restore=PgRestoreQuarantine(restored, limits=LIMITS), command=replace(command, backup_sha256='f' * 64),
                                 bundle=bundle, backup_receipt=backup_receipt)
    finally:
        if restored is not None:
            await restored.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_cancelled_audit_keeps_one_physical_snapshot_until_joined(database, monkeypatch, auditor_reader):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import integrity_publication
    f, cfg, _, _, _ = await capital_store(database, flows=())
    await f.close()
    audit = publisher(database[0], cfg)
    entered, release = asyncio.Event(), asyncio.Event()
    original = integrity_publication.capture_transaction

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(integrity_publication, 'capture_transaction', blocked)
    task = asyncio.create_task(audit.audit(reader=auditor_reader))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(StorageError, match='BACKPRESSURE'):
        await audit.audit(reader=auditor_reader)
    closing = asyncio.create_task(audit.close())
    await asyncio.sleep(0)
    assert not closing.done() and database[0].is_in_transaction()
    release.set()
    await closing
    assert not database[0].is_in_transaction()


@pytest.mark.asyncio
async def test_native_verification_and_image_share_one_exported_snapshot(database, monkeypatch, auditor_reader):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import integrity_publication
    f, cfg, _, _, _ = await capital_store(database, flows=())
    await f.close()
    before = await capture_database(database[0], limits=LIMITS)
    original = integrity_publication.capture_transaction
    changed = False

    async def concurrent_commit(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            other = await asyncpg.connect(database[2])
            try:
                await other.execute("UPDATE life_financial.balances SET total=total+1,version=version+1 WHERE asset='USDT'")
            finally:
                await other.close()
        return await original(*args, **kwargs)
    monkeypatch.setattr(integrity_publication, 'capture_transaction', concurrent_commit)
    audit = publisher(database[0], cfg)
    receipt = await audit.audit(reader=auditor_reader)
    assert changed and receipt.body['artifact_digest'] == before.content_digest
    assert (await capture_database(database[0], limits=LIMITS)).content_digest != before.content_digest
    with pytest.raises(StorageError, match='CAPITAL_BASIS_UNVERIFIED'):
        await audit.audit(reader=auditor_reader)
