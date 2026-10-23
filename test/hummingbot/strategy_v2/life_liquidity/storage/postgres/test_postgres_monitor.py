"""Telemetry uses separate read-only capacity and cannot become authority."""
import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle, prepared
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.monitor import AUDIT_SQL, PgFinancialMonitor


@pytest.mark.asyncio
async def test_monitor_reads_native_scope_without_touching_runtime_capacity_or_authority(database):
    runtime, cfg, _ = await backend(database)
    monitor = PgFinancialMonitor(cfg, max_rows=32, timeout_ms=1000)
    try:
        before = dict(await database[0].fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id))
        await monitor.sample()
        report = monitor.status()
        assert report['state'] == 'CURRENT' and report['trading_permission'] is False
        assert report['owner_generation'] == str(before['owner_generation'])
        assert report['unknown_attempts'] == dict(count=0, truncated=False)
        assert report['unresolved_holds'] == dict(count=0, truncated=False)
        assert report['balances']['USDT']['reserved'] == '0'
        assert report['audit_health'] == report['backup_health'] == 'UNVERIFIED'
        report['balances']['USDT']['reserved'] = '999'
        assert monitor.status()['balances']['USDT']['reserved'] == '0'
        assert runtime.healthy and runtime._counts == {False: 0, True: 0}
        assert dict(await database[0].fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)) == before
        async with monitor._pool.acquire() as c:
            assert await c.fetchval('SHOW default_transaction_read_only') == 'on'
            with pytest.raises(Exception, match='read-only'):
                await c.execute('CREATE TABLE public.forbidden_monitor_write (v integer)')
    finally:
        await monitor.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_wrong_scope_and_database_loss_clear_freshness_without_poisoning_sender(database):
    runtime, cfg, _ = await backend(database)
    monitor = PgFinancialMonitor(replace(cfg, account_id=uuid4()), max_rows=32, timeout_ms=1000)
    try:
        await monitor.sample()
        assert monitor.status()['state'] == 'UNAVAILABLE'
        assert monitor.status()['reason_code'] == 'FINANCIAL_TELEMETRY_UNAVAILABLE'
        assert 'dsn' not in str(monitor.status()).lower()
        assert runtime.healthy
    finally:
        await monitor.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_sampler_does_not_queue_behind_previous_read_or_resume_after_close(database):
    runtime, cfg, _ = await backend(database)
    monitor = PgFinancialMonitor(cfg, max_rows=32, timeout_ms=1000)
    try:
        await monitor.sample()
        async with monitor._pool.acquire():
            first = asyncio.create_task(monitor.sample())
            await asyncio.sleep(.02)
            assert not first.done()
            await asyncio.wait_for(monitor.sample(), .1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await monitor.close()
        await monitor.sample()
        assert monitor.status()['state'] == 'CLOSED'
        assert runtime.healthy
    finally:
        await monitor.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_unknown_cancel_retains_true_money_and_truncated_counts_never_look_complete(database):
    repo, cfg, owner = await prepared(database, arm=True)
    monitor = PgFinancialMonitor(cfg, max_rows=1, timeout_ms=1000)
    try:
        before = await repo.snapshot(cfg.binding)
        await repo.apply_lifecycle(lifecycle('REQUEST_CANCEL'), cut=before.cut, owner=owner)
        await monitor.sample()
        report = monitor.status()
        assert report['state'] == 'CURRENT'
        assert report['unknown_attempts'] == dict(count=1, truncated=False)
        count = await database[0].fetchval("SELECT count(*) FROM life_financial.reservations WHERE account_id=$1 AND state<>'TERMINAL'", cfg.account_id)
        assert count > 1 and report['unresolved_holds'] == dict(count=1, truncated=True)
        assert Decimal(report['balances']['USDT']['reserved']) == before.balance.reserved_quote_usdt
        assert report['budgets'] and report['budget_usage_ratio_max'] > 0
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        # A diagnostic connection failure retains old values with age, never
        # replaces unresolved funds with a successful zero-valued observation.
        monitor._pool.terminate()
        await monitor.sample()
        failed = monitor.status()
        assert failed['state'] == 'UNAVAILABLE'
        assert failed['unknown_attempts'] == report['unknown_attempts']
        assert failed['sample_age_ms'] >= report['sample_age_ms']
        assert repo._runtime.healthy
    finally:
        await monitor.close()
        await repo.close()


@pytest.mark.asyncio
async def test_audit_head_is_indexed_with_retained_history_and_signature_presence_is_not_verification(database):
    runtime, cfg, _ = await backend(database)
    monitor = PgFinancialMonitor(cfg, max_rows=1, timeout_ms=1000)
    try:
        c = database[0]
        # Untrusted synthetic history for query shape only; this is deliberately
        # NOT an independently signed audit or financial acceptance artifact.
        await c.execute('''INSERT INTO life_financial.audit_checkpoints
            SELECT $1,'synthetic-'||n,n,repeat('a',64),'unverified'::bytea FROM generate_series(1,10000) n''', cfg.account_id)
        await c.execute('ANALYZE life_financial.audit_checkpoints')
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_recorder_queries import (
            plan_nodes,
        )
        plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + AUDIT_SQL, cfg.account_id))[0]['Plan']
        nodes = list(plan_nodes(plan))
        assert any(n.get('Index Name') == 'audit_checkpoint_sequence' for n in nodes)
        assert sum(n.get('Rows Removed by Filter', 0) for n in nodes) == 0
        await monitor.sample()
        report = monitor.status()
        assert report['state'] == 'CURRENT' and report['audit_recorded_sequence'] == 10000
        assert report['audit_signature_present'] is True and report['audit_health'] == 'UNVERIFIED'
    finally:
        await monitor.close()
        await runtime.close()
