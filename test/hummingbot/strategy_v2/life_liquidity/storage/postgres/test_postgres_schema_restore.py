"""Full application DDL/ACL and sequence guards on offline restored images."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.schema_image import capture_schema, verify_schema


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['function', 'constraint', 'index', 'rls', 'acl', 'column', 'default_acl', 'type_acl', 'internal_trigger', 'public_acl'])
async def test_schema_image_catches_non_row_drift(database, change):
    c = database[0]
    async with c.transaction(isolation='repeatable_read', readonly=True):
        image = await capture_schema(c, limits=LIMITS)
    statements = {
        'function': "CREATE OR REPLACE FUNCTION life_financial.can_access(p_account uuid,p_write boolean DEFAULT false) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT true'",
        'constraint': 'ALTER TABLE life_financial.accounts DROP CONSTRAINT accounts_owner_epoch_check',
        'index': 'DROP INDEX life_financial.active_send_attempts',
        'rls': 'ALTER TABLE life_financial.balances DISABLE ROW LEVEL SECURITY',
        'acl': 'GRANT SELECT ON life_financial.accounts TO PUBLIC',
        'default_acl': 'ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT SELECT ON TABLES TO PUBLIC',
        'type_acl': 'REVOKE USAGE ON TYPE life_financial.amount FROM PUBLIC',
        'internal_trigger': 'ALTER TABLE life_financial.spot_policies DISABLE TRIGGER ALL',
        'public_acl': 'GRANT CREATE ON SCHEMA public TO PUBLIC',
        'column': 'ALTER TABLE life_financial.accounts ADD COLUMN unregistered text',
    }
    if change == 'internal_trigger':
        import asyncpg
        admin = await asyncpg.connect(database[1])
        try:
            await admin.execute(statements[change])
        finally:
            await admin.close()
    else:
        await c.execute(statements[change])
    async with c.transaction(isolation='repeatable_read', readonly=True):
        actual = await capture_schema(c, limits=LIMITS)
    with pytest.raises(StorageError, match='SCHEMA_PARITY'):
        verify_schema(image, actual)


@pytest.mark.asyncio
async def test_sequence_image_accepts_consumed_gaps_but_not_rewind(database):
    c = database[0]
    async with c.transaction(isolation='repeatable_read', readonly=True):
        image = await capture_schema(c, limits=LIMITS)
    assert image.sequences
    sequence = image.sequences[0]
    # Gaps are permitted by PostgreSQL; sequence values are not MVCC snapshots.
    await c.fetchval('SELECT nextval($1::regclass)', sequence.name)
    async with c.transaction(isolation='repeatable_read', readonly=True):
        advanced = await capture_schema(c, limits=LIMITS)
    verify_schema(image, advanced)
    await c.fetchval('SELECT setval($1::regclass,$2,false)', sequence.name, sequence.last_value)
    async with c.transaction(isolation='repeatable_read', readonly=True):
        rewound = await capture_schema(c, limits=LIMITS)
    with pytest.raises(StorageError, match='SEQUENCE_REWIND'):
        verify_schema(advanced, rewound)


@pytest.mark.asyncio
async def test_schema_manifest_is_stable_and_explicitly_bounded(database):
    c = database[0]
    async with c.transaction(isolation='repeatable_read', readonly=True):
        first = await capture_schema(c, limits=LIMITS)
        second = await capture_schema(c, limits=LIMITS)
    assert first == second
    assert first.content_digest == second.content_digest
    with pytest.raises(StorageError, match='CAPACITY'):
        async with c.transaction(isolation='repeatable_read', readonly=True):
            await capture_schema(c, limits=replace(LIMITS, max_schema_objects=1))


@pytest.mark.asyncio
@pytest.mark.parametrize('ddl', [
    'CREATE SCHEMA foreign_schema',
    'CREATE TYPE life_financial.unsupported AS (value text)',
    'CREATE TABLE public.unregistered(value text)',
])
async def test_schema_capture_refuses_unmodelled_objects(database, ddl):
    c = database[0]
    await c.execute(ddl)
    with pytest.raises(StorageError, match='UNSUPPORTED'):
        async with c.transaction(isolation='repeatable_read', readonly=True):
            await capture_schema(c, limits=LIMITS)


@pytest.mark.asyncio
async def test_restore_denies_unchanged_rows_with_changed_security_function(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import spot
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import isolate, request

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
    c = database[0]
    f, cfg, owner = await spot(database)
    await f.close()
    manifest = await capture_database(c, limits=LIMITS)
    await c.execute("CREATE OR REPLACE FUNCTION life_financial.can_access(p_account uuid,p_write boolean DEFAULT false) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT true'")
    await isolate(database)
    with pytest.raises(StorageError, match='SCHEMA_PARITY'):
        await PgRestoreQuarantine(c, limits=LIMITS).quarantine(request(manifest), manifest=manifest)
    assert str(await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')) == manifest.lineage
    assert await c.fetchval('SELECT count(*) FROM life_storage.restore_history') == 0


@pytest.mark.asyncio
async def test_sequence_capture_refuses_lost_ownership(database):
    c = database[0]
    await c.execute('ALTER SEQUENCE life_recorder."Controllers_controller_id_seq" OWNED BY NONE')
    with pytest.raises(StorageError, match='SEQUENCE_PROFILE_UNSUPPORTED'):
        async with c.transaction(isolation='repeatable_read', readonly=True):
            await capture_schema(c, limits=LIMITS)
