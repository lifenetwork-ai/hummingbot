"""One STABLE snapshot reuses its own carry proof, never a stored permission."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import setup as swap_setup

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['no_policy', 'pending', 'qualified', 'denied', 'expired', 'corrupt_receipt'])
async def test_portfolio_snapshot_keeps_original_digest_with_one_fewer_carry_proof(database, state):
    c = database[0]
    helper = 'life_financial.portfolio_after_carry_verification(uuid,boolean)'
    assert await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', helper)
    original = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.portfolio_verified(uuid)'::regprocedure")
    reused = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', helper)
    assert reused == original.replace('NOT life_financial.carry_verified(p_account)', 'p_verified IS DISTINCT FROM true')
    values, repo, *_ = await (swap_setup(database) if state == 'no_policy' else setup(database))
    financial, cfg, owner = values[:3]
    try:
        if state in ('qualified', 'expired', 'corrupt_receipt'):
            await repo.measure(MeasurePortfolio('portfolio'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        elif state == 'denied':
            values[8].observation = lambda: None
            assert not (await values[8].check_carry(cfg.binding)).allowed
        if state == 'expired':
            await asyncio.sleep(1.05)
        if state == 'corrupt_receipt':
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"checked_at_ms\":1}'::jsonb WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure)")
        call = ("CASE WHEN b->'unverified' IS NOT DISTINCT FROM 'false'::jsonb THEN "
                "life_financial.portfolio_after_financial_verification(p_account,(b#>>'{carry,verified}')::boolean,(b#>>'{shared_capital,verified}')::boolean,true) "
                "ELSE life_financial.portfolio_after_snapshot_verification(p_account,(b#>>'{carry,verified}')::boolean,(b#>>'{shared_capital,verified}')::boolean) END")
        assert definition.count(call) == 1
        prior = definition.replace('life_financial.spot_body_before_swap_terminal(', 'life_financial.test_prior_portfolio_body(')
        await c.execute(prior.replace(call, 'life_financial.portfolio_verified(p_account)'))
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_prior_portfolio_body(uuid) FROM PUBLIC')
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
        await c.execute('CREATE SEQUENCE life_financial.test_portfolio_carry_count MINVALUE 0 START 0')
        proof = await c.fetchval("SELECT pg_get_functiondef('life_financial.carry_verified(uuid)'::regprocedure)")
        await c.execute(proof.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_portfolio_carry_count');", 1))
        bodies, counts = [], []
        for function in ('spot_body_before_swap_terminal', 'test_prior_portfolio_body'):
            await c.fetchval("SELECT setval('life_financial.test_portfolio_carry_count',0,false)")
            bodies.append(await c.fetchval(f"SELECT (life_financial.{function}($1))::text", cfg.account_id))
            counts.append(await c.fetchval("SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_portfolio_carry_count"))
        assert bodies[0] == bodies[1]
        assert counts[0] >= 1 and counts[1] == counts[0] + (state != 'no_policy')
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
        native = await asyncpg.connect(cfg.dsn)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await native.fetchval('SELECT life_financial.portfolio_after_carry_verification($1,true)', cfg.account_id)
        finally:
            await native.close()
    finally:
        await financial.close()
