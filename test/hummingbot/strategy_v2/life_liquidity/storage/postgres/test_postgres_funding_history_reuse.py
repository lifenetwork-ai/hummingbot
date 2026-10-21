"""Compare every history relation with the independent complete bundle check."""
import json
from copy import deepcopy
from dataclasses import asdict
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.shared_capital import _json


@pytest.mark.asyncio
async def test_funding_history_only_check_preserves_original_failures(database):
    c = database[0]
    assert await c.fetchval("SELECT to_regprocedure('life_financial.funding_prior_bundle_reason(jsonb,jsonb,bigint)') IS NOT NULL")
    financial, cfg, _, _, _, _, policy, feed, _ = await setup(database, refresh_initial_facts=True)
    try:
        p = _json(asdict(policy))
        cp = json.loads(await c.fetchval('SELECT body::text FROM life_financial.shared_capital_policies WHERE account_id=$1', cfg.account_id))
        bundle = _json(asdict(feed[0]))
        at = bundle['account']['snapshot']['observed_at_ms']
        cases = [(bundle, None), (bundle, bundle)]
        # Both valid and malformed predecessors, payment membership/value,
        # coverage/settlement, all five sequence/time/mutation comparisons.
        for stream in ('account', 'mark', 'index', 'funding', 'terms'):
            for field, value in (('sequence', 2), ('observed_at_ms', at + 1), ('sequence', 'bad')):
                prior = deepcopy(bundle)
                target = prior[stream]['snapshot'] if stream == 'account' else prior[stream]
                target[field] = value
                cases.append((bundle, prior))
            prior = deepcopy(bundle)
            prior[stream]['unexpected'] = True
            cases.append((bundle, prior))
        for funding in ({'payments': [None]}, {'payments': [{'bill_id': 'missing'}]},
                        {'coverage_through_ms': at + 1}, {'settlement_ms': at - 1},
                        {'payments': {}}, {'coverage_through_ms': 'bad'}):
            prior = deepcopy(bundle)
            prior['funding'].update(funding)
            cases.append((bundle, prior))
        paid = deepcopy(bundle)
        paid['funding']['payments'] = [dict(bill_id='777', settled_at_ms=at - 1, signed_payment_quote='-.1')]
        paid['account']['included_funding_bill_ids'] = ['777']
        cases.append((paid, paid))
        advanced = deepcopy(bundle)
        for stream in ('account', 'mark', 'index', 'funding', 'terms'):
            target = advanced[stream]['snapshot'] if stream == 'account' else advanced[stream]
            target['sequence'] = 2
        cases.append((advanced, bundle))
        for changed in ('bill_id', 'settled_at_ms', 'signed_payment_quote'):
            prior = deepcopy(paid)
            prior['funding']['payments'][0][changed] = {'bill_id': '778', 'settled_at_ms': at - 2, 'signed_payment_quote': '.1'}[changed]
            cases.append((paid, prior))
        for prior in ({}, None, [], 'malformed'):
            cases.append((bundle, prior))
        for path, value in ((('funding', 'scope_complete'), False), (('mark', 'price_usdt'), 'bad'),
                            (('terms', 'tiers'), []), (('funding', 'payments'), [None])):
            bad = deepcopy(bundle)
            bad[path[0]][path[1]] = value
            cases.append((bad, bundle))
        for current, prior in cases:
            args = [json.dumps(x) for x in (p, cp, current, prior)]
            original = await c.fetchval('SELECT life_financial.carry_bundle_reason($1::jsonb,$2::jsonb,$3::jsonb,$4::jsonb,$3::jsonb#>\'{account,snapshot}\',$5,64)', *args, at)
            if (current, prior) in ((bundle, None), (bundle, bundle), (paid, paid), (advanced, bundle)):
                assert original == 'CARRY_QUALIFIED'
            # The optimized path can use the relation-only check ONLY after the
            # first complete validation has returned CARRY_QUALIFIED.
            base = await c.fetchval('SELECT life_financial.carry_bundle_reason($1::jsonb,$2::jsonb,$3::jsonb,NULL,$3::jsonb#>\'{account,snapshot}\',$4,64)', *args[:3], at)
            actual = base
            if base == 'CARRY_QUALIFIED':
                actual = await c.fetchval('SELECT life_financial.funding_prior_bundle_reason($1::jsonb,$2::jsonb,$3)', args[2], args[3], at)
            assert actual == original, (current, prior, original, actual)
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.funding_prior_bundle_reason(jsonb,jsonb,bigint)','EXECUTE')", role)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_funding_keeps_complete_prior_checks_with_one_fewer_full_bundle_walk(database):
    c = database[0]
    values, repo, command = await funding_setup(database)
    financial, cfg, owner = values[:3]
    try:
        result = await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED'
        row = await c.fetchrow('SELECT * FROM life_financial.funding_observations WHERE account_id=$1', cfg.account_id)
        assert row['previous_carry_operation'] is not None
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.funding_bundle_reason(uuid,jsonb,text,text,bigint)'::regprocedure)")
        needle = 'life_financial.funding_prior_bundle_reason(b,prior,at_ms)'
        assert definition.count(needle) == 1
        historical = definition.replace('life_financial.funding_bundle_reason(', 'life_financial.test_full_funding_reason(', 1)
        historical = historical.replace(
            needle,
            "life_financial.carry_bundle_reason(carry_policy.body,capital_policy,b,prior,b#>'{account,snapshot}',at_ms,policy_row.max_payments)")
        await c.execute(historical)
        await c.execute('REVOKE ALL ON FUNCTION life_financial.test_full_funding_reason(uuid,jsonb,text,text,bigint) FROM PUBLIC')
        await c.execute('CREATE SEQUENCE life_financial.test_bundle_walks MINVALUE 0 START 0')
        full = await c.fetchval("SELECT pg_get_functiondef('life_financial.carry_bundle_reason(jsonb,jsonb,jsonb,jsonb,jsonb,bigint,integer)'::regprocedure)")
        await c.execute(full.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_bundle_walks');", 1))
        outcomes, counts = [], []
        for name in ('funding_bundle_reason', 'test_full_funding_reason'):
            await c.fetchval("SELECT setval('life_financial.test_bundle_walks',0,false)")
            bundle = row['bundle'] if isinstance(row['bundle'], str) else json.dumps(row['bundle'])
            args = (cfg.account_id, bundle, row['previous_funding_operation'], row['previous_carry_operation'], row['received_at_ms'])
            outcomes.append(await c.fetchval(f'SELECT life_financial.{name}($1,$2::jsonb,$3,$4,$5)', *args))
            counts.append(await c.fetchval('SELECT last_value+1 FROM life_financial.test_bundle_walks'))
        assert outcomes == ['FUNDING_QUALIFIED'] * 2
        assert counts == [1, 2]
    finally:
        await financial.close()
