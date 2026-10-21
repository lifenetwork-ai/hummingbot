"""Primary snapshots reuse only proofs established by that same read."""
import asyncio
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_release import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate

PROOFS = ('spot_actions_verified', 'swap_verified', 'swap_accounting_verified', 'funding_verified', 'spot_capital_verified')


@pytest.mark.asyncio
async def test_primary_read_reuses_base_proofs_without_caching_between_reads(database):
    c = database[0]
    values, *_ = await setup(database)
    financial, cfg = values[:2]
    try:
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
        baseline = json.loads(await c.fetchval("SELECT life_financial.spot_state($1)-'observed_at'", cfg.account_id))
        assert baseline['unverified'] is False
        for name in PROOFS:
            await c.execute('CREATE SEQUENCE life_financial.test_count_' + name + ' MINVALUE 0 START 0')
            definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + name + '(uuid)')
            source = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', 'life_financial.' + name + '(uuid)')
            counter = "PERFORM nextval('life_financial.test_count_" + name + "');"
            if 'LANGUAGE sql' in definition:
                definition = definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql')
                definition = definition.replace(source, 'BEGIN ' + counter + ' RETURN (' + source.strip().rstrip(';') + '); END;')
            else:
                definition = definition.replace('BEGIN', 'BEGIN ' + counter, 1)
            await c.execute(definition)
        current = json.loads(await c.fetchval("SELECT life_financial.spot_state($1)-'observed_at'", cfg.account_id))
        assert current == baseline
        counts = {name: await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_count_' + name)
                  for name in PROOFS}
        assert {name: counts[name] for name in ('spot_actions_verified', 'swap_verified', 'spot_capital_verified')} == {
            'spot_actions_verified': 1, 'swap_verified': 1, 'spot_capital_verified': 1}
        # Nested current-source checks remain independent; the top-level body
        # must remove at least its own duplicate accounting/funding call.
        assert 1 <= counts['funding_verified'] <= 3
        assert 1 <= counts['swap_accounting_verified'] <= 4
        assert json.loads(await c.fetchval("SELECT life_financial.spot_state($1)-'observed_at'", cfg.account_id)) == baseline
        assert {name: await c.fetchval('SELECT last_value+1 FROM life_financial.test_count_' + name)
                for name in PROOFS} == {name: 2 * count for name, count in counts.items()}
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_invalid_carry_preserves_original_portfolio_short_circuit(database):
    c = database[0]
    values, *_ = await setup(database)
    financial, cfg = values[:2]
    try:
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        assert await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"checked_at_ms\":1}'::jsonb WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id) != 'UPDATE 0'
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await c.execute('CREATE SEQUENCE life_financial.test_capital_short_circuit MINVALUE 0 START 0')
        # The base body establishes capital once. A failed carry proof must
        # still return the old denial before evaluating portfolio capital.
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_capital_verified(uuid)'::regprocedure)")
        source = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.spot_capital_verified(uuid)'::regprocedure")
        definition = definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(source,
                                                                                    "BEGIN IF nextval('life_financial.test_capital_short_circuit')>0 THEN RAISE EXCEPTION 'TEST_UNEXPECTED_CAPITAL_RECHECK'; END IF; RETURN (" + source.strip().rstrip(';') + '); END;')
        await c.execute(definition)
        body = json.loads(await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
        assert body['unverified'] and body['carry']['verified'] is False and body['portfolio']['verified'] is False
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [93], indirect=True)
@pytest.mark.parametrize('damage', ['none', 'capital', 'swap_fee', 'receipt'])
async def test_financial_reuse_upgrade_preserves_complete_cut_and_negative_flags(database, monkeypatch, damage):
    c = database[0]
    load = runtime_module.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 93))
        values, *_ = await setup(database)
        financial, cfg = values[:2]
        await financial.close()
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    if damage != 'none':
        table, change, condition = {
            'capital': ('shared_capital_observations', "source_hash=repeat('0',64)", 'true'),
            'swap_fee': ('fees', 'signed_fee=signed_fee-1', "product='SWAP'"),
            'receipt': ('operations', "receipt=receipt||'{\"execution_loss_usdt\":\"999\"}'::jsonb", "command->>'kind'='SWAP_FILL'"),
        }[damage]
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        assert await c.execute('UPDATE life_financial.' + table + ' SET ' + change + ' WHERE account_id=$1 AND ' + condition, cfg.account_id) != 'UPDATE 0'
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
    # Actual predecessor code, with only nested body calls redirected to an
    # independent schema; no oracle shares the new body implementation.
    await c.execute('CREATE SCHEMA snapshot_financial_reference')
    definitions = await c.fetch("SELECT pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='life_financial'::regnamespace AND proname LIKE 'spot_body%'")
    for row in definitions:
        definition = row['definition'].replace('life_financial.spot_body', 'snapshot_financial_reference.spot_body')
        await c.execute(definition)
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure)")
    await c.execute(definition.replace('life_financial.spot_state(', 'snapshot_financial_reference.spot_state(')
                    .replace('life_financial.spot_body(', 'snapshot_financial_reference.spot_body('))
    assert (await migrate(c, action='up'))['applied'] == ['000094_snapshot_financial_proof_reuse.sql', '000095_recorder_verification_reads.sql', '000096_recorder_history_lookups.sql', '000097_atomic_metadata_dispatch.sql', '000098_native_quote_capture.sql', '000099_provider_collection.sql']
    # Both evaluations use genuinely aged facts, avoiding a comparison across
    # a live readiness boundary; all original flags and digest remain compared.
    await asyncio.sleep(1.05)
    row = await c.fetchrow("""SELECT (life_financial.spot_state($1)-'observed_at')::text AS current,
        (snapshot_financial_reference.spot_state($1)-'observed_at')::text AS previous""", cfg.account_id)
    current, previous = json.loads(row['current']), json.loads(row['previous'])
    assert current == previous
    assert current['unverified'] is (damage != 'none')
    assert (await migrate(c, action='up'))['applied'] == []
