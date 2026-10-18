"""Reuse only the already computed carry proof inside one stable snapshot."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
    setup as shared_setup,
)

import asyncpg
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['no_policy', 'pending', 'qualified', 'denied', 'expired', 'corrupt_receipt'])
async def test_same_snapshot_body_digest_and_decisions_with_one_fewer_proof(database, state):
    c = database[0]
    helper = 'life_financial.carry_ready_after_verification(uuid,boolean)'
    assert await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', helper)
    original_body = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.carry_current_ready(uuid)'::regprocedure")
    reused_body = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', helper)
    assert reused_body == original_body.replace('IF NOT life_financial.carry_verified(p_account) THEN', 'IF p_verified IS DISTINCT FROM true THEN')
    if state == 'no_policy':
        financial, cfg, owner, *_ = await shared_setup(database)
    else:
        financial, cfg, owner, _, _, capital, policy, feed, repo = await setup(database, refresh_initial_facts=True)
    try:
        if state not in ('no_policy', 'pending'):
            if state == 'denied':
                repo.observation = lambda: None
            assert (await repo.check_carry(cfg.binding)).allowed is (state != 'denied')
        if state == 'expired':
            await asyncio.sleep(1.05)
        if state == 'corrupt_receipt':
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"checked_at_ms\":1}'::jsonb WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        # Independent original path: exactly the pre-optimization body and its
        # original ready function (which still performs its own full proof).
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_state_before_swap(uuid)'::regprocedure)")
        assert definition.count('life_financial.carry_ready_after_verification(p_account,verified)') == 1
        historical = definition.replace('life_financial.spot_state_before_swap(', 'life_financial.test_prior_carry_state(')
        historical = historical.replace('life_financial.carry_ready_after_verification(p_account,verified)', 'life_financial.carry_current_ready(p_account)')
        await c.execute(historical)
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_prior_carry_state(uuid) FROM PUBLIC')
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
        # A test-only sequence counts actual proof invocations without replacing
        # any financial predicate or granting the runtime new entrypoints.
        await c.execute('CREATE SEQUENCE life_financial.test_carry_proof_count MINVALUE 0 START 0')
        proof = await c.fetchval("SELECT pg_get_functiondef('life_financial.carry_verified(uuid)'::regprocedure)")
        await c.execute(proof.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_carry_proof_count');", 1))
        values = []
        counts = []
        for function in ('spot_state_before_swap', 'test_prior_carry_state'):
            await c.fetchval("SELECT setval('life_financial.test_carry_proof_count',0,false)")
            values.append(await c.fetchval(f"SELECT (life_financial.{function}($1)-'observed_at')::text", cfg.account_id))
            counts.append(await c.fetchval("SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_carry_proof_count"))
        assert values[0] == values[1]  # Includes the published digest and flags.
        assert counts[0] >= 1 and counts[1] == counts[0] + (state != 'no_policy')
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
        native = await asyncpg.connect(cfg.dsn)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await native.fetchval('SELECT life_financial.carry_ready_after_verification($1,true)', cfg.account_id)
        finally:
            await native.close()
    finally:
        await financial.close()
