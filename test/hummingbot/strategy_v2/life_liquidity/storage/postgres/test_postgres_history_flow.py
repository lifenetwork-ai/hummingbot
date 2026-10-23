"""Whole-snapshot integrity equivalence and bounded same-invocation audits."""
import json
import re
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


async def instrument(c, name, signature='uuid'):
    sequence = 'history_calls_' + name
    await c.execute(f'CREATE SEQUENCE life_financial.{sequence} MINVALUE 0 START 0')
    definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', f'life_financial.{name}({signature})')
    if 'LANGUAGE sql' in definition:
        body = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', f'life_financial.{name}({signature})')
        definition = definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(body, 'BEGIN RETURN (' + body.strip().rstrip(';') + '); END;')
    assert 'LANGUAGE plpgsql' in definition
    await c.execute(definition.replace('BEGIN', f"BEGIN PERFORM nextval('life_financial.{sequence}');", 1))
    return sequence


async def calls(c, sequence, *, reset=False):
    if reset:
        return await c.fetchval(f"SELECT setval('life_financial.{sequence}',0,false)")
    return await c.fetchval(f'SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.{sequence}')


async def original_body(c):
    """Capture every original body wrapper before applying the new migration."""
    await c.execute('CREATE SCHEMA history_reference')
    rows = await c.fetch("""SELECT p.oid,pg_get_functiondef(p.oid) AS definition,p.proname
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='life_financial' AND p.proname LIKE 'spot_body%' ORDER BY p.oid""")
    names = {r['proname'] for r in rows}
    for row in rows:
        definition = row['definition']
        for name in sorted(names, key=len, reverse=True):
            definition = re.sub(r'\blife_financial\.' + name + r'\(', 'history_reference.' + name + '(', definition)
        await c.execute(definition)


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [126], indirect=True)
@pytest.mark.parametrize('count', [0, 1, 2], ids=['empty', 'unsettled', 'settled'])
async def test_whole_snapshot_reuses_one_lifecycle_audit_and_keeps_independent_commit(database, request, tmp_path, count):
    c = database[0]
    # A genuine two-sided completed flow, including original recorder streams,
    # birth/preparation, wire/trade IDs and inventory settlement.
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    load = runtime.load_migrations
    from unittest.mock import patch
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 126)):
        values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer=MODEL)
        f, cfg, owner, _, lease, *_ = values
        try:
            await seed_completed(database, f, cfg, owner, lease, count, {})
        finally:
            await f.close()
    await original_body(c)
    definitions = await c.fetch("""SELECT oid::regprocedure::text AS signature,
        pg_get_functiondef(oid) AS definition,encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS sha256
        FROM pg_proc WHERE pronamespace='life_financial'::regnamespace AND proname IN (
        'spot_body_v13','spot_lifecycle_verified','spot_actions_verified','spot_inventory_commit_guard',
        'spot_inventory_projection_guard','spot_body_with_carry_proof','spot_body_v7',
        'spot_accounting_verified','spot_accounting_verified_before_repair')
        OR pronamespace='life_financial'::regnamespace AND (
            proname IN ('recorder_stage_executor','recorder_apply_executor','recorder_config_stage','recorder_config_apply',
                        'recorder_event_stage','recorder_event_apply','recorder_position_stage','recorder_position_apply'))""")
    artifact = tmp_path / 'predecessors.json'
    artifact.write_text(json.dumps([dict(r) for r in definitions]))
    request.node.user_properties.append(('predecessor_definitions', str(artifact)))
    prior_guard = next(r['definition'] for r in definitions if r['signature'] == 'life_financial.spot_inventory_commit_guard(uuid,jsonb)')
    await c.execute(prior_guard.replace('FUNCTION life_financial.spot_inventory_commit_guard', 'FUNCTION history_reference.spot_inventory_commit_guard', 1))
    await migrate(c, action='up')
    sequence = await instrument(c, 'spot_inventory_settlements_verified')
    current = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    current_calls = await calls(c, sequence)
    await calls(c, sequence, reset=True)
    previous = await c.fetchval('SELECT history_reference.spot_body($1)::text', cfg.account_id)
    previous_calls = await calls(c, sequence)
    request.node.user_properties.append(('snapshot_settlement_audits', f'{current_calls}/{previous_calls}'))
    assert current == previous
    assert current_calls == 1 and previous_calls == (2 if count else 1), (current_calls, previous_calls)
    # A later independent call must audit again; no saved permission crosses
    # this SQL statement or a post-write physical COMMIT.
    await calls(c, sequence, reset=True)
    assert await c.fetchval('SELECT life_financial.spot_lifecycle_verified($1)', cfg.account_id)
    assert await calls(c, sequence) == 1
    if count != 2:
        return
    guard_calls = []
    for schema in ('life_financial', 'history_reference'):
        await calls(c, sequence, reset=True)
        await c.execute(f"""SELECT {schema}.spot_inventory_commit_guard($1,
            jsonb_set(command,'{{facts,observed_at}}',to_jsonb(clock_timestamp())))
            FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='SPOT_INVENTORY_SETTLE'""", cfg.account_id)
        guard_calls.append(await calls(c, sequence))
    assert guard_calls == [1, 2]
    recorder_sequence = await instrument(c, 'recorder_executor_only_integrity')
    digest = await c.fetchval("SELECT life_financial.spot_state($1)->>'digest'", cfg.account_id)
    await calls(c, recorder_sequence, reset=True)
    assert await c.fetchval('SELECT life_financial.recorder_cut_verified($1,$2)', cfg.account_id, digest)
    assert await calls(c, recorder_sequence) == 1
    assert not await c.fetchval('SELECT life_financial.recorder_cut_verified($1,$2)', cfg.account_id, 'f' * 64)
    # Corruption is injected only into this disposable source, with the owner
    # explicitly bypassing immutable-history triggers. Full original and new
    # bodies must agree, including all readiness/recovery/money/hash inputs.
    faults = (
        ('spot_inventory_settlements', "evidence=jsonb_set(evidence,'{proof}',to_jsonb(repeat('f',64)))"),
        ('spot_inventory_members', 'actual_usdt=actual_usdt+1'),
        ('budget_events', "amount=amount+1 WHERE event_id LIKE 'settle-spend:%'"),
        ('operations', "receipt=jsonb_set(receipt,'{proof_digest}',to_jsonb(repeat('f',64))) WHERE command->>'kind'='SPOT_INVENTORY_SETTLE'"),
        ('recorder_outbox', "source_hash=repeat('f',64) WHERE stream LIKE 'executor:%'"),
        ('projection_receipts', "applied_hash=repeat('f',64)"),
        ('life_recorder."Executors"', 'cum_fees_quote=cum_fees_quote+1'),
        ('quote_actions', "state='REJECTED'"),
    )
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            qualified = table if table.startswith('life_recorder.') else 'life_financial.' + table
            await c.execute(f'ALTER TABLE {qualified} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE {qualified} SET {change}') != 'UPDATE 0'
            a = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
            b = await c.fetchval('SELECT history_reference.spot_body($1)::text', cfg.account_id)
            assert a == b, table
            assert json.loads(a)['unverified'] is True, table
        finally:
            await tx.rollback()
