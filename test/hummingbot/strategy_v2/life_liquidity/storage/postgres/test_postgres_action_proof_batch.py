"""Whole-action proof differential and join-work checks over native history."""
import json
from datetime import timedelta
from pathlib import Path
from statistics import median
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from time import perf_counter

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_complete_action_batch_keeps_native_state_and_history_refusals(database, request):
    c = database[0]
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, f, cfg, owner, lease, 16, {})
    finally:
        await f.close()
    # The owner-side readback excludes only dynamic observed_at. No account
    # version, clock, command identity or financial row is rewritten.
    await c.execute('UPDATE life_financial.access_bindings SET can_write=false WHERE principal=current_user AND account_id=$1', cfg.account_id)
    before = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    original = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_actions_verified(uuid)'::regprocedure)")
    identity = await c.fetchrow("SELECT oid,proowner,proacl,proconfig,provolatile FROM pg_proc WHERE oid='life_financial.spot_actions_verified(uuid)'::regprocedure")
    await c.execute('CREATE SCHEMA action_proof_reference')
    await c.execute(original.replace('FUNCTION life_financial.spot_actions_verified', 'FUNCTION action_proof_reference.verified', 1))
    await c.execute(Path(__file__).with_name('action_proof_batch.sql').read_text())
    assert await c.fetchrow("SELECT oid,proowner,proacl,proconfig,provolatile FROM pg_proc WHERE oid='life_financial.spot_actions_verified(uuid)'::regprocedure") == identity
    assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == before

    async def result(name):
        try:
            async with c.transaction():
                return ('value', await c.fetchval(f'SELECT {name}($1)', cfg.account_id))
        except asyncpg.PostgresError as error:
            return ('error', error.sqlstate)

    async def compare():
        old = await result('action_proof_reference.verified')
        new = await result('life_financial.spot_actions_verified')
        assert old == new, (old, new)
        return new

    assert await compare() == ('value', True)
    await c.execute('SET jit=off; SET plan_cache_mode=force_generic_plan')
    work, costly_joins = [], []
    for name in ('action_proof_reference.verified', 'life_financial.spot_actions_verified'):
        body = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', name + '(uuid)')
        query_body = body.split('\nEXCEPTION WHEN data_exception', 1)[0]
        query = query_body.split('RETURN (', 1)[1].rsplit(');', 1)[0].replace('p_account', '$1::uuid')
        plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF) ' + query, cfg.account_id))
        nodes = []
        costly = []

        def tuples(node):
            nodes.append({key: node.get(key) for key in ('Node Type', 'Relation Name', 'Actual Rows', 'Actual Loops',
                                                         'Rows Removed by Filter', 'Rows Removed by Join Filter')})
            own = node.get('Actual Loops', 0) * sum(node.get(key, 0) for key in (
                'Actual Rows', 'Rows Removed by Filter', 'Rows Removed by Join Filter'))
            if any(key in node.get('Join Filter', '') for key in ('descriptor', 'to_jsonb', 'receipt', 'request')):
                costly.append(node.get('Actual Loops', 0) * (node.get('Actual Rows', 0) + node.get('Rows Removed by Join Filter', 0)))
            return own + sum(tuples(child) for child in node.get('Plans', ()))
        work.append(tuples(plan[0]['Plan']))
        costly_joins.append(sum(costly))
        request.node.user_properties.append(('action_plan_' + name.split('.')[0], json.dumps(nodes[:128])))
    request.node.user_properties.append(('action_proof_plan_tuple_visits', json.dumps(work)))
    request.node.user_properties.append(('action_proof_json_join_pairs', json.dumps(costly_joins)))
    timings = [[], []]
    for sample in range(7):
        for index in ((0, 1) if sample % 2 == 0 else (1, 0)):
            name = ('action_proof_reference.verified', 'life_financial.spot_actions_verified')[index]
            started = perf_counter()
            assert await c.fetchval(f'SELECT {name}($1)', cfg.account_id)
            timings[index].append((perf_counter() - started) * 1000)
    request.node.user_properties.append(('action_proof_read_median_ms', json.dumps([median(t) for t in timings])))
    faults = (
        ('quote_actions', 'version=version-1'),
        ('spot_action_events', "after_state=jsonb_set(after_state,'{side}','\"SELL\"') WHERE after_state->>'side'='BUY'"),
        ('spot_action_events', "command=jsonb_set(command,'{expected}','\"REJECTED\"')"),
        ('spot_action_descriptors', "descriptor=jsonb_set(descriptor,'{level}','1')"),
        ('spot_action_descriptors', "descriptor=jsonb_set(descriptor,'{level}','null')"),
        ('spot_action_descriptors', "descriptor=jsonb_set(descriptor,'{level}','\"bad\"')"),
        ('operations', "receipt=jsonb_set(receipt,'{states}','[]') WHERE command->>'kind'='SPOT_ACTION_TRANSITION'"),
        ('spot_preparations', 'quote_level=quote_level+1'),
        ('spot_preparations', "request=jsonb_set(request,'{price_usdt}','\"2\"')"),
        ('send_attempts', "side='SELL' WHERE side='BUY'"),
        ('operations', "recorded_at='infinity' WHERE command->>'kind'='SPOT_TERMINAL'"),
        ('reservations', 'remaining=amount,state=\'HELD\''),
    )
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE life_financial.{table} SET {change}') != 'UPDATE 0'
            assert await compare() != ('value', True), table
        finally:
            await tx.rollback()
    tx = c.transaction()
    await tx.start()
    try:
        await c.execute('ALTER TABLE life_financial.spot_action_events DISABLE TRIGGER USER')
        assert await c.execute('''DELETE FROM life_financial.spot_action_events e WHERE action_version=(
            SELECT max(x.action_version) FROM life_financial.spot_action_events x
                WHERE x.account_id=e.account_id AND x.action_id=e.action_id)''') != 'DELETE 0'
        assert await compare() == ('value', False)
    finally:
        await tx.rollback()
    # This tests the intended plan shape, not a latency claim. Materialized
    # key joins can traverse more cheap tuples on a tiny cold corpus; retain
    # that metric above. Only the whole-flow replay qualifies performance.
    assert costly_joins[0] > 0 and costly_joins[1] < costly_joins[0], costly_joins
    for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_actions_before_key_joins(uuid)','EXECUTE')", role)
