"""Release uses its own verified snapshot; COMMIT still proves the new state."""
import asyncio
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_release import setup

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'portfolio', 'carry', 'expired', 'wrong_terminal'])
async def test_release_reuses_only_its_own_snapshot_and_preserves_original_decisions(database, fault):
    c = database[0]
    values, repo, command, *_ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        signature = 'life_financial.shared_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        call = "(view#>>'{portfolio,verified}')::boolean IS DISTINCT FROM true"
        assert definition.count(call) == 1
        historical = definition.replace('life_financial.shared_release_command(', 'life_financial.test_prior_release_command(')
        await c.execute(historical.replace(call, 'NOT life_financial.portfolio_verified(p_account)'))
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_prior_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) FROM PUBLIC')
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
        await c.execute('CREATE SEQUENCE life_financial.test_release_carry_count MINVALUE 0 START 0')
        proof = await c.fetchval("SELECT pg_get_functiondef('life_financial.carry_verified(uuid)'::regprocedure)")
        await c.execute(proof.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_release_carry_count');", 1))
        if fault in ('portfolio', 'carry'):
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            if fault == 'portfolio':
                await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"halted\":true}'::jsonb WHERE account_id=$1 AND command->>'kind'='PORTFOLIO_NAV'", cfg.account_id)
            else:
                await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"checked_at_ms\":1}'::jsonb WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        elif fault == 'expired':
            await asyncio.sleep(5.05)  # Existing synthetic portfolio age limit, unchanged.
        elif fault == 'wrong_terminal':
            command = replace(command, terminal_operation_id='unknown')
        cut = (await financial.snapshot(cfg.binding)).cut
        version, digest = financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='SHARED_RELEASE', binding=cfg.binding.json(), owner=owner.json(),
                       version=version, cut=digest, command=body)
        results, counts = [], []
        before = await financial.balance(cfg.binding)
        for name in ('shared_release_command', 'test_prior_release_command'):
            transaction = c.transaction()
            await transaction.start()
            try:
                await c.fetchval("SELECT setval('life_financial.test_release_carry_count',0,false)")
                try:
                    result = json.loads(await c.fetchval(f'SELECT life_financial.{name}($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                                                         cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()),
                                                         version, digest, fingerprint(request), json.dumps(body)))
                    count = await c.fetchval("SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_release_carry_count")
                    # Force every real deferred trigger, including the new
                    # post-write proofs, before rolling back this comparison.
                    await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    assert not result['trading_permission'] and result['released']
                    assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 1
                    results.append(result)
                    counts.append(count)
                except asyncpg.RaiseError as error:
                    results.append(error.message)
            finally:
                await transaction.rollback()
        assert results[0] == results[1]
        if fault == 'none':
            assert counts[0] > 0 and counts[1] == counts[0] + 1
        else:
            assert isinstance(results[0], str) and results[0].startswith('LIFE_')
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
        assert await financial.balance(cfg.binding) == before
    finally:
        await financial.close()
