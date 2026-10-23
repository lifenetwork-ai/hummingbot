"""Native settlement evidence stays byte-exact while member reads are joined."""
import json
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


async def scan_count(c):
    return await c.fetchval('''SELECT COALESCE(sum(seq_scan+idx_scan),0)::bigint FROM pg_stat_xact_user_tables
        WHERE schemaname='life_financial' AND relname IN ('send_attempts','spot_preparations',
            'spot_accounting_progress','spot_terminal_facts','reservations','spot_fill_facts',
            'spot_inventory_members','budget_events','operations')''')


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [131], indirect=True)
async def test_joined_settlement_evidence_keeps_complete_original_bytes_and_corruption_refusals(database, request):
    load = runtime.load_migrations
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 131)):
        values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer=MODEL)
        f, cfg, owner, _, lease, *_ = values
        try:
            await seed_completed(database, f, cfg, owner, lease, 4, {})
        finally:
            await f.close()
    c = database[0]
    await c.execute('CREATE SCHEMA settlement_batch_reference')
    predecessors = []
    for signature in ('spot_inventory_evidence(uuid,jsonb)', 'spot_inventory_settlements_verified(uuid)'):
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + signature)
        name = signature.split('(')[0]
        sha = await c.fetchval("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid=to_regprocedure($1)",
                               'life_financial.' + signature)
        predecessors.append(dict(signature='life_financial.' + signature, sha256=sha))
        definition = definition.replace('FUNCTION life_financial.' + name, 'FUNCTION settlement_batch_reference.' + name, 1)
        if name == 'spot_inventory_settlements_verified':
            definition = definition.replace('life_financial.spot_inventory_evidence(', 'settlement_batch_reference.spot_inventory_evidence(')
        await c.execute(definition)
    request.node.user_properties.append(('settlement_batch_predecessors', json.dumps(predecessors)))
    money = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    await migrate(c, action='up')
    scans = []
    # Counters are backend-local current-transaction counters. Both verifiers
    # consume identical retained rows; the measurement performs no writes.
    async with c.transaction():
        for schema in ('life_financial', 'settlement_batch_reference'):
            start = await scan_count(c)
            assert await c.fetchval(f'SELECT {schema}.spot_inventory_settlements_verified($1)', cfg.account_id)
            scans.append(await scan_count(c) - start)
    request.node.user_properties.append(('settlement_relation_scans', json.dumps(scans)))
    assert scans[0] < scans[1], scans
    members = await c.fetch("SELECT command->'intent_ids' AS members FROM life_financial.spot_inventory_settlements ORDER BY cycle_id")
    for row in members:
        assert await c.fetchval('SELECT life_financial.spot_inventory_evidence($1,$2::jsonb)::text', cfg.account_id, row['members']) == (
            await c.fetchval('SELECT settlement_batch_reference.spot_inventory_evidence($1,$2::jsonb)::text', cfg.account_id, row['members']))
    # Keep original decimal TEXT scale, signed rebates/costs and tied fill
    # ordering. Validation casts must never round the returned actuals.
    for fee, currency, quantity in (('-0.000123456789012345679', 'USDT', '1.0000000000000000001'),
                                    ('0.000123456789012345679', 'USDT', '1'), ('0.0000000000000000000', 'LIFE', '1.00')):
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute('ALTER TABLE life_financial.spot_fill_facts DISABLE TRIGGER USER; '
                            'ALTER TABLE life_financial.spot_accounting_progress DISABLE TRIGGER USER')
            await c.execute('''UPDATE life_financial.spot_fill_facts SET observation=observation||jsonb_build_object(
                'signed_fee',$1::text,'fee_currency',$2::text,'quantity_base_life',$3::text,'fill_at_ms',100)''', fee, currency, quantity)
            # Use a representable quantity for the progress domain while the
            # original high-precision fill case below is expected to fail.
            for row in members:
                results = []
                for schema in ('life_financial', 'settlement_batch_reference'):
                    try:
                        async with c.transaction():
                            result = await c.fetchval(f'SELECT {schema}.spot_inventory_evidence($1,$2::jsonb)::text', cfg.account_id, row['members'])
                        results.append(('value', result))
                    except Exception as error:
                        import asyncpg
                        assert isinstance(error, asyncpg.PostgresError)
                        results.append(('error', error.sqlstate, error.message))
                assert results[0] == results[1], (fee, currency, quantity, results)
        finally:
            await tx.rollback()
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == money
    faults = (
        ('spot_inventory_settlements', "evidence=jsonb_set(evidence,'{proof}',to_jsonb(repeat('f',64)))"),
        ('spot_inventory_members', 'actual_usdt=actual_usdt+1'),
        ('budget_events', "amount=amount+1 WHERE event_id LIKE 'settle-spend:%'"),
        ('operations', "receipt=jsonb_set(receipt,'{proof_digest}',to_jsonb(repeat('f',64))) WHERE command->>'kind'='SPOT_INVENTORY_SETTLE'"),
        ('spot_fill_facts', "observation=jsonb_set(observation,'{signed_fee}','\"-0.01\"')"),
        ('spot_fill_facts', "exchange_order_id='77777'"),
        ('spot_accounting_progress', 'filled_base=filled_base+1'),
        ('reservations', "state='HELD',remaining=amount"),
        ('send_attempts', "state='SEND_UNKNOWN'"),
    )
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE life_financial.{table} SET {change}') != 'UPDATE 0'
            for schema in ('life_financial', 'settlement_batch_reference'):
                assert not await c.fetchval(f'SELECT {schema}.spot_inventory_settlements_verified($1)', cfg.account_id), table
        finally:
            await tx.rollback()
