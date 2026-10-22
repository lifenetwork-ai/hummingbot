"""Whole snapshot/COMMIT equivalence for same-invocation capital proof reuse."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'capital_receipt', 'carry_receipt', 'release_version'])
async def test_historical_claim_membership_uses_join_without_changing_retained_decisions(database, fault):
    """Compare original history predicates and count actual nested SQL calls."""
    c = database[0]
    financial, cfg, owner, _, account_feed, capital, _, feed, repo = await setup(
        database, max_age_ms=60000, refresh_initial_facts=True)
    try:
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
            native_claim,
        )

        from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
        claim = native_claim(owner)
        assert (await capital.reserve(ReserveCapital('joined-history-claim', claim),
                                      await capital.cut(cfg.binding), owner)).decision.allowed
        for sequence in range(2, 5):
            at = int((await c.fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
            account_feed[0] = replace(account_feed[0], sequence=sequence, observed_at_ms=at)
            assert (await capital.check_capital(cfg.binding)).allowed
            feed[0] = replace(feed[0], account=replace(feed[0].account, snapshot=account_feed[0]),
                              **{key: replace(getattr(feed[0], key), sequence=sequence, observed_at_ms=at)
                                 for key in ('mark', 'index', 'funding', 'terms')})
            assert (await repo.check_carry(cfg.binding)).allowed
        # Keep the predecessor's exact query, including the inclusive release
        # version boundary. No synthetic permission or saved proof is supplied.
        original = 'life_financial.shared_claim_active_at(p_account,c.intent_id,op.committed_version-1)'
        joined = '''NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases released
            JOIN life_financial.operations release_op USING(account_id,operation_id)
            WHERE released.account_id=p_account AND released.intent_id=c.intent_id
                AND release_op.committed_version<=op.committed_version-1)'''
        await c.execute('CREATE SCHEMA test_history_reference')
        for name in ('shared_operation_verified', 'carry_check_verified'):
            definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))',
                                          'life_financial.' + name + '(uuid,text)')
            assert definition.count(joined) == 1 and original not in definition
            prior = definition.replace('FUNCTION life_financial.' + name, 'FUNCTION test_history_reference.' + name, 1)
            prior = prior.replace(joined, original)
            if name == 'carry_check_verified':
                prior = prior.replace('life_financial.shared_operation_verified(', 'test_history_reference.shared_operation_verified(')
            await c.execute(prior)
        await c.execute('CREATE SEQUENCE life_financial.test_claim_history_calls MINVALUE 0 START 0')
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.shared_claim_active_at(uuid,text,bigint)'::regprocedure)")
        body = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.shared_claim_active_at(uuid,text,bigint)'::regprocedure")
        await c.execute(definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(
            body, "BEGIN PERFORM nextval('life_financial.test_claim_history_calls'); RETURN (" + body.strip().rstrip(';') + '); END;'))
        if fault in ('capital_receipt', 'carry_receipt'):
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"corrupt\":true}'::jsonb WHERE account_id=$1 AND command->>'kind'=$2",
                            cfg.account_id, 'SHARED_CAPITAL' if fault == 'capital_receipt' else 'CARRY_CHECK')
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        if fault == 'release_version':
            # Bypass only the fixture's immutable-history guards, then retain a
            # release on the exact historical cutoff. Both verifiers must see
            # the same original committed release semantics, even if orphaned.
            import asyncpg
            operation = await c.fetchval("SELECT operation_id FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK' ORDER BY committed_version LIMIT 1", cfg.account_id)
            admin = await asyncpg.connect(database[1])
            try:
                await admin.execute('ALTER TABLE life_financial.shared_claim_releases DISABLE TRIGGER ALL')
                await admin.execute("INSERT INTO life_financial.shared_claim_releases VALUES($1,$2,$3,$3,'{}'::jsonb,1)",
                                    cfg.account_id, claim.intent_id, operation)
                await admin.execute('ALTER TABLE life_financial.shared_claim_releases ENABLE TRIGGER ALL')
            finally:
                await admin.close()
        before = await c.fetch('SELECT * FROM life_financial.balances ORDER BY account_id,asset')
        results, counts = [], []
        for schema in ('life_financial', 'test_history_reference'):
            await c.fetchval("SELECT setval('life_financial.test_claim_history_calls',0,false)")
            rows = await c.fetch(f'''SELECT operation_id,command->>'kind' AS kind,
                CASE command->>'kind' WHEN 'SHARED_CAPITAL' THEN {schema}.shared_operation_verified(account_id,operation_id)
                ELSE {schema}.carry_check_verified(account_id,operation_id) END AS verified
                FROM life_financial.operations WHERE account_id=$1 AND command->>'kind' IN ('SHARED_CAPITAL','CARRY_CHECK')
                ORDER BY operation_id''', cfg.account_id)
            results.append([dict(row) for row in rows])
            counts.append(await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_claim_history_calls'))
        assert results[0] == results[1], json.dumps(results)
        assert counts[0] == 0 and counts[1] > 0
        if fault == 'none':
            assert all(row['verified'] for row in results[0])
        else:
            assert not all(row['verified'] for row in results[0])
        assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY account_id,asset') == before
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['qualified', 'pending', 'denied', 'capital_corrupt', 'carry_corrupt', 'unreferenced_head'])
async def test_flow_snapshot_and_commit_keep_original_proofs_with_fewer_shared_operation_walks(database, state):
    c = database[0]
    helper = 'life_financial.shared_capital_after_carry_proof(uuid,boolean)'
    assert await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', helper)
    # Explicit synthetic matrix policy, separate from the unchanged 1000-ms
    # loaded/service gates; preserve one original lease throughout this test.
    financial, cfg, owner, _, account_feed, capital, _, _, repo = await setup(database, max_age_ms=60000, refresh_initial_facts=True)
    try:
        if state != 'pending':
            if state == 'denied':
                repo.observation = lambda: None
            assert (await repo.check_carry(cfg.binding)).allowed is (state != 'denied')
        if state == 'unreferenced_head':
            at = int((await c.fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
            account_feed[0] = replace(account_feed[0], sequence=2, observed_at_ms=at)
            assert (await capital.check_capital(cfg.binding)).allowed
        if state in ('capital_corrupt', 'carry_corrupt'):

            kind = 'SHARED_CAPITAL' if state == 'capital_corrupt' else 'CARRY_CHECK'
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"changed\":\"corrupt\"}'::jsonb WHERE account_id=$1 AND command->>'kind'=$2", cfg.account_id, kind)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_body_before_swap(uuid)'::regprocedure)")
        needle = """IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    verified:=life_financial.carry_verified(p_account);
    b:=life_financial.spot_body_with_carry_proof(p_account,verified);
    ready:=life_financial.carry_ready_after_verification(p_account,verified);"""
        assert definition.count(needle) == 1
        prior = definition.replace('life_financial.spot_body_before_swap(', 'life_financial.test_prior_flow_body(', 1)
        prior = prior.replace(
            needle,
            'b:=life_financial.spot_body_before_carry(p_account); verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_ready_after_verification(p_account,verified);')
        await c.execute(prior)
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_prior_flow_body(uuid) FROM PUBLIC')
        await c.execute('CREATE SEQUENCE life_financial.test_shared_walks MINVALUE 0 START 0')
        full = await c.fetchval("SELECT pg_get_functiondef('life_financial.shared_operation_verified(uuid,text)'::regprocedure)")
        await c.execute(full.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_shared_walks');", 1))
        values, counts = [], []
        for name in ('spot_body_before_swap', 'test_prior_flow_body'):
            await c.fetchval("SELECT setval('life_financial.test_shared_walks',0,false)")
            values.append(await c.fetchval(f'SELECT life_financial.{name}($1)::text', cfg.account_id))
            counts.append(await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_shared_walks'))
        assert values[0] == values[1]
        assert counts[1] == counts[0] + (state in ('qualified', 'denied')), counts
        if state == 'qualified':
            commit = await c.fetchval("SELECT pg_get_functiondef('life_financial.capital_carry_portfolio_commit_check(uuid,bigint)'::regprocedure)")
            needle = 'shared_ok:=life_financial.shared_capital_after_carry_proof(p_account,carry_ok);'
            assert commit.count(needle) == 1
            prior = commit.replace('life_financial.capital_carry_portfolio_commit_check(', 'life_financial.test_prior_flow_commit(', 1)
            prior = prior.replace(needle, 'shared_ok:=life_financial.shared_capital_verified(p_account);')
            await c.execute(prior)
            await c.execute('REVOKE ALL ON FUNCTION life_financial.test_prior_flow_commit(uuid,bigint) FROM PUBLIC')
            counts = []
            version = await c.fetchval('SELECT version FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
            for name in ('capital_carry_portfolio_commit_check', 'test_prior_flow_commit'):
                await c.fetchval("SELECT setval('life_financial.test_shared_walks',0,false)")
                await c.execute(f'SELECT life_financial.{name}($1,$2)', cfg.account_id, version)
                counts.append(await c.fetchval('SELECT last_value+1 FROM life_financial.test_shared_walks'))
            assert counts[1] == counts[0] + 1
        for signature in (helper, 'life_financial.spot_body_with_carry_proof(uuid,boolean)'):
            for role in ('life_pg2_runtime', 'life_pg2_reporter'):
                assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    finally:
        await financial.close()
