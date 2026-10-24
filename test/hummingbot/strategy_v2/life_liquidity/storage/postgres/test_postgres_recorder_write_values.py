"""Write-derived recorder values must preserve independent history verification."""
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from time import perf_counter

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

SQL = Path(__file__).with_name('recorder_write_values.sql')


@asynccontextmanager
async def rolled_back(c):
    tx = c.transaction()
    await tx.start()
    try:
        yield
    finally:
        await tx.rollback()


async def populated(database, count=8):
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, f, cfg, owner, lease, count, {})
    finally:
        await f.close()
    c = database[0]
    await c.execute('UPDATE life_financial.access_bindings SET can_write=false WHERE principal=current_user AND account_id=$1', cfg.account_id)
    return c, cfg


@pytest.mark.asyncio
async def test_recorder_write_values_keep_native_cut_and_all_historical_refusals(database, request):
    c, cfg = await populated(database)
    # No permission, time, account version or monetary data is changed by installation.
    before = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    identity_query = "SELECT oid,proowner,proacl,proconfig,provolatile FROM pg_proc WHERE oid='life_financial.recorder_executor_only_integrity(uuid)'::regprocedure"
    identity = await c.fetchrow(identity_query)
    original = await c.fetchval("SELECT pg_get_functiondef('life_financial.recorder_executor_only_integrity(uuid)'::regprocedure)")
    await c.execute('CREATE SCHEMA recorder_write_reference')
    await c.execute(original.replace('FUNCTION life_financial.recorder_executor_only_integrity', 'FUNCTION recorder_write_reference.verified', 1))
    await c.execute(SQL.read_text())
    assert await c.fetchrow(identity_query) == identity
    assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == before
    assert await c.fetchval('''SELECT bool_and(executor_row_value::text=life_financial.recorder_executor_row(
        (body#>>'{command,payload_json}')::jsonb)::text AND calculated_source_hash=source_hash)
        FROM life_financial.recorder_outbox WHERE stream LIKE 'executor:%' ''')

    async def compare(expected):
        for name in ('recorder_write_reference.verified', 'life_financial.recorder_executor_only_integrity'):
            assert await c.fetchval(f'SELECT {name}($1)', cfg.account_id) is expected, name

    await compare(True)
    timings = [[], []]
    for sample in range(7):
        for index in ((0, 1) if sample % 2 == 0 else (1, 0)):
            name = ('recorder_write_reference.verified', 'life_financial.recorder_executor_only_integrity')[index]
            started = perf_counter()
            assert await c.fetchval(f'SELECT {name}($1)', cfg.account_id)
            timings[index].append((perf_counter() - started) * 1000)
    request.node.user_properties.append(('recorder_write_read_median_ms', json.dumps([median(t) for t in timings])))
    faults = (
        ('recorder_outbox', "source_hash=repeat('f',64) WHERE stream LIKE 'executor:%'"),
        ('recorder_outbox', "sequence=sequence+20 WHERE sequence=2 AND stream LIKE 'executor:%'"),
        ('recorder_outbox', "body=jsonb_set(body,'{command,expected_sequence}','99') WHERE sequence=2 AND stream LIKE 'executor:%'"),
        ('operations', "receipt='{}' WHERE command->>'kind'='RECORDER_STAGE'"),
        ('operations', "command=jsonb_set(command,'{kind}','\"WRONG\"') WHERE command->>'kind'='RECORDER_APPLY'"),
        ('projection_receipts', "applied_hash=repeat('f',64)"),
        ('spot_action_descriptors', "descriptor=jsonb_set(descriptor,'{controller_id}','\"foreign\"')"),
        ('life_recorder."Executors"', 'cum_fees_quote=cum_fees_quote+1'),
    )
    for table, change in faults:
        async with rolled_back(c):
            qualified = table if table.startswith('life_recorder.') else 'life_financial.' + table
            await c.execute(f'ALTER TABLE {qualified} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE {qualified} SET {change}') != 'UPDATE 0'
            await compare(False)
    # Rehashing both envelope and source operation cannot hide a malformed old
    # payload or a broken transition. Generated values recompute with USER off.
    edits = (
        ('timestamp', 'bad'), ('timestamp', '1e10000'), ('close_timestamp', '9223372036854775808'),
        ('net_pnl_pct', 'NaN'), ('filled_amount_quote', '-1'), ('status', 2),
        ('custom_info', {}), ('controller_id', 'foreign'), ('custom_info.recovery_order_ids', ['unknown']),
    )
    for field, value in edits:
        async with rolled_back(c):
            await c.execute('ALTER TABLE life_financial.recorder_outbox DISABLE TRIGGER USER; '
                            'ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute('''WITH changed AS (
                SELECT projection_id,jsonb_set(body,'{command,payload_json}',to_jsonb(jsonb_set(
                    (body#>>'{command,payload_json}')::jsonb,$1::text[],$2::jsonb)::text)) AS b
                FROM life_financial.recorder_outbox WHERE stream LIKE 'executor:%' AND sequence=1
            ) UPDATE life_financial.recorder_outbox o SET body=b,source_hash=encode(sha256(convert_to(b::text,'UTF8')),'hex')
                FROM changed c WHERE c.projection_id=o.projection_id''', field.split('.'), json.dumps(value))
            await c.execute('''UPDATE life_financial.operations op SET command=jsonb_set(command,'{command}',o.body->'command')
                FROM life_financial.recorder_outbox o WHERE op.account_id=o.account_id AND op.operation_id=o.projection_id
                    AND o.stream LIKE 'executor:%' AND o.sequence=1''')
            await compare(False)
    for column in ('executor_row_value', 'calculated_source_hash'):
        with pytest.raises(asyncpg.GeneratedAlwaysError):
            async with c.transaction():
                await c.execute(f'UPDATE life_financial.recorder_outbox SET {column}=NULL')
    for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.recorder_payload_value(jsonb)','EXECUTE')", role)


@pytest.mark.asyncio
async def test_recorder_write_values_refuse_changed_functions_or_generated_expressions(database):
    c, cfg = await populated(database, 2)
    await c.execute(SQL.read_text())
    for name, result_type in (('recorder_payload_value', 'jsonb'), ('recorder_executor_row', 'jsonb'), ('recorder_envelope_hash', 'text')):
        async with rolled_back(c):
            await c.execute(f'''CREATE OR REPLACE FUNCTION life_financial.{name}(p jsonb) RETURNS {result_type}
                LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$ BEGIN RETURN NULL;END; $$''')
            assert not await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)
    for column in ('executor_row_value', 'calculated_source_hash'):
        async with rolled_back(c):
            await c.execute(f'ALTER TABLE life_financial.recorder_outbox ALTER COLUMN {column} DROP EXPRESSION')
            # Even unchanged old values are unusable once SQL stops deriving them.
            assert not await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)
    for constraint in ('recorder_outbox_sequence_check', 'recorder_outbox_account_id_stream_sequence_key'):
        async with rolled_back(c):
            await c.execute(f'ALTER TABLE life_financial.recorder_outbox DROP CONSTRAINT {constraint}')
            assert not await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['function', 'constraint'])
async def test_recorder_write_values_unknown_predecessor_leaves_no_partial_columns(database, damage):
    c = database[0]
    if damage == 'function':
        await c.execute("ALTER FUNCTION life_financial.recorder_executor_row(jsonb) VOLATILE")
    else:
        await c.execute('ALTER TABLE life_financial.recorder_outbox DROP CONSTRAINT recorder_outbox_sequence_check')
    with pytest.raises(asyncpg.RaiseError, match='UNKNOWN_PREDECESSOR'):
        await c.execute(SQL.read_text())
    assert await c.fetchval("SELECT to_regprocedure('life_financial.recorder_payload_value(jsonb)')") is None
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='life_financial.recorder_outbox'::regclass AND attname='executor_row_value')")


@pytest.mark.asyncio
async def test_recorder_write_values_do_not_expand_public_delivery_or_its_byte_budget(database):
    c, cfg = await populated(database, 2)
    public_bytes = await c.fetchval('''SELECT sum(octet_length(to_jsonb(o)::text))+2*count(*)+2
        FROM life_financial.recorder_outbox o WHERE stream LIKE 'executor:%' AND sequence=2''')
    await c.execute(SQL.read_text())
    read_config = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'),
                          principal='life_pg2_reporter', write=False)
    f = PgSpotRepository(await PgRuntime.open(read_config))
    try:
        repo = PgRecorderRepository(f, controller_id='controller1', max_rows=8, max_read_bytes=public_bytes)
        raw = await repo._read(cfg.binding, False)
        assert len(raw) == 2
        assert all(set(row) == {'account_id', 'projection_id', 'stream', 'sequence', 'body', 'source_hash'} for row in raw)
        assert len(await repo.read(cfg.binding)) == 2
        smaller = PgRecorderRepository(f, controller_id='controller1', max_rows=8, max_read_bytes=public_bytes - 1)
        with pytest.raises(StorageUnavailable, match='BYTE_CAPACITY'):
            await smaller.read(cfg.binding)
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['empty', 'provisional', 'pending'])
async def test_recorder_write_values_keep_unprepared_birth_and_pending_semantics(database, state):
    c = database[0]
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, f, cfg, owner, lease, 0, {})
        if state != 'empty':
            ticket = await PgQuoteBirthRepository(f).issue('unprepared',
                                                           PgSpotQuoteIntent('unprepared', 'controller1', owner, 1, TradeType.BUY, 0, Decimal(1), Decimal(1)),
                                                           model_hash=fingerprint(MODEL), expected_version=(await f.snapshot(cfg.binding)).cut.checkpoint.revision)
            config = OrderExecutorConfig.model_validate_json(ticket.action.payload_json)
            info = ExecutorInfo(id=config.id, timestamp=config.timestamp, type='order_executor', controller_id='controller1',
                                config=config, status=RunnableStatus.NOT_STARTED, net_pnl_pct=Decimal(0), net_pnl_quote=Decimal(0),
                                cum_fees_quote=Decimal(0), filled_amount_quote=Decimal(0), is_active=False, is_trading=False,
                                custom_info={'recovery_account_uid': cfg.binding.account_uid, 'recovery_order_ids': []})
            command = ExecutorProjection.from_info('initial-unprepared', cfg.binding, info)
            repo = PgRecorderRepository(f, controller_id='controller1', max_rows=8, max_read_bytes=2**20)
            if state == 'pending':
                await repo.stage(command, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            else:
                await repo.project(command, owner=owner)
    finally:
        await f.close()
    await c.execute('UPDATE life_financial.access_bindings SET can_write=false WHERE principal=current_user AND account_id=$1', cfg.account_id)
    before = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    await c.execute(SQL.read_text())
    assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == before
    assert await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)
    if state != 'empty':
        async with rolled_back(c):
            await c.execute('ALTER TABLE life_financial.quote_births DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.quote_births SET body_hash=repeat('f',64) WHERE account_id=$1", cfg.account_id)
            assert not await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)


@pytest.mark.asyncio
async def test_recorder_write_values_join_work_on_growing_native_history(database, request):
    c, cfg = await populated(database, 32)
    original = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.recorder_executor_only_integrity(uuid)'::regprocedure")
    original_query = original.split('FOR joined IN', 1)[1].split(' LOOP\n', 1)[0]
    await c.execute(SQL.read_text())
    candidate = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.recorder_executor_only_integrity(uuid)'::regprocedure")
    candidate_query = candidate.split('IF EXISTS(WITH checked AS MATERIALIZED (', 1)[1].split(') THEN RETURN false;END IF;', 1)[0]
    candidate_query = 'WITH checked AS MATERIALIZED (' + candidate_query
    await c.execute('SET jit=off; SET plan_cache_mode=force_generic_plan')
    work = []
    for query in (original_query, candidate_query):
        plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF) ' +
                                           query.replace('p_account', '$1::uuid'), cfg.account_id))
        nodes = []

        def collect(node):
            nodes.append({k: node[k] for k in ('Node Type', 'Relation Name', 'Actual Rows', 'Actual Loops',
                                               'Rows Removed by Filter', 'Rows Removed by Join Filter', 'Join Filter') if k in node})
            for child in node.get('Plans', ()):
                collect(child)
        collect(plan[0]['Plan'])
        costly = sum(n.get('Actual Loops', 0) * n.get('Rows Removed by Join Filter', 0) for n in nodes
                     if any(k in n.get('Join Filter', '') for k in ('payload', 'controller_id', 'delivery')))
        work.append(costly)
        request.node.user_properties.append(('recorder_write_plan_' + str(len(work)), json.dumps(nodes)))
    request.node.user_properties.append(('recorder_write_join_discarded_pairs', json.dumps(work)))
    assert work[1] <= work[0], work
