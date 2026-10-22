"""Write-time command digest preserves original bytes and corruption refusal."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def qualified_carry(database):
    values = await setup(database, refresh_initial_facts=True)
    assert (await values[8].check_carry(values[1].binding)).allowed
    return values


@pytest.fixture
def digest_upgrade_directory(tmp_path):
    # This differential gate is specifically 99 -> 100, including the old
    # function catalog and exact snapshot bytes. Later migrations have their
    # own current-schema workflow/upgrade gates, not these historical claims.
    directory = tmp_path / 'command_digest_upgrade'
    directory.mkdir()
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) <= 100:
            (directory / path.name).write_bytes(path.read_bytes())
    return directory


@pytest.mark.asyncio
async def test_command_digest_generated_from_original_commands_without_permission(database):
    values = await qualified_carry(database)
    financial, cfg, owner = values[:3]
    try:
        rows = await database[0].fetch('''SELECT operation_id,fingerprint,canonical_fingerprint,
            encode(sha256(convert_to(life_financial.canonical_json_v1(command),'UTF8')),'hex') AS original
            FROM life_financial.operations WHERE account_id=$1 ORDER BY operation_id''', cfg.account_id)
        assert rows and all(r['canonical_fingerprint'] == r['original'] == r['fingerprint'] for r in rows)
        assert await database[0].fetchval('SELECT life_financial.carry_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=$1', cfg.account_id) == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [99], indirect=True)
async def test_command_digest_upgrade_backfills_original_history_preserves_cut_oids_acls_and_reruns(database, monkeypatch, digest_upgrade_directory):
    c = database[0]
    load = runtime_module.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 99))
        values = await qualified_carry(database)
        financial, cfg, owner = values[:3]
        try:
            before = await financial.snapshot(cfg.binding)
            operations = await c.fetchval('SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1', cfg.account_id)
        finally:
            await financial.close()
    metadata_sql = '''SELECT oid,proacl,prosecdef,provolatile::text,proisstrict,proleakproof,
        proparallel::text,proconfig,prorettype,proargtypes::text FROM pg_proc
        WHERE pronamespace='life_financial'::regnamespace ORDER BY oid'''
    original = await c.fetch(metadata_sql)
    assert (await migrate(c, digest_upgrade_directory, action='up'))['applied'] == ['000100_generated_command_fingerprint.sql']
    monkeypatch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 100))
    current = {row['oid']: row for row in await c.fetch(metadata_sql)}
    assert [current[row['oid']] for row in original] == original
    assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o)-'canonical_fingerprint' ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id) == operations
    assert await c.fetchval("SELECT bool_and(canonical_fingerprint=fingerprint AND canonical_fingerprint=encode(sha256(convert_to(life_financial.canonical_json_v1(command),'UTF8')),'hex')) FROM life_financial.operations WHERE account_id=$1", cfg.account_id)
    cold = PgSpotRepository(await runtime_module.PgRuntime.open(cfg))
    try:
        after = await cold.snapshot(cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert await c.fetchval('SELECT life_financial.carry_verified($1)', cfg.account_id)
    finally:
        await cold.close()
    assert (await migrate(c, digest_upgrade_directory, action='up'))['applied'] == []


@pytest.mark.asyncio
async def test_private_command_digest_matches_original_canonical_bytes_on_nested_values(database):
    # Private hashing equality does not broaden the financial JSON input domain.
    # Compare against the original SQL primitive, including escaping and order.
    values = [None, True, False, 0, -1, 10**40, '', 'quote"\\\n', '\u00e9\u4e2d\U0001f642',
              [], {}, [None, {'b': [1, 2], 'a': {'z': True, 'x': 'text'}}], {'z': 1, 'a': 2}]
    for value in values:
        row = await database[0].fetchrow('''SELECT life_financial.command_fingerprint_v1($1::jsonb) AS current,
            encode(sha256(convert_to(life_financial.canonical_json_v1($1::jsonb),'UTF8')),'hex') AS original''', json.dumps(value))
        assert row['current'] == row['original']


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [99], indirect=True)
@pytest.mark.parametrize('fault', ['body', 'strict', 'definer', 'canonical'])
async def test_command_digest_migration_rejects_verifier_drift_without_partial_schema(database, fault, digest_upgrade_directory):
    c = database[0]
    signature = 'life_financial.carry_check_verified(uuid,text)'
    if fault == 'body':
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace('BEGIN', 'BEGIN -- unexpected body drift\n', 1))
    elif fault == 'strict':
        await c.execute('ALTER FUNCTION ' + signature + ' STRICT')
    elif fault == 'definer':
        await c.execute('ALTER FUNCTION ' + signature + ' SECURITY INVOKER')
    else:
        await c.execute('ALTER FUNCTION life_financial.canonical_json_v1(jsonb) CALLED ON NULL INPUT')
    for _ in range(2):
        with pytest.raises(asyncpg.RaiseError, match='LIFE_COMMAND_HASH_MIGRATION_MISMATCH'):
            await migrate(c, digest_upgrade_directory, action='up')
        assert await c.fetchval('SELECT max(version) FROM life_migrations.schema_migrations') == 99
        assert await c.fetchval("SELECT to_regprocedure('life_financial.command_fingerprint_v1(jsonb)') IS NULL")
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='life_financial.operations'::regclass AND attname='canonical_fingerprint' AND NOT attisdropped)")


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [99], indirect=True)
async def test_command_digest_migration_removes_hostile_default_execute_grants(database, digest_upgrade_directory):
    c = database[0]
    admin = await asyncpg.connect(database[1])
    role = 'life_hash_acl_' + uuid4().hex
    try:
        await admin.execute('CREATE ROLE ' + role + ' NOLOGIN')
        await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO ' + role)
        assert (await migrate(c, digest_upgrade_directory, action='up'))['applied'] == ['000100_generated_command_fingerprint.sql']
        helper = 'life_financial.command_fingerprint_v1(jsonb)'
        assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, helper)
        assert await c.fetchval('''SELECT bool_and(a.grantee=p.proowner) FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=to_regprocedure($1)''', helper)
        assert (await migrate(c, digest_upgrade_directory, action='up'))['applied'] == []
    finally:
        await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial REVOKE EXECUTE ON FUNCTIONS FROM ' + role)
        await c.execute('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM ' + role)
        await admin.execute('DROP ROLE ' + role)
        await admin.close()


@pytest.mark.asyncio
async def test_same_version_command_corruption_recomputes_digest_and_still_denies(database):
    values = await qualified_carry(database)
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        before = await c.fetchrow('''SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset)
            FROM life_financial.balances b WHERE b.account_id=a.account_id) AS money
            FROM life_financial.accounts a WHERE account_id=$1''', cfg.account_id)
        old = await c.fetchrow("SELECT operation_id,fingerprint,canonical_fingerprint FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id)
        assert old is not None
        # Privileged test corruption only. Disabling row-retention triggers must
        # NOT disable PostgreSQL's generated expression or retain an old digest.
        async with c.transaction():
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET command=command||'{\"unexpected_scalar\":1}'::jsonb WHERE account_id=$1 AND operation_id=$2", cfg.account_id, old['operation_id'])
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        after = await c.fetchrow("SELECT fingerprint,canonical_fingerprint,encode(sha256(convert_to(life_financial.canonical_json_v1(command),'UTF8')),'hex') AS original FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2", cfg.account_id, old['operation_id'])
        assert after['canonical_fingerprint'] == after['original'] != old['canonical_fingerprint']
        assert after['fingerprint'] == old['fingerprint']
        assert await c.fetchval('SELECT life_financial.carry_verified($1)', cfg.account_id) is False
        assert await c.fetchrow('''SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset)
            FROM life_financial.balances b WHERE b.account_id=a.account_id) AS money
            FROM life_financial.accounts a WHERE account_id=$1''', cfg.account_id) == before
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_generated_digest_cannot_be_assigned_even_by_owner_and_helper_stays_private(database):
    values = await qualified_carry(database)
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        with pytest.raises(asyncpg.GeneratedAlwaysError):
            async with c.transaction():
                await c.execute("UPDATE life_financial.operations SET canonical_fingerprint=repeat('0',64) WHERE account_id=$1", cfg.account_id)
        helper = 'life_financial.command_fingerprint_v1(jsonb)'
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, helper)
        client = await asyncpg.connect(cfg.dsn)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await client.fetchval("SELECT life_financial.command_fingerprint_v1('{}'::jsonb)")
        finally:
            await client.close()
        assert await c.fetchval('SELECT life_financial.carry_verified($1)', cfg.account_id)
    finally:
        await financial.close()
