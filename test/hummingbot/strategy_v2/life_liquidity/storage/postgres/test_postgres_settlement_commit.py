"""Distinguish real settlement refusals without changing the public code/gates."""
import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_settlement import (
    command,
    cycle,
    facts,
)

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


async def install_diagnostic_overlay(connection):
    if os.environ.get('LIFE_PG_TEST_SETTLEMENT_DIAGNOSTIC') != '1':
        await connection.execute(Path(__file__).with_name('settlement_commit_diagnostics.sql').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('case,reason', [('expired', 'SETTLEMENT_FACTS_EXPIRED'), ('future', 'SETTLEMENT_FACTS_FUTURE'),
                                         ('nonfinite', 'SETTLEMENT_FACTS_NONFINITE'),
                                         ('projection', 'SETTLEMENT_PROJECTION_INVALID')])
async def test_commit_guard_reports_its_actual_failed_predicate(database, case, reason):
    repo, cfg, owner = await spot(database)
    c = database[0]
    try:
        await install_diagnostic_overlay(c)
        await c.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,1000)', cfg.account_id, cfg.binding.policy_hash)
        if case == 'projection':
            value = 'false'
            await c.execute(f'''CREATE OR REPLACE FUNCTION life_financial.spot_lifecycle_verified(p_account uuid) RETURNS boolean
                LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT {value}' ''')
        expressions = dict(expired="clock_timestamp()-interval '2 seconds'", future="clock_timestamp()+interval '2 seconds'",
                           nonfinite="'infinity'::timestamptz", missing='NULL::timestamptz')
        expr = expressions.get(case, 'clock_timestamp()')
        with pytest.raises(asyncpg.RaiseError) as failed:
            await c.execute(f'''SELECT life_financial.spot_inventory_commit_guard($1,
                jsonb_build_object('facts',jsonb_build_object('observed_at',({expr})::text)))''', cfg.account_id)
        assert failed.value.message == 'LIFE_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT'
        detail = json.loads(failed.value.detail)
        assert detail['format'] == 'LIFE_SETTLEMENT_COMMIT_V1' and detail['reason'] == reason
        assert detail['max_fact_age_ms'] == 1000
        if case == 'expired':
            assert detail['age_ms'] >= 2000
        elif case == 'future':
            assert detail['age_ms'] < 0
        elif case in ('missing', 'nonfinite'):
            assert detail['age_ms'] is None
        else:
            assert 0 <= detail['age_ms'] < 1000
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['fresh', 'missing', 'null_projection'])
async def test_diagnostic_guard_preserves_original_null_behavior_and_verifier_calls(database, case):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument

    repo, cfg, _ = await spot(database)
    c = database[0]
    try:
        await c.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,1000)', cfg.account_id, cfg.binding.policy_hash)
        original = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_inventory_commit_guard(uuid,jsonb)'::regprocedure)")
        await c.execute('CREATE SCHEMA settlement_diagnostic_reference')
        await c.execute(original.replace('FUNCTION life_financial.spot_inventory_commit_guard',
                                         'FUNCTION settlement_diagnostic_reference.guard', 1))
        await install_diagnostic_overlay(c)
        if case == 'null_projection':
            await c.execute('''CREATE OR REPLACE FUNCTION life_financial.spot_lifecycle_verified(p_account uuid) RETURNS boolean
                LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT NULL::boolean' ''')
        counter = await instrument(c, 'spot_inventory_settlements_verified')
        expression = 'NULL::timestamptz' if case == 'missing' else 'clock_timestamp()'
        counts = []
        for name in ('settlement_diagnostic_reference.guard', 'life_financial.spot_inventory_commit_guard'):
            await calls(c, counter, reset=True)
            await c.execute(f'''SELECT {name}($1,jsonb_build_object('facts',
                jsonb_build_object('observed_at',({expression})::text)))''', cfg.account_id)
            counts.append(await calls(c, counter))
        assert counts == ([0, 0] if case == 'null_projection' else [1, 1]), counts
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [134], indirect=True)
async def test_guard_upgrade_preserves_identity_acl_and_repeat_migration(database):
    c = database[0]
    await install_diagnostic_overlay(c)
    query = "SELECT oid,proacl,proowner,proconfig FROM pg_proc WHERE oid='life_financial.spot_inventory_commit_guard(uuid,jsonb)'::regprocedure"
    before = await c.fetchrow(query)
    await migrate(c, action='up')
    assert await c.fetchrow(query) == before
    assert (await migrate(c, action='up'))['applied'] == []


@pytest.mark.asyncio
async def test_settlement_precheck_reports_fact_age_at_the_original_decision_point(database):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError

    repo, cfg, owner, settlement = await cycle(database)
    try:
        await install_diagnostic_overlay(database[0])
        before = await repo.snapshot(cfg.binding)
        source = await facts(repo)
        expired = replace(source, observed_at=source.observed_at - timedelta(seconds=2))
        with pytest.raises(StorageError, match='PG_SETTLEMENT_FACTS_UNQUALIFIED'):
            await settlement.settle_inventory(command(), facts=expired, cut=before.cut, owner=owner)
        diagnostic = repo._runtime.diagnostics[-1]
        assert diagnostic.error_phase == 'execute'
        assert diagnostic.reason_code == 'SETTLEMENT_FACTS_EXPIRED'
        assert diagnostic.fact_age_ms >= 2000 and diagnostic.fact_max_age_ms == 1000
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()
