"""Post-write money constraints share a proof only inside one fresh invocation."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup

import asyncpg
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'funding_receipt', 'balance', 'swap_event'])
async def test_grouped_account_money_keeps_both_original_constraints_and_failure_codes(database, fault):
    c = database[0]
    helper = 'life_financial.account_money_commit_check(uuid)'
    assert await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', helper)
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        assert (await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        await c.execute('CREATE SEQUENCE life_financial.test_capital_walks MINVALUE 0 START 0')
        original = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_capital_verified(uuid)'::regprocedure)")
        source = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.spot_capital_verified(uuid)'::regprocedure")
        body = "BEGIN PERFORM nextval('life_financial.test_capital_walks'); RETURN (" + source.strip().rstrip(';') + '); END;'
        await c.execute(original.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(source, body))
        blocks = []
        for name in ('funding_commit_guard', 'swap_fill_commit_guard'):
            source = await c.fetchval('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure($1)', 'life_financial.' + name + '()')
            label = name + '_block'
            blocks.append('<<' + label + '>>' + source.replace('NEW.account_id', 'p_account').replace('RETURN NULL;', 'EXIT ' + label + ';'))
        await c.execute('CREATE FUNCTION life_financial.test_original_money_check(p_account uuid) RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN ' + '\n'.join(blocks) + ' END; $$')
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_original_money_check(uuid) FROM PUBLIC')
        if fault == 'funding_receipt':
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"cash_delta_usdt\":\"123\"}'::jsonb WHERE account_id=$1 AND command->>'kind'='PAID_FUNDING'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        elif fault == 'balance':
            await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.balances SET total=total+1 WHERE account_id=$1 AND asset='USDT'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.balances ENABLE TRIGGER USER')
        elif fault == 'swap_event':
            await c.execute("""INSERT INTO life_financial.financial_events
                (account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES($1,'orphan-swap','SWAP','FILL','missing-trade',clock_timestamp(),repeat('0',64),0,0)""", cfg.account_id)
        outcomes, counts = [], []
        for name in ('account_money_commit_check', 'test_original_money_check'):
            await c.fetchval("SELECT setval('life_financial.test_capital_walks',0,false)")
            try:
                await c.execute(f'SELECT life_financial.{name}($1)', cfg.account_id)
                outcomes.append('allowed')
            except asyncpg.RaiseError as error:
                outcomes.append(error.message)
            counts.append(await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_capital_walks'))
        assert outcomes[0] == outcomes[1]
        if fault == 'none':
            assert outcomes == ['allowed', 'allowed'] and counts == [1, 2]
        else:
            assert outcomes[0] != 'allowed'
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_grouped_money_without_funding_or_swap_policy_keeps_empty_history_checks(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
        setup as shared_setup,
    )
    financial, cfg, *_ = await shared_setup(database)
    try:
        await database[0].execute('SELECT life_financial.account_money_commit_check($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.funding_verified($1) AND life_financial.swap_accounting_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_accounting_policies') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_policies') == 0
    finally:
        await financial.close()
