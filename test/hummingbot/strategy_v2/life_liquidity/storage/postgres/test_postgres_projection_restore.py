"""Physical tool round trip of a small enrolled candidate; no live cutover."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import projection_contract
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_fill_projection import install, ready
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


async def small_rows(c):
    # Test-only complete small-image parity, explicitly bounded. This helper
    # never supplies a production restore manifest or send authority.
    tables = await c.fetch('''SELECT quote_ident(n.nspname)||'.'||quote_ident(r.relname) AS name
        FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND r.relkind='r' ORDER BY 1''', projection_contract.NAMES)
    result, total = {}, 0
    assert len(tables) <= 512
    for row in tables:
        rows = await c.fetch(f'''SELECT to_jsonb(r)::text AS body FROM {row['name']} r
            ORDER BY to_jsonb(r)::text COLLATE "C" LIMIT 4097''')
        assert len(rows) <= 4096
        values = [r['body'] for r in rows]
        total += sum(len(v.encode()) for v in values)
        assert total <= 32 * 2**20
        result[row['name']] = fingerprint(values)
    sequences = await c.fetch('''SELECT quote_ident(n.nspname)||'.'||quote_ident(r.relname) AS name
        FROM pg_sequence s JOIN pg_class r ON r.oid=s.seqrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname=ANY($1::text[]) ORDER BY 1''', projection_contract.NAMES)
    assert len(sequences) <= 512
    for row in sequences:
        result['sequence:' + row['name']] = fingerprint(dict(await c.fetchrow(f"SELECT last_value,is_called FROM {row['name']}")))
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['spot', 'joint', 'repair'])
async def test_projection_real_dump_restore_keeps_graph_money_catalog_and_internal_refusals(database, tmp_path, request, scenario):
    c = database[0]
    repo = None
    try:
        if scenario == 'spot':
            repo, cfg, owner = await ready(database, economics=True)
            await repo.acknowledge(ack(owner, await c.fetchval('SELECT clock_timestamp()')),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        else:
            await install(c, products=True)
            if scenario == 'joint':
                from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_batch import (
                    test_all_four_financial_families_share_one_cut_and_keep_gross_costs_and_claims,
                )
                await test_all_four_financial_families_share_one_cut_and_keep_gross_costs_and_claims(database)
            else:
                from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_cashflow_repair import (
                    test_signed_cashflow_pending_books_bill_once_without_overwriting_original,
                )
                await test_signed_cashflow_pending_books_bill_once_without_overwriting_original(database, 'USDT', False)
        account = await c.fetchval('SELECT account_id FROM life_financial.accounts')
        # The trusted archive owner needs an explicit read binding too. This
        # grants no write/send capability and is retained in exact row parity.
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', account)
        source_name = await c.fetchval('SELECT current_database()')
        archive = tmp_path / 'candidate.dump'
        async with c.transaction(isolation='repeatable_read', readonly=True):
            expected_catalog = await projection_contract.catalog(c)
            expected_rows = await small_rows(c)
            expected_body = await c.fetchval('SELECT life_financial.spot_body($1)::text', account)
            snapshot = await c.fetchval('SELECT pg_export_snapshot()')
            with archive.open('xb') as output:
                await tools(source_name).dump(snapshot, output, 2**30)
        assert archive.stat().st_size <= 2**30
        # Native restore intentionally refuses an unknown experiment schema.
        # Do not broaden its allowlist merely to make a prototype pass.
        with pytest.raises(StorageError, match='^PG_SCHEMA_OWNER_OR_OBJECT_UNSUPPORTED$'):
            await capture_database(c, limits=LIMITS)
    finally:
        if repo is not None:
            await repo.close()
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(root + '/life_pg2_test')
    name = 'life_projection_restore_' + uuid4().hex
    restored = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        with archive.open('rb') as source:
            await tools(name).restore(source)
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        async with restored.transaction(isolation='repeatable_read', readonly=True):
            await projection_contract.verify(restored, expected_catalog)
            assert await small_rows(restored) == expected_rows
            assert await restored.fetchval('SELECT life_financial.spot_body($1)::text', account) == expected_body
        # Existing migration re-entry cannot install/re-enroll or silently
        # reset the experiment. No new production migration is claimed.
        await migrate(restored, action='up')
        async with restored.transaction(isolation='repeatable_read', readonly=True):
            await projection_contract.verify(restored, expected_catalog)
            assert await small_rows(restored) == expected_rows
        assert await restored.fetchval('SELECT life_financial.spot_accounting_verified($1)', account)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with restored.transaction():
                if scenario == 'repair':
                    await restored.execute('ALTER TABLE life_financial.cashflows DISABLE TRIGGER USER')
                    await restored.execute('UPDATE life_financial.cashflows SET value_usdt=value_usdt+1 WHERE account_id=$1', account)
                else:
                    await restored.execute('ALTER TABLE life_financial.fees DISABLE TRIGGER USER')
                    await restored.execute('UPDATE life_financial.fees SET signed_fee=signed_fee-1 WHERE account_id=$1', account)
        assert await restored.fetchval('SELECT life_financial.spot_accounting_verified($1)', account)
        request.node.user_properties.append(('projection_archive_bytes', str(archive.stat().st_size)))
    finally:
        if restored is not None:
            await restored.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
