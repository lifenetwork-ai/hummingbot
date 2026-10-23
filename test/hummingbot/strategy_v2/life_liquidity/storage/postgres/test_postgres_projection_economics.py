"""Causal loss and held-capital obligations over original native commands."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import (
    test_postgres_fill_projection as original_prefix,
    test_postgres_lifecycle as original_lifecycle,
    test_postgres_spot_observations as original_observations,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_fill_projection import (
    budgets,
    prefix,
    ready,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def check_holds(c, account):
    rows = await c.fetch('''SELECT r.reservation_id,r.remaining,r.amount,r.state,p.remaining AS proof_remaining,
        p.amount AS proof_amount,p.state AS proof_state FROM life_financial.reservations r
        LEFT JOIN fill_projection_spike.hold_proof p USING(account_id,reservation_id)
        WHERE r.account_id=$1''', account)
    assert rows
    for r in rows:
        assert (r['remaining'], r['amount'], r['state']) == (r['proof_remaining'], r['proof_amount'], r['proof_state'])


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('asset', ['LIFE', 'USDT'])
async def test_projection_economics_loss_scopes_and_partial_holds_use_original_fill(database, side, asset):
    repo, cfg, owner = await ready(database, economics=True, side=side)
    c = database[0]
    try:
        await check_holds(c, cfg.account_id)
        at = await c.fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at, side=side)
        reference = D('.9') if side == 'BUY' else D('1.1')
        cmd = replace(cmd, fills=(replace(cmd.fills[0], fee_currency=asset, independent_price_usdt=reference),))
        result = await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED'
        proof = await c.fetchrow('SELECT * FROM fill_projection_spike.loss_proof WHERE account_id=$1', cfg.account_id)
        assert proof['amount'] == result.execution_loss_usdt
        assert proof['session_key'] == str(owner.session_id)
        assert proof['campaign_key'] == 'synthetic'
        assert proof['day_key'] == at.strftime('%Y-%m-%d')
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.operation_proof WHERE account_id=$1', cfg.account_id) == await c.fetchval('SELECT count(*) FROM life_financial.operations WHERE account_id=$1', cfg.account_id)
        await check_holds(c, cfg.account_id)
        view = await repo.snapshot(cfg.binding)
        head = await c.fetchrow('SELECT * FROM fill_projection_spike.fill_head WHERE account_id=$1', cfg.account_id)
        assert (head['filled'], head['paid_base'], head['paid_quote'], head['loss']) == (
            D(1), D('.02') if asset == 'LIFE' else D(0), D('.02') if asset == 'USDT' else D(0), result.execution_loss_usdt)
        assert view.recovery_required  # Original SEND_UNKNOWN is still retained.
        await repo.acknowledge(ack(owner, at, side=side), cut=view.cut, owner=owner)
        await check_holds(c, cfg.account_id)
        for b in await budgets(c, cfg.account_id):
            assert (b['committed'], b['incurred']) == (b['committed_after'], b['incurred_after'])
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['DAY', 'SESSION', 'CAMPAIGN'])
async def test_projection_economics_missing_original_loss_event_aborts_before_commit(database, monkeypatch, scope):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        original = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_charge_loss(uuid,uuid,timestamptz,text,numeric)'::regprocedure)")
        needle = "FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP"
        assert original.count(needle) == 1
        await c.execute(original.replace(needle, needle + f" IF scope_name='{scope}' THEN CONTINUE; END IF;"))
        before = await repo.snapshot(cfg.binding)
        reached = []

        async def physical_commit(tx):
            reached.append(True)
            raise AssertionError('The causal FK must reject missing loss before COMMIT')

        monkeypatch.setattr(repo._runtime, '_commit', physical_commit)
        with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
            await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=before.cut, owner=owner)
        assert not reached
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.loss_proof') == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table,change', [
    ('operations', "receipt=jsonb_set(receipt,'{state}','\"invented\"'::jsonb)"),
    ('operations', "command=jsonb_set(command,'{command,exchange_order_id}','\"999\"'::jsonb)"),
    ('spot_preparations', 'fee_quote=fee_quote+1'),
    ('reservations', 'remaining=remaining+.01'),
    ('spot_accounting_progress', 'filled_base=0'),
])
async def test_projection_economics_original_receipt_basis_and_hold_edits_are_rejected(database, table, change):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises((asyncpg.ForeignKeyViolationError, asyncpg.CheckViolationError)):
            async with c.transaction():
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(f'UPDATE life_financial.{table} SET {change} WHERE account_id=$1', cfg.account_id)
        await check_holds(c, cfg.account_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_head_cannot_rewind_behind_retained_fill_even_with_progress(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        next_row = replace(cmd.fills[0], trade_id='next', signed_fee=D('.01'), independent_price_usdt=D('1.1'))
        await repo.apply_fills(replace(cmd, operation_id='next-fill', fills=(*cmd.fills, next_row), cumulative_base_life=D(2)),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute('ALTER TABLE fill_projection_spike.fill_head DISABLE TRIGGER USER')
                await c.execute('ALTER TABLE life_financial.spot_accounting_progress DISABLE TRIGGER USER')
                await c.execute('ALTER TABLE life_financial.reservations DISABLE TRIGGER USER')
                await c.execute('''UPDATE fill_projection_spike.fill_head SET ordinal=1,filled=1,paid_base=0,paid_quote=.02,loss=.12''')
                await c.execute('''UPDATE life_financial.spot_accounting_progress SET filled_base=1,paid_base=0,paid_quote=.02,execution_loss=.12''')
                await c.execute('''UPDATE fill_projection_spike.hold_proof SET filled=1,paid_base=0,paid_quote=.02,loss=.12,
                    remaining=CASE WHEN reservation_id LIKE '%principal' THEN 1 WHEN asset='LIFE' THEN .1 ELSE .18 END''')
                await c.execute('''UPDATE life_financial.reservations SET remaining=CASE WHEN reservation_id LIKE '%principal' THEN 1 WHEN asset='LIFE' THEN .1 ELSE .18 END''')
        assert await c.fetchval('SELECT ordinal FROM fill_projection_spike.fill_head') == 2
        await check_holds(c, cfg.account_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['pair', 'unknown_before', 'unknown_after', 'rebate', 'overrun',
                                      'abort_buy', 'abort_sell', 'zero_buy', 'zero_sell', 'cancel', 'late'])
async def test_projection_economics_original_complete_scenarios(database, monkeypatch, scenario):
    # Exercise the exact existing functional scenarios with the extension
    # enrolled before native account creation. They retain their assertions,
    # real transactions, original IDs and original command clocks.
    install = original_prefix.install

    async def enrolled(c, *, sources=True, economics=False, wallet=True):
        return await install(c, sources=sources, economics=True, wallet=wallet)

    monkeypatch.setattr(original_prefix, 'install', enrolled)
    if scenario == 'pair':
        await original_prefix.test_fill_projection_completed_native_pair_keeps_release_spend_and_recorder(database)
    elif scenario.startswith('unknown'):
        await original_prefix.test_fill_projection_unknown_commit_cold_retry_applies_once(database, monkeypatch, scenario == 'unknown_after')
    elif scenario == 'rebate':
        await original_prefix.test_fill_projection_real_fills_fees_rebates_and_replay_match_native(database, 'LIFE', 'SELL')
    else:
        await install(database[0], economics=True)
        if scenario == 'overrun':
            await original_observations.test_fee_overrun_books_funding_deficit_without_releasing_other_order_holds(database)
        elif scenario.startswith('abort'):
            await original_lifecycle.test_abort_proven_unsent_releases_holds_once_and_preserves_identity(database, scenario.split('_')[1].upper())
        elif scenario.startswith('zero'):
            await original_lifecycle.test_zero_fill_terminal_releases_only_reconciled_holds(database, scenario.split('_')[1].upper())
        elif scenario == 'cancel':
            await original_lifecycle.test_unknown_send_cannot_abort_and_cancel_retains_money_and_budget(database)
        else:
            await original_observations.test_original_actor_can_account_after_owner_expiry_but_cannot_allocate(database, 'FILL')
    c = database[0]
    account = await c.fetchval('SELECT account_id FROM life_financial.accounts')
    await check_holds(c, account)
    assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.dirty_intents') == 0
    for b in await budgets(c, account):
        assert (b['committed'], b['incurred']) == (b['committed_after'], b['incurred_after'])


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['floor', 'receipt'])
async def test_projection_economics_wrong_original_floor_or_receipt_is_not_published(database, fault):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_observe(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure)")
        if fault == 'floor':
            needle, replacement = "op||':floor:'||scope_name", "op||':missing-floor:'||scope_name"
        else:
            needle, replacement = "'execution_loss_usdt',new_loss::text", "'execution_loss_usdt',(new_loss+1)::text"
        assert definition.count(needle) == 1
        await c.execute(definition.replace(needle, replacement))
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
            await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.intent_prefix WHERE ordinal>0') == 0
        assert await c.fetchval("SELECT count(*) FROM fill_projection_spike.subsidy_obligation WHERE role='FLOOR'") == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table,trigger', [
    ('life_financial.operations', 'prototype_operation'), ('life_financial.operations', 'prototype_subsidy'),
    ('fill_projection_spike.fill_source', 'prototype_intent_fill'), ('life_financial.reservations', 'prototype_dirty_hold'),
])
async def test_projection_economics_skipped_capture_is_an_atomic_refusal(database, table, trigger):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        before = await repo.snapshot(cfg.binding)
        await c.execute(f'ALTER TABLE {table} DISABLE TRIGGER {trigger}')
        with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
            await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=before.cut, owner=owner)
        await c.execute(f'ALTER TABLE {table} ENABLE TRIGGER {trigger}')
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
        await check_holds(c, cfg.account_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_opening_wallet_still_requires_original_capital_audit(database, request):
    repo, cfg, owner = await ready(database, economics=True, wallet=False)
    c = database[0]
    try:
        await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                        cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        old = await prefix(c, cfg.account_id)
        assert await c.fetchval('SELECT life_financial.spot_capital_verified($1)', cfg.account_id)
        async with c.transaction():
            await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.balances SET total=total+1 WHERE account_id=$1 AND asset='USDT'", cfg.account_id)
            # Prefixes bind the event delta, not its opening basis or complete
            # wallet. This real counterexample is a mandatory promotion block.
            assert await prefix(c, cfg.account_id) == old
            assert not await c.fetchval('SELECT life_financial.spot_capital_verified($1)', cfg.account_id)
            request.node.user_properties.append(('unbound_wallet_counterexample', 'WITHOUT_WALLET_ENROLLMENT_NATIVE_CAPITAL_AUDIT_IS_REQUIRED'))
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['total', 'reserved'])
async def test_projection_economics_bound_wallet_rejects_unjournaled_balance_change(database, field):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises((asyncpg.ForeignKeyViolationError, asyncpg.CheckViolationError)):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
                await c.execute(f"UPDATE life_financial.balances SET {field}={field}+1 WHERE account_id=$1 AND asset='USDT'", cfg.account_id)
        assert await c.fetchval('SELECT total FROM fill_projection_spike.wallet_proof WHERE asset=\'USDT\'') == D('8.98')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_wallet_cannot_rewind_behind_retained_money(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
                await c.execute('''UPDATE fill_projection_spike.wallet_proof SET total=opening_total,money_ordinal=0,base_after=0,quote_after=0''')
                await c.execute("UPDATE life_financial.balances SET total=CASE asset WHEN 'LIFE' THEN 5 ELSE 10 END")
        assert await c.fetchval('SELECT min(money_ordinal) FROM fill_projection_spike.wallet_proof') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_missing_wallet_publication_aborts_even_zero_delta_ack(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        before = await repo.snapshot(cfg.binding)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER zz_prototype_wallet')
        with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
            await repo.acknowledge(ack(owner, await c.fetchval('SELECT clock_timestamp()')), cut=before.cut, owner=owner)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER zz_prototype_wallet')
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await c.fetchval('SELECT max(ordinal) FROM fill_projection_spike.money_frontier') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_imported_opening_policy_must_match_original_wallet(database):
    repo, cfg, owner = await ready(database, economics=True)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,11,1,$3,'100',4096)",
                                      cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['committed', 'incurred', 'budget_limit'])
async def test_projection_economics_materialized_budget_cannot_change_without_event(database, field):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.budget_buckets DISABLE TRIGGER USER')
                assert await c.execute(f"UPDATE life_financial.budget_buckets SET {field}={field}+1 WHERE account_id=$1 AND family='SUBSIDY'", cfg.account_id) != 'UPDATE 0'
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.dirty_budgets') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_budget_cannot_rewind_behind_spent_events(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.budget_buckets DISABLE TRIGGER USER')
                await c.execute("UPDATE fill_projection_spike.budget_current SET committed=0,incurred=0,ordinal=0 WHERE family='LOSS'")
                await c.execute("UPDATE life_financial.budget_buckets SET committed=0,incurred=0 WHERE family='LOSS'")
        assert await c.fetchval("SELECT min(incurred) FROM fill_projection_spike.budget_current WHERE family='LOSS'") == D('.12')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_missing_budget_publication_aborts_entire_fill(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        before = await repo.snapshot(cfg.binding)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER zzz_prototype_budget')
        with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
            await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')), cut=before.cut, owner=owner)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER zzz_prototype_budget')
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.loss_proof') == 0
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.dirty_budgets') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_economics_unknown_hold_cannot_be_forged_terminal_in_both_projections(database):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.reservations DISABLE TRIGGER USER')
                await c.execute("UPDATE life_financial.reservations SET state='TERMINAL',remaining=0")
                await c.execute("UPDATE fill_projection_spike.hold_proof SET state='TERMINAL',remaining=0,attempt_state='TERMINAL'")
        assert await c.fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") == D('2.2')
    finally:
        await repo.close()
