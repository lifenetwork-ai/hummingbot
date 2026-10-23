"""Joined recorder origins and applied heads preserve complete old decisions."""
import json
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from unittest.mock import patch

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [130], indirect=True)
@pytest.mark.parametrize('state', ['empty', 'settled', 'provisional', 'pending'])
async def test_recorder_history_joins_original_origins_and_heads_without_reusing_a_saved_verdict(database, state, request):
    load = runtime.load_migrations
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 130)):
        values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer=MODEL)
        f, cfg, owner, _, lease, *_ = values
        try:
            await seed_completed(database, f, cfg, owner, lease, 4 if state == 'settled' else 0, {})
            if state in ('provisional', 'pending'):
                ticket = await PgQuoteBirthRepository(f).issue('unprepared',
                                                               PgSpotQuoteIntent('unprepared', 'controller1', owner, 1, TradeType.BUY, 0, Decimal(1), Decimal(1)),
                                                               model_hash=fingerprint(MODEL), expected_version=(await f.snapshot(cfg.binding)).cut.checkpoint.revision)
                config = OrderExecutorConfig.model_validate_json(ticket.action.payload_json)
                info = ExecutorInfo(id=config.id, timestamp=config.timestamp, type='order_executor', controller_id='controller1',
                                    config=config, status=RunnableStatus.NOT_STARTED, net_pnl_pct=Decimal(0), net_pnl_quote=Decimal(0),
                                    cum_fees_quote=Decimal(0), filled_amount_quote=Decimal(0), is_active=False, is_trading=False,
                                    custom_info={'recovery_account_uid': cfg.binding.account_uid, 'recovery_order_ids': []})
                command = ExecutorProjection.from_info('initial-unprepared', cfg.binding, info)
                recorder = PgRecorderRepository(f, controller_id='controller1', max_rows=64, max_read_bytes=2**20)
                if state == 'pending':
                    await recorder.stage(command, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
                else:
                    await recorder.project(command, owner=owner)
        finally:
            await f.close()
    c = database[0]
    names = ('recorder_executor_only_integrity(uuid)', 'recorder_executor_origin(uuid,jsonb,jsonb)',
             'recorder_executor_row(jsonb)', 'recorder_executor_transition(jsonb,jsonb)')
    predecessors = await c.fetch('''SELECT oid::regprocedure::text AS signature,
        encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS sha256 FROM pg_proc
        WHERE oid=ANY($1::regprocedure[])''', ['life_financial.' + n for n in names])
    request.node.user_properties.append(('recorder_batch_predecessors', json.dumps([dict(r) for r in predecessors])))
    await c.execute('CREATE SCHEMA recorder_batch_reference')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.recorder_executor_only_integrity(uuid)'::regprocedure)")
    await c.execute(definition.replace('FUNCTION life_financial.recorder_executor_only_integrity', 'FUNCTION recorder_batch_reference.original', 1))
    before = await c.fetchval('SELECT life_financial.spot_state($1)::text', cfg.account_id)
    await migrate(c, action='up')
    after = await c.fetchval('SELECT life_financial.spot_state($1)::text', cfg.account_id)
    a, b = json.loads(before), json.loads(after)
    a.pop('observed_at'); b.pop('observed_at')
    assert a == b
    sequence = await instrument(c, 'recorder_executor_origin', 'uuid,jsonb,jsonb')
    row_sequence = await instrument(c, 'recorder_executor_row', 'jsonb')
    counts = []
    for name in ('life_financial.recorder_executor_only_integrity', 'recorder_batch_reference.original'):
        await calls(c, sequence, reset=True)
        await calls(c, row_sequence, reset=True)
        assert await c.fetchval(f'SELECT {name}($1)', cfg.account_id)
        counts.append((await calls(c, sequence), await calls(c, row_sequence)))
    request.node.user_properties.append(('recorder_batch_calls', json.dumps(counts)))
    if state == 'settled':
        assert counts == [(0, 8), (8, 12)], counts
    elif state in ('provisional', 'pending'):
        assert counts[0][0] == counts[1][0] == 1  # Original unprepared birth proof remains mandatory.
    if state != 'settled':
        return
    faults = (
        ('operations', "receipt='{}'::jsonb WHERE command->>'kind'='RECORDER_STAGE'"),
        ('operations', "command=jsonb_set(command,'{kind}','\"WRONG\"') WHERE command->>'kind'='RECORDER_APPLY'"),
        ('projection_receipts', "applied_hash=repeat('f',64)"),
        ('recorder_outbox', "source_hash=repeat('f',64)"),
        ('recorder_outbox', "sequence=sequence+20 WHERE sequence=2 AND stream LIKE 'executor:%'"),
        ('recorder_outbox', "body=jsonb_set(body,'{command,expected_sequence}','99') WHERE sequence=2 AND stream LIKE 'executor:%'"),
        ('life_recorder."Executors"', "cum_fees_quote=cum_fees_quote+1"),
        ('spot_action_descriptors', "descriptor=jsonb_set(descriptor,'{controller_id}','\"foreign\"')"),
    )
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            qualified = table if table.startswith('life_recorder.') else 'life_financial.' + table
            await c.execute(f'ALTER TABLE {qualified} DISABLE TRIGGER USER')
            assert await c.execute(f'UPDATE {qualified} SET {change}') != 'UPDATE 0'
            for name in ('life_financial.recorder_executor_only_integrity', 'recorder_batch_reference.original'):
                assert not await c.fetchval(f'SELECT {name}($1)', cfg.account_id), table
        finally:
            await tx.rollback()
    # Rehash the envelope and update its original stage operation too. These
    # cases must fail the joined origin predicates, not merely a stale hash.
    for payload_change in (
        "jsonb_set(payload,'{custom_info,recovery_account_uid}','\"foreign\"')",
        "jsonb_set(payload,'{custom_info,life_storage_binding,account_uid}','\"foreign\"')",
        "jsonb_set(payload,'{custom_info,recovery_order_ids}','[\"foreign-wire\"]')",
        "jsonb_set(payload,'{custom_info,recovery_order_ids}',(payload#>'{custom_info,recovery_order_ids}')||(payload#>'{custom_info,recovery_order_ids}'))",
    ):
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute('ALTER TABLE life_financial.recorder_outbox DISABLE TRIGGER USER; '
                            'ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute('''WITH selected AS MATERIALIZED (
                SELECT projection_id,(body#>>'{command,payload_json}')::jsonb AS payload
                FROM life_financial.recorder_outbox WHERE stream LIKE 'executor:%' AND sequence=2)
                UPDATE life_financial.recorder_outbox o SET body=jsonb_set(o.body,'{command,payload_json}',to_jsonb((''' + payload_change + ''')::text))
                FROM selected WHERE selected.projection_id=o.projection_id''')
            await c.execute("UPDATE life_financial.recorder_outbox SET source_hash=encode(sha256(convert_to(body::text,'UTF8')),'hex')")
            await c.execute('''UPDATE life_financial.operations op SET command=jsonb_build_object('kind','RECORDER_STAGE',
                'subtype',o.body->'subtype','command',o.body->'command','binding',o.body->'binding',
                'owner',o.body->'owner','cut',o.body->'cut','version',o.body->'version')
                FROM life_financial.recorder_outbox o WHERE op.operation_id=o.projection_id AND op.account_id=o.account_id''')
            for name in ('life_financial.recorder_executor_only_integrity', 'recorder_batch_reference.original'):
                assert not await c.fetchval(f'SELECT {name}($1)', cfg.account_id), payload_change
        finally:
            await tx.rollback()
