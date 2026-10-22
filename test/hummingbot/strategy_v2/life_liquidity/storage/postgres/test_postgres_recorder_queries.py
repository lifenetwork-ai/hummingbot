"""Bound duplicate history reads; verify the production lookup plan on retained rows."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event, setup

import pytest


@pytest.mark.asyncio
async def test_event_effects_read_each_previous_order_and_state_once_per_invocation(database):
    c = database[0]
    t, repo, source, at = await setup(database)
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        command = await c.fetchrow("SELECT body FROM life_financial.recorder_outbox WHERE stream LIKE 'event:%'")
        body = json.loads(command['body'])
        args = t.cfg.account_id, json.dumps(body['binding']), json.dumps(body['command']), int(body['version'])
        sql = 'SELECT life_financial.recorder_event_effects($1,$2::jsonb,$3::jsonb,$4)'
        expected = await c.fetchval(sql, *args)
        await c.execute('CREATE SEQUENCE life_financial.test_previous_read MINVALUE 0 START 0')
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.recorder_previous_effect(uuid,text,jsonb,bigint)'::regprocedure)")
        src = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.recorder_previous_effect(uuid,text,jsonb,bigint)'::regprocedure")
        instrumented = "BEGIN PERFORM nextval('life_financial.test_previous_read'); RETURN (" + src.strip().rstrip(';') + '); END;'
        await c.execute(definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(src, instrumented))
        assert await c.fetchval(sql, *args) == expected
        # ORDER, STATUS and STATE each need their original predecessor once.
        # Both subsequent calls must independently read the current snapshot.
        assert await c.fetchval('SELECT last_value+1 FROM life_financial.test_previous_read') == 3
        assert await c.fetchval(sql, *args) == expected
        assert await c.fetchval('SELECT last_value+1 FROM life_financial.test_previous_read') == 6
    finally:
        await t.close()


def plan_nodes(plan):
    yield plan
    for child in plan.get('Plans', ()):
        yield from plan_nodes(child)


@pytest.mark.asyncio
async def test_actual_status_lookup_does_not_scan_unrelated_wire_history(database, record_property):
    c = database[0]
    t, repo, source, at = await setup(database)
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        account = t.cfg.account_id
    finally:
        await t.close()
    # Unqualified synthetic load for the actual lookup/EXPLAIN only. These
    # additional effects are not valid financial/reporting completeness proof.
    await c.execute('''WITH seed AS MATERIALIZED (SELECT * FROM life_financial.recorder_projection_effects
            WHERE account_id=$1 AND table_kind='STATUS' LIMIT 1),
        load AS (SELECT seed.*,jsonb_build_object('order_id','unrelated-'||n,'timestamp',n,'status','OrderCancelled') AS k
            FROM seed CROSS JOIN generate_series(1,8192) n)
        INSERT INTO life_financial.recorder_projection_effects
            (account_id,projection_id,table_kind,key_hash,target_key,previous_projection,before_row,after_row,source_version)
        SELECT account_id,projection_id,table_kind,encode(sha256(convert_to(k::text,'UTF8')),'hex'),k,NULL,NULL,k,source_version FROM load''', account)
    await c.execute('ANALYZE life_financial.recorder_projection_effects')
    src = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.recorder_event_effects(uuid,jsonb,jsonb,bigint)'::regprocedure")
    fragment = src.split('SELECT e.after_row INTO latest', 1)[1].split(';', 1)[0]
    query = 'SELECT e.after_row' + fragment.replace('p_account', '$1').replace("p->>'wire'", '$2').replace('p_version', '$3')
    args = account, 'wire1', 9223372036854775807
    expected = await c.fetchval(query, *args)
    assert json.loads(expected)['order_id'] == 'wire1'
    plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + query, *args))[0]['Plan']
    discarded = sum(node.get('Rows Removed by Filter', 0) * node.get('Actual Loops', 1) for node in plan_nodes(plan))
    record_property('status_unrelated_rows_removed', discarded)
    record_property('status_index_bytes', await c.fetchval("SELECT pg_relation_size('life_financial.recorder_status_by_wire_version'::regclass)"))
    assert discarded <= 8, {'unrelated_rows_scanned': discarded, 'plan': plan}
    assert any(node.get('Index Name') == 'recorder_status_by_wire_version' for node in plan_nodes(plan))
    async with c.transaction():
        await c.execute('SET LOCAL enable_indexscan=off; SET LOCAL enable_bitmapscan=off')
        assert await c.fetchval(query, *args) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [95], indirect=True)
@pytest.mark.parametrize('scenario', ['ordered', 'fill_first', 'funding'])
async def test_previous_read_reuse_preserves_predecessor_effect_bytes_and_rejections(database, monkeypatch, scenario, reference_migrations_100):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    c = database[0]
    load = runtime_module.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 95))
        t, repo, source, at = await setup(database, market='okx_perpetual' if scenario == 'funding' else 'okx')
        try:
            if scenario == 'funding':
                await repo.project(event(t.cfg.binding, source, at, 'FundingPaymentCompleted'), owner=t.owner)
            elif scenario == 'fill_first':
                await repo.project(event(t.cfg.binding, source, at + 1, 'OrderFilled'), owner=t.owner)
                await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
            else:
                await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
                await repo.project(event(t.cfg.binding, source, at + 1, 'OrderFilled'), owner=t.owner)
                await repo.project(event(t.cfg.binding, source, at + 2, 'OrderCancelled'), owner=t.owner)
        finally:
            await t.close()
    await c.execute('CREATE SCHEMA recorder_effect_reference')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.recorder_event_effects(uuid,jsonb,jsonb,bigint)'::regprocedure)")
    await c.execute(definition.replace('FUNCTION life_financial.recorder_event_effects', 'FUNCTION recorder_effect_reference.original'))
    assert (await migrate(c, reference_migrations_100, action='up'))['applied'] == ['000096_recorder_history_lookups.sql', '000097_atomic_metadata_dispatch.sql', '000098_native_quote_capture.sql', '000099_provider_collection.sql', '000100_generated_command_fingerprint.sql']
    rows = await c.fetch("SELECT body FROM life_financial.recorder_outbox WHERE stream LIKE 'event:%' ORDER BY projection_id")
    for row in rows:
        body = json.loads(row['body'])
        args = t.cfg.account_id, json.dumps(body['binding']), json.dumps(body['command']), int(body['version'])
        old_sql = 'SELECT recorder_effect_reference.original($1,$2::jsonb,$3::jsonb,$4)'
        new_sql = 'SELECT life_financial.recorder_event_effects($1,$2::jsonb,$3::jsonb,$4)'
        assert await c.fetchval(new_sql, *args) == await c.fetchval(old_sql, *args)
        invalid = {**body['command'], 'tracking_json': '{"foreign":{}}'}
        errors = []
        for sql in (old_sql, new_sql):
            with pytest.raises(asyncpg.RaiseError) as caught:
                await c.fetchval(sql, args[0], args[1], json.dumps(invalid), args[3])
            errors.append((caught.value.sqlstate, caught.value.message))
        assert errors[0] == errors[1]
    assert (await migrate(c, reference_migrations_100, action='up'))['applied'] == []
