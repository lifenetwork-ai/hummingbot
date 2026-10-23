"""Set-based action finality retains complete original closure decisions."""
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [128], indirect=True)
async def test_whole_action_audit_batches_closure_without_trusting_saved_finality(database, request):
    load = runtime.load_migrations
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 128)):
        values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer=MODEL)
        f, cfg, owner, _, lease, *_ = values
        try:
            await seed_completed(database, f, cfg, owner, lease, 4, {})
        finally:
            await f.close()
    c = database[0]
    await c.execute('CREATE SCHEMA action_reference')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_actions_verified(uuid)'::regprocedure)")
    await c.execute(definition.replace('FUNCTION life_financial.spot_actions_verified', 'FUNCTION action_reference.original', 1))
    await migrate(c, action='up')
    sequence = await instrument(c, 'spot_action_closed_at', 'uuid,life_financial.identity')
    counts = []
    for name in ('life_financial.spot_actions_verified', 'action_reference.original'):
        await calls(c, sequence, reset=True)
        assert await c.fetchval(f'SELECT {name}($1)', cfg.account_id)
        counts.append(await calls(c, sequence))
    request.node.user_properties.append(('action_closure_queries', str(counts)))
    assert counts == [0, 4], counts
    faults = (
        ('operations', "recorded_at='infinity' WHERE command->>'kind'='SPOT_TERMINAL'"),
        ('send_attempts', "state='SEND_UNKNOWN'"),
        ('reservations', "remaining=amount,state='HELD'"),
    )
    for table, change in faults:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            await c.execute(f'UPDATE life_financial.{table} SET {change}')
            previous = await c.fetchval('SELECT action_reference.original($1)', cfg.account_id)
            current = await c.fetchval('SELECT life_financial.spot_actions_verified($1)', cfg.account_id)
            assert current is previous is False, table
        finally:
            await tx.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [128], indirect=True)
@pytest.mark.parametrize('stage', ['abort', 'zero'])
async def test_batched_closure_keeps_native_abort_and_zero_fill_terminal_origins(database, stage):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_reconciliation import closed
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import transition
    load = runtime.load_migrations
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 128)):
        f, cfg, owner, row = await closed(database, stage=stage)
        try:
            command = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
            await f.transition_quote_action(command, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        finally:
            await f.close()
    c = database[0]
    before = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    await c.execute('CREATE SCHEMA action_reference')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_actions_verified(uuid)'::regprocedure)")
    await c.execute(definition.replace('FUNCTION life_financial.spot_actions_verified', 'FUNCTION action_reference.original', 1))
    await migrate(c, action='up')
    assert await c.fetchval('SELECT action_reference.original($1)', cfg.account_id)
    assert await c.fetchval('SELECT life_financial.spot_actions_verified($1)', cfg.account_id)
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == before
