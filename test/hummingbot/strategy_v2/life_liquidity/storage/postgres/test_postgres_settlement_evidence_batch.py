"""Complete retained-cycle parity and source-scan qualification of one replay."""
from datetime import timedelta
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_settlement_history_batch import (
    scan_count,
)

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_complete_cycle_batch_preserves_bytes_scales_refusals_and_eliminates_per_cycle_reads(database, request):
    c = database[0]
    values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, f, cfg, owner, lease, 8, {})
    finally:
        await f.close()
    await c.execute('UPDATE life_financial.access_bindings SET can_write=false WHERE principal=current_user AND account_id=$1', cfg.account_id)
    before = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    await c.execute('CREATE SCHEMA settlement_evidence_reference')
    original = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_inventory_settlements_verified(uuid)'::regprocedure)")
    await c.execute(original.replace('FUNCTION life_financial.spot_inventory_settlements_verified',
                                     'FUNCTION settlement_evidence_reference.verified', 1))
    await c.execute(Path(__file__).with_name('settlement_evidence_batch.sql').read_text())
    assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == before
    sequence = await instrument(c, 'spot_inventory_evidence', 'uuid,jsonb')
    scans, per_cycle = [], []
    async with c.transaction():
        for schema, name in [('life_financial', 'spot_inventory_settlements_verified'), ('settlement_evidence_reference', 'verified')]:
            await calls(c, sequence, reset=True)
            start = await scan_count(c)
            assert await c.fetchval(f'SELECT {schema}.{name}($1)', cfg.account_id)
            scans.append(await scan_count(c) - start)
            per_cycle.append(await calls(c, sequence))
    assert per_cycle == [0, 4], per_cycle
    assert scans[0] < scans[1], scans
    request.node.user_properties.append(('settlement_evidence_source_scans', str(scans)))
    request.node.user_properties.append(('settlement_per_cycle_calls', str(per_cycle)))
    members = await c.fetch('SELECT operation_id,command FROM life_financial.spot_inventory_settlements ORDER BY operation_id')

    async def compare():
        # Compare exact native evidence/error behavior, with no normalization
        # of strings, decimal scale, tied ordering or source timestamps.
        originals = []
        for m in members:
            try:
                async with c.transaction():
                    value = await c.fetchval("SELECT life_financial.spot_inventory_evidence($1,($2::jsonb)->'intent_ids')::text", cfg.account_id, m['command'])
                originals.append((m['operation_id'], value))
            except asyncpg.PostgresError as e:
                originals.append(('error', e.sqlstate, e.message))
        try:
            async with c.transaction():
                values = await c.fetch('SELECT operation_id,evidence::text AS body FROM life_financial.spot_inventory_evidence_history($1) ORDER BY operation_id', cfg.account_id)
            assert originals == [(r['operation_id'], r['body']) for r in values]
        except asyncpg.PostgresError as e:
            assert any(v == ('error', e.sqlstate, e.message) for v in originals), originals
    await compare()
    for fee, currency, quantity in [('0.0000000000000000000', 'USDT', '1.00'),
                                    ('0.000123456789012345679', 'USDT', '1'),
                                    ('-0.000123456789012345679', 'USDT', '1.0000000000000000001'),
                                    ('0.0000000000000000000', 'LIFE', '1.00')]:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute('ALTER TABLE life_financial.spot_fill_facts DISABLE TRIGGER USER')
            await c.execute('''UPDATE life_financial.spot_fill_facts SET observation=observation||jsonb_build_object(
                'signed_fee',$1::text,'fee_currency',$2::text,'quantity_base_life',$3::text,'fill_at_ms',100)''', fee, currency, quantity)
            await compare()
        finally:
            await tx.rollback()
    faults = [
        ('spot_inventory_settlements', "evidence=jsonb_set(evidence,'{proof}',to_jsonb(repeat('f',64)))"),
        ('spot_inventory_members', 'actual_usdt=actual_usdt+1'),
        ('budget_events', "amount=amount+1 WHERE event_id LIKE 'settle-spend:%'"),
        ('operations', "receipt=jsonb_set(receipt,'{proof_digest}',to_jsonb(repeat('f',64))) WHERE command->>'kind'='SPOT_INVENTORY_SETTLE'"),
        ('spot_fill_facts', "observation=jsonb_set(observation,'{signed_fee}','\"-0.01\"')"),
        ('spot_fill_facts', "exchange_order_id='77777'"),
        ('spot_accounting_progress', 'filled_base=filled_base+1'),
        ('reservations', "state='HELD',remaining=amount"),
        ('send_attempts', "state='SEND_UNKNOWN'"),
        ('spot_inventory_settlements', "command=jsonb_set(command,'{intent_ids}','[]')"),
        ('spot_inventory_settlements', "command=jsonb_set(command,'{intent_ids}','[1]')"),
    ]
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE life_financial.{table} SET {change}') != 'UPDATE 0'
            for schema, name in [('life_financial', 'spot_inventory_settlements_verified'), ('settlement_evidence_reference', 'verified')]:
                assert not await c.fetchval(f'SELECT {schema}.{name}($1)', cfg.account_id), (table, schema)
        finally:
            await tx.rollback()
    for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_inventory_evidence_history(uuid)','EXECUTE')", role)
