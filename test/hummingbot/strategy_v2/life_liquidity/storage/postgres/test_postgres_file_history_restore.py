"""Real dump/restore of populated File financial and ancestry import stages."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_file_history import fixture
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS, request
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import PgLogicalBackup, restore_archive
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_history import PgFileHistoryStaging
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['paid', 'segmented'])
async def test_full_import_history_survives_actual_backup_restore_without_adopting_old_target(database, tmp_path, profile):
    raw, source, command = await fixture(database, tmp_path, profile=profile)
    c = database[0]
    writer = PgFileHistoryStaging(c, timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    await writer.translate_history(command)
    original = await writer.history_documents(command)
    assert original['checkpoint'] == json.loads(source.base.core.checkpoint_bytes)
    tables = ('file_imports', 'file_import_chunks', 'file_financial_rows', 'file_financial_seals', 'file_history_rows', 'file_history_seals')
    before = {name: await c.fetch('SELECT * FROM life_storage.' + name + ' ORDER BY 1,2') for name in tables}
    assert all(before.values())
    bundle = await PgLogicalBackup(c, tools=tools(await c.fetchval('SELECT current_database()')), limits=LIMITS).create(tmp_path / 'backup')
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(root + '/life_pg2_test')
    name, restored = 'history_restore_' + uuid4().hex, None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=LIMITS)
        assert await capture_database(restored, limits=LIMITS) == bundle.image
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        result = await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(request(bundle.image), manifest=bundle.image)
        assert not result.trading_permission
        for table in tables:
            assert await restored.fetch('SELECT * FROM life_storage.' + table + ' ORDER BY 1,2') == before[table]
        cold = PgFileHistoryStaging(restored, timeout_ms=10000)
        for method in (cold.history_documents, cold.financial_rows, cold.translate_history):
            with pytest.raises(StorageError, match='DATABASE_SCOPE'):
                await method(command)
        assert await restored.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
        assert await restored.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        # Quarantining the copied database cannot consume or alter the source.
        assert await writer.history_documents(command) == original
    finally:
        if restored is not None:
            await restored.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_discarded_history_stage_keeps_all_original_bytes_and_permanent_tombstones(database, tmp_path):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    c = database[0]
    writer = PgFileHistoryStaging(c, timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    await writer.translate_history(command)
    tables = ('file_import_chunks', 'file_financial_rows', 'file_financial_seals', 'file_history_rows', 'file_history_seals')
    before = {name: await c.fetch('SELECT * FROM life_storage.' + name + ' ORDER BY 1,2') for name in tables}
    assert await writer.discard(command) == 'DISCARDED'
    cold = PgFileHistoryStaging(c, timeout_ms=10000)
    assert await cold.discard(command) == 'DISCARDED'
    for method in (cold.read, cold.financial_rows, cold.history_documents, cold.translate_history):
        with pytest.raises(StorageError, match='DISCARDED'):
            await method(command)
    with pytest.raises(StorageError, match='DISCARDED'):
        await cold.stage(command, raw)
    for table in tables:
        assert await c.fetch('SELECT * FROM life_storage.' + table + ' ORDER BY 1,2') == before[table]
    assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
