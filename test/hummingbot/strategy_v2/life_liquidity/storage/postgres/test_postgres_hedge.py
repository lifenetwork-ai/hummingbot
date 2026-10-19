"""Native hedge decisions consume actual account/carry; all inputs synthetic."""
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.hedge import HedgePolicy
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeExecutionPolicy, HedgeMarket
from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import (
    HedgeChild,
    PgHedgeRepository,
    PlanHedge,
    UpdateHedgeTarget,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


async def setup(database, *, target='0', max_plans=128, max_targets=64, max_bytes=1048576, carry_options=None, **changes):
    values = await carry_setup(database, max_age_ms=5000, refresh_initial_facts=True, **(carry_options or {}))
    financial, cfg, owner, cp, feed, capital, carry_policy, carry_feed, carry = values
    sp = dict(controller_id='main', tick_size_usdt='0.01', min_size_contracts='0.1', max_quote_levels=8, max_capture_age_ms=5000)
    await database[0].execute('INSERT INTO life_financial.swap_policies VALUES($1,$2,$3::jsonb,$4)', cfg.account_id, cfg.binding.policy_hash, json.dumps(sp), fingerprint(sp))
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, owner.valid_from - timedelta(seconds=1))
    await database[0].execute('INSERT INTO life_financial.swap_fill_policies VALUES($1,$2,0,0,0,0,64,128,500)', cfg.account_id, cfg.binding.policy_hash)
    hp = HedgePolicy('LIFE-USDT-SWAP', D('.1'), D('.25'), D('2'), D('3'), 1000, 3, D('1'), D('100'), D('.001'))
    policy = replace(HedgeExecutionPolicy(hp, D(target), D('5'), 5000, 'bounded_maker_and_pause'), **changes)
    body = _json(asdict(policy))
    await database[0].execute('INSERT INTO life_financial.hedge_policies VALUES($1,$2,$3::jsonb,$4,$5,$6,$7)',
                              cfg.account_id, cfg.binding.policy_hash, json.dumps(body), fingerprint(body), max_targets, max_plans, max_bytes)
    # Multi-child settlement spans the carry fixture's 10-second payment date.
    # Use an explicit future synthetic schedule; no source-age budget changes.
    funding = carry_feed[0].funding
    carry_feed[0] = replace(carry_feed[0], funding=replace(funding, settlement_ms=funding.observed_at_ms + 3600000,
                                                           next_settlement_ms=funding.observed_at_ms + 5 * 3600000))
    assert (await carry.check_carry(cfg.binding)).allowed
    at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
    market = HedgeMarket('12345', 'LIFE-USDT-SWAP', 1, at, ((D('.99'), D('100')),), ((D('1.01'), D('100')),),
                         D('.001'), D('0'), D('10'), True, 'okx_depth_fee')
    return values, PgHedgeRepository(PgSwapRepository(capital)), market, policy


@pytest.mark.asyncio
async def test_plan_uses_actual_residual_native_carry_and_conservative_cost_without_creating_money(database):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        command = PlanHedge.capture('plan1', market)
        before = await financial.balance(cfg.binding)
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await repo.plan(command, cut=cut, owner=owner)
        assert result.decision['side'] == 'SELL' and result.decision['position_action'] == 'OPEN'
        assert D(result.decision['quantity_base']) == 2 and D(result.decision['price_usdt']) == D('1.01')
        assert D(result.decision['estimated_cost_quote']) > D('.02')
        assert result.decision['urgent'] and not result.decision['allow_spot_risk_increase']
        assert not result.trading_permission and result.child_operation_id is None
        assert (await repo.plan(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup('plan1', cfg.binding)).replayed
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_target_versions_are_permanent_and_balanced_plan_preserves_all_budgets(database):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        command = UpdateHedgeTarget('target1', 0, D('5'))
        assert not (await repo.update_target(command, cut=cut, owner=owner)).trading_permission
        assert (await repo.update_target(command, cut=cut, owner=owner)).replayed
        with pytest.raises(StorageError, match='(OPERATION_CONFLICT|HEDGE_TARGET)'):
            await repo.update_target(replace(command, target_base=D('4')), cut=cut, owner=owner)
        with pytest.raises(StorageError, match='HEDGE_TARGET_VERSION'):
            await repo.update_target(UpdateHedgeTarget('stale', 0, D('4')), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.plan(PlanHedge.capture('balanced', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.decision['reason_code'] == 'HEDGE_DEADBAND' and result.decision['allow_spot_risk_increase']
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.budget_events') == 0
    finally:
        await financial.close()


async def child(database, values, *, key='one', amount='2', price='1.01', side=None, action=None):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.core.data_type.common import PositionAction, TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
    side, action = side or TradeType.SELL, action or PositionAction.OPEN
    owner = values[2]
    at = await database[0].fetchval('SELECT clock_timestamp()')
    cfg = OrderExecutorConfig(id='hedge-' + key, timestamp=at.timestamp(), controller_id='main', connector_name='okx_perpetual',
                              trading_pair='LIFE-USDT', side=side, position_action=action, amount=D(amount), price=D(price),
                              leverage=2, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    claim = replace(native_claim(owner, cfg.id, product='SWAP', side=side.name, action=action.name, amount=amount, price=price), wire_id='HedgeWire' + key)
    return HedgeChild('hedge-cap:' + key, 'hedge-carry:' + key, PrepareSwap.from_config('hedge-prepare:' + key, claim.wire_id, cfg), claim)


@pytest.mark.asyncio
async def test_child_claim_carry_preparation_and_gross_cost_publish_together_once(database):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        command = PlanHedge.capture('plan1', market, child=captured)
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await repo.plan(command, cut=cut, owner=owner)
        assert result.child_state == 'PREPARED', await repo.frame(cfg.binding)
        assert result.child_operation_id == captured.prepare.operation_id
        assert (await repo.plan(command, cut=cut, owner=owner)).replayed
        assert (await repo.swap.lookup(captured.prepare.operation_id, cfg.binding)).state == 'PREPARED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts") == 'PREPARED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
        next_plan = await repo.plan(PlanHedge.capture('again', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert next_plan.decision['reason_code'] == 'HEDGE_IN_FLIGHT'
        assert not next_plan.decision['allow_spot_risk_increase']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
        with pytest.raises(StorageError, match='HEDGE_TARGET_PENDING'):
            await repo.update_target(UpdateHedgeTarget('target', 0, D('5')), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'price', 'direction'])
async def test_child_cannot_overhedge_or_replace_recomputed_cost_geometry(database, fault):
    from hummingbot.core.data_type.common import TradeType
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values, **dict(amount='3') if fault == 'amount' else dict(price='1.02') if fault == 'price' else dict(side=TradeType.BUY))
        with pytest.raises(StorageError, match='HEDGE_CHILD_MISMATCH'):
            await repo.plan(PlanHedge.capture('bad', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
        assert len(await values[5].claims(cfg.binding)) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,reason', [('crossed', 'HEDGE_DEPTH_CROSSED'), ('shallow', 'HEDGE_DEPTH_INSUFFICIENT'),
                                          ('fee', 'HEDGE_COST_OR_NET_EDGE_LIMIT'), ('edge', 'HEDGE_COST_OR_NET_EDGE_LIMIT')])
async def test_native_depth_fee_funding_and_net_edge_bounds_deny_child_without_inventing_cash(database, fault, reason):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        if fault == 'crossed':
            market = replace(market, bids=((D('1.02'), D('100')),))
        elif fault == 'shallow':
            market = replace(market, bids=((D('.99'), D('.1')),))
        elif fault == 'fee':
            market = replace(market, fee_rate=D('1'))
        else:
            market = replace(market, available_edge_quote=D('0'))
        result = await repo.plan(PlanHedge.capture('blocked', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.decision['reason_code'] == reason
        assert not result.decision['allow_spot_risk_increase'] and not result.trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_installed_hedge_policy_denies_generic_swap_claim_and_unbalanced_spot_prepare(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        with pytest.raises(StorageError, match='HEDGE_CHILD_CONTEXT_REQUIRED'):
            await values[5].reserve(ReserveCapital('bypass', native_claim(owner, 'bypass', product='SWAP', side='SELL', amount='2')),
                                    await values[5].cut(cfg.binding), owner)
        await repo.plan(PlanHedge.capture('unbalanced', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        claim = replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')
        assert (await values[5].reserve(ReserveCapital('spot-cap', claim), await values[5].cut(cfg.binding), owner)).decision.allowed
        assert (await values[8].check_carry(cfg.binding)).allowed
        with pytest.raises(StorageError, match='HEDGE_SPOT_PAUSED'):
            await financial.prepare(order(owner, risk_epoch=1), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fee', ['.001', '.1'])
async def test_native_partial_settlement_retains_cost_hold_and_blocks_second_child(database, fee):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
        FillSwap,
        PgSwapFillRepository,
        SwapFillObservation,
    )
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        root = await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert root.child_state == 'PREPARED'
        await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
        await repo.swap.acknowledge(captured.prepare.operation_id, exchange_order_id='12345678', owner=owner)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        trade = SwapFillObservation('hedge-trade1', D('.5'), D('1.01'), 'USDT', -D(fee), at, D('1.01'), at, at, 'independent_market', D('0'), None)
        fill = FillSwap('hedge-fill', captured.prepare.operation_id, captured.claim.intent_id, captured.claim.wire_id,
                        'LIFE-USDT', 'SELL', str(owner.session_id), owner.epoch, '12345678', D('.5'), (trade,), now)
        assert (await PgSwapFillRepository(repo.swap).apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        account = replace(values[4][0], sequence=2, observed_at_ms=at, short_base=D('.5'), spot_cash_quote=D('10') - D(fee))
        values[4][0] = account
        assert (await values[5].check_capital(cfg.binding)).allowed
        b = values[7][0]
        values[7][0] = replace(b, account=replace(b.account, snapshot=account), mark=replace(b.mark, sequence=2, observed_at_ms=at),
                               index=replace(b.index, sequence=2, observed_at_ms=at), funding=replace(b.funding, sequence=2, observed_at_ms=at, coverage_through_ms=at),
                               terms=replace(b.terms, sequence=2, observed_at_ms=at))
        assert (await values[8].check_carry(cfg.binding)).allowed
        cut = (await financial.observation_cut(cfg.binding)).cut
        settled = await repo.settle(SettleHedge('settle1', 'plan1'), cut=cut, owner=owner)
        assert settled.state == 'PARTIAL' and not settled.trading_permission
        assert (await repo.settle(SettleHedge('settle1', 'plan1'), cut=cut, owner=owner)).replayed
        result = await repo.plan(PlanHedge.capture('wait', replace(market, sequence=2, observed_at_ms=at)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.decision['reason_code'] == ('HEDGE_IN_FLIGHT' if fee == '.001' else 'HEDGE_CARRY_UNAVAILABLE')
        frame = await repo.frame(cfg.binding)
        assert D(frame['plans'][0]['progress']['cost_hold']) == D(root.decision['estimated_cost_quote'])
        assert await database[0].fetchval('SELECT incurred_usdt FROM life_financial.hedge_plans WHERE plan_id=$1', 'plan1') == D(fee)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('10') - D(fee)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts') is (fee == '.1')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_canceled_zero_fill_hedges_keep_gross_attempt_costs_until_retry_cap(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
        ConfirmSwapTerminal,
        PgSwapTerminalRepository,
    )
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        total_cost = D('0')
        for i in range(3):
            await refresh(database, values)
            at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
            m = replace(market, sequence=i + 1, observed_at_ms=at)
            captured = await child(database, values, key=str(i))
            root = await repo.plan(PlanHedge.capture('plan' + str(i), m, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert root.child_state == 'PREPARED'
            total_cost += D(root.decision['estimated_cost_quote'])
            await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
            await repo.swap.acknowledge(captured.prepare.operation_id, exchange_order_id=str(10000000 + i), owner=owner)
            await refresh(database, values)
            await PgPortfolioRepository(repo.swap).measure(MeasurePortfolio('nav' + str(i)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            prepared_at = await database[0].fetchval('SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id=$1', captured.prepare.operation_id)
            terminal = ConfirmSwapTerminal('terminal' + str(i), captured.prepare.operation_id, str(10000000 + i), 'canceled', D('0'), (), values[4][0].observed_at_ms, prepared_at, 'okx_joint_reconciled')
            assert (await PgSwapTerminalRepository(repo.swap).confirm(terminal, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
            source_check = await database[0].fetchrow('''SELECT life_financial.hedge_source_qualified($1,cap.operation_id,carry.operation_id,a.version,
                floor(extract(epoch FROM clock_timestamp())*1000)::bigint) AS qualified,cap.command,carry.receipt,
                life_financial.portfolio_money_at($1,a.version) AS money,life_financial.portfolio_positions_at($1,a.version) AS legs
                FROM life_financial.accounts a JOIN life_financial.shared_capital_heads h USING(account_id)
                JOIN life_financial.operations cap USING(account_id,operation_id)
                JOIN life_financial.carry_heads ch USING(account_id)
                JOIN life_financial.operations carry ON carry.account_id=a.account_id AND carry.operation_id=ch.operation_id
                WHERE a.account_id=$1''', cfg.account_id)
            assert source_check['qualified'], (i, dict(source_check))
            settled = await repo.settle(SettleHedge('settle' + str(i), 'plan' + str(i)), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
            assert settled.state == 'TERMINAL'
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        await refresh(database, values)
        result = await repo.plan(PlanHedge.capture('capped', replace(market, sequence=4, observed_at_ms=at)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.decision['reason_code'] == 'HEDGE_RETRY_CAP'
        frame = await repo.frame(cfg.binding)
        assert D(frame['plans'][-1]['progress']['cost_hold']) == total_cost
        assert frame['plans'][-1]['progress']['attempts'] == 3
        assert len(await values[5].claims(cfg.binding)) == 3  # Hedge settlement is no capital release.
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 3
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_settlements') == 3
        assert await database[0].fetchval('SELECT sum(incurred_usdt) FROM life_financial.hedge_plans') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_hedge_arm_rechecks_its_shorter_market_deadline_at_physical_commit(database, monkeypatch):
    import asyncio
    values, repo, market, policy = await setup(database, max_market_age_ms=2000)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        root = await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert root.child_state == 'PREPARED'
        original = repo.runtime._commit

        async def expired(tx):
            assert await tx._connection.fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
            at = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            await asyncio.sleep(max(0, (market.observed_at_ms + policy.max_market_age_ms - at) / 1000) + .05)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', expired)
        with pytest.raises(StorageError, match='HEDGE_ARM_REVOKED_AT_COMMIT'):
            await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_refused_child_keeps_attempt_cost_and_retires_all_captured_ids(database):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        # Geometry is valid; the native contract rejects a different connector.
        config = captured.prepare.config().model_copy(update={'connector_name': 'other'})
        from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
        captured = replace(captured, prepare=PrepareSwap.from_config(captured.prepare.operation_id, captured.prepare.client_order_id, config))
        command = PlanHedge.capture('refused', market, child=captured)
        assert PlanHedge.from_json(command.json()) == command
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await repo.plan(command, cut=cut, owner=owner)
        assert result.child_state == 'REFUSED' and not result.trading_permission
        assert (await repo.plan(command, cut=cut, owner=owner)).replayed
        frame = await repo.frame(cfg.binding)
        assert frame['plans'][0]['progress']['attempts'] == 1
        assert D(frame['plans'][0]['progress']['cost_hold']) == D(result.decision['estimated_cost_quote']) > 0
        assert len(await values[5].claims(cfg.binding)) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
        with pytest.raises(StorageError, match='HEDGE_CHILD_ID_RETIRED'):
            await repo.plan(replace(command, observation=replace(command.observation, operation_id='retry')), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        # Cross-kind reuse also fails, without a new hedge attempt or money.
        with pytest.raises(StorageError, match='HEDGE_CHILD_ID_RETIRED'):
            await repo.update_target(UpdateHedgeTarget(captured.carry_operation_id, 0, D('5')), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert len((await repo.frame(cfg.binding))['targets']) == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['rows', 'bytes'])
async def test_hedge_history_capacity_rolls_back_child_and_revokes_local_admission(database, limit):
    values, repo, market, policy = await setup(database, max_plans=1 if limit == 'rows' else 128, max_bytes=1 if limit == 'bytes' else 1048576)
    financial, cfg, owner = values[:3]
    try:
        if limit == 'rows':
            await repo.plan(PlanHedge.capture('first', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        captured = await child(database, values)
        with pytest.raises(StorageError, match='HEDGE_BOUND'):
            await repo.plan(PlanHedge.capture('full', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == int(limit == 'rows')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['decision', 'progress', 'projection', 'source'])
async def test_corrupted_hedge_history_is_detected_on_cold_read_without_refund(database, fault):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        assert (await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).child_state == 'PREPARED'
        await database[0].execute('ALTER TABLE life_financial.hedge_plan_facts DISABLE TRIGGER USER')
        if fault == 'decision':
            await database[0].execute("UPDATE life_financial.hedge_plan_facts SET decision=jsonb_set(decision,'{estimated_cost_quote}','\"0\"')")
        elif fault == 'progress':
            await database[0].execute("UPDATE life_financial.hedge_plan_facts SET progress=jsonb_set(progress,'{attempts}','0')")
        elif fault == 'source':
            await database[0].execute("UPDATE life_financial.hedge_plan_facts SET context=jsonb_set(context,'{positions,short_base}','\"2\"')")
        else:
            await database[0].execute('ALTER TABLE life_financial.hedge_plans DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.hedge_plans SET incurred_usdt=1')
        with pytest.raises(StorageError, match='HEDGE_UNVERIFIED'):
            await repo.frame(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await database[0].fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_balanced_spot_preparation_uses_hedge_barrier_and_corruption_revokes_it(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    values, repo, market, policy = await setup(database, target='5')
    financial, cfg, owner = values[:3]
    try:
        await repo.plan(PlanHedge.capture('balanced', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        claim = replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')
        assert (await values[5].reserve(ReserveCapital('spot-cap', claim), await values[5].cut(cfg.binding), owner)).decision.allowed
        assert (await values[8].check_carry(cfg.binding)).allowed
        assert (await financial.prepare(order(owner, risk_epoch=1, fee_buffer_quote_usdt=D('.002'), fee_buffer_base_life=D('0')), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'PREPARED'
        assert await database[0].fetchval('SELECT life_financial.hedge_spot_ready($1)', cfg.account_id)
        # Corrupt retained evidence; the real market-timeout COMMIT boundary
        # is exercised separately without changing any proof row.
        await database[0].execute('ALTER TABLE life_financial.hedge_plan_facts DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.hedge_plan_facts SET progress=jsonb_set(progress,'{market,observed_at_ms}',to_jsonb($1::bigint))", market.observed_at_ms - policy.max_market_age_ms - 1)
        assert not await database[0].fetchval('SELECT life_financial.hedge_spot_ready($1)', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_hedge_cost_matches_existing_file_coordinator_over_identical_depth_fee_funding(database):
    from types import SimpleNamespace

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        carry = await values[8].check_carry(cfg.binding)
        raw = await database[0].fetchrow('''SELECT p.body,cp.body AS capital,co.command#>'{command,snapshot}' AS snapshot,
            life_financial.hedge_context($1,a.version) AS context FROM life_financial.hedge_policies p
            JOIN life_financial.shared_capital_policies cp USING(account_id)
            JOIN life_financial.shared_capital_heads h USING(account_id)
            JOIN life_financial.operations co USING(account_id,operation_id)
            JOIN life_financial.accounts a USING(account_id) WHERE p.account_id=$1''', cfg.account_id)
        p, cp, snapshot, context = [json.loads(raw[k]) for k in ('body', 'capital', 'snapshot', 'context')]
        native = object.__new__(HedgeCoordinator)
        native.policy = policy
        native.controller = SimpleNamespace(_carry_monitor=SimpleNamespace(check=lambda: carry))
        for side in (TradeType.SELL, TradeType.BUY):
            for price in ('1', '.997', '1.003', '1.0000000000000000001'):
                for funding in ('0', '.03'):
                    obs = SimpleNamespace(mark_price_usdt=D(price), index_price_usdt=D('1'))
                    m = replace(market, bids=((D('.99'), D('.3')), (D('.98'), D('10'))),
                                asks=((D('1.01'), D('.4')), (D('1.02'), D('10'))), funding_cost_quote=D(funding))
                    limit, expected = native._cost(m, obs, side, D('2'))
                    snap = {**snapshot, 'mark_price_usdt': price}
                    target = '0' if side is TradeType.SELL else '7'
                    result = json.loads(await database[0].fetchval('SELECT life_financial.hedge_evaluate($1::jsonb,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,NULL,$7::numeric,$8,true)',
                                                                   json.dumps(p), json.dumps(cp), json.dumps(snap), json.dumps(_json(asdict(carry))), json.dumps(_json(asdict(m))),
                                                                   json.dumps(context), D(target), market.observed_at_ms))
                    assert result['decision']['side'] == side.name
                    assert D(result['decision']['price_usdt']) == limit
                    assert D(result['decision']['estimated_cost_quote']) == expected
        # Actual opposite inventory is closed before a new position is opened.
        context['positions']['long_base'] = '.75'
        result = json.loads(await database[0].fetchval('SELECT life_financial.hedge_evaluate($1::jsonb,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,NULL,0,$7,true)',
                                                       json.dumps(p), json.dumps(cp), json.dumps(snapshot), json.dumps(_json(asdict(carry))), json.dumps(_json(asdict(market))),
                                                       json.dumps(context), market.observed_at_ms))
        assert result['decision']['position_action'] == 'CLOSE'
        assert D(result['decision']['quantity_base']) <= D('.75')
        # A balanced account cannot bless an unowned provider pending order.
        context['positions']['long_base'] = '0'
        snapshot['pending'] = [dict(intent_id='foreign')]
        result = json.loads(await database[0].fetchval('SELECT life_financial.hedge_evaluate($1::jsonb,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,NULL,5,$7,true)',
                                                       json.dumps(p), json.dumps(cp), json.dumps(snapshot), json.dumps(_json(asdict(carry))), json.dumps(_json(asdict(market))),
                                                       json.dumps(context), market.observed_at_ms))
        assert result['decision']['reason_code'] == 'HEDGE_WAIT_SPOT_RECONCILIATION'
        assert not result['decision']['allow_spot_risk_increase']
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['plan', 'settle'])
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_crash_resolves_hedge_identity_without_resending_or_resetting_cost(database, monkeypatch, kind, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_process import exit_hedge

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageUnavailable
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        captured = await child(database, values)
        command = PlanHedge.capture('plan1', market, child=captured)
        if kind == 'settle':
            assert (await repo.plan(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).child_state == 'PREPARED'
            await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
            await repo.swap.acknowledge(captured.prepare.operation_id, exchange_order_id='12345678', owner=owner)
            command = SettleHedge('settle1', 'plan1')
        cut = (await financial.observation_cut(cfg.binding)).cut
        if fault == 'process':
            await exit_hedge(cfg, command, cut, owner, committed, kind)
        else:
            original = repo.runtime._commit

            async def killed(tx):
                state = await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id=$1", command.operation_id)
                assert state == ('PLANNED' if kind == 'plan' else 'PARTIAL')
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', tx._connection.get_server_pid())
                if not committed:
                    await original(tx)
                raise ConnectionError('synthetic lost terminated COMMIT result')

            monkeypatch.setattr(repo.runtime, '_commit', killed)
            with pytest.raises(CommitUnknown):
                await getattr(repo, kind)(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        recovered = PgHedgeRepository(PgSwapRepository(capital))
        found = await recovered.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        if committed:
            assert found.replayed and not found.trading_permission
            assert (await getattr(recovered, kind)(command, cut=cut, owner=owner)).replayed
        attempts = int(kind == 'settle' or committed)
        assert len(await capital.claims(cfg.binding)) == attempts
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == attempts
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_settlements') == int(kind == 'settle' and committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        if attempts:
            frame = await recovered.frame(cfg.binding)
            assert frame['plans'][0]['progress']['attempts'] == 1
            assert D(frame['plans'][0]['progress']['cost_hold']) > 0
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == ('ACKED' if kind == 'settle' else 'PREPARED')
    finally:
        try:
            try:
                await financial.close()
            except StorageUnavailable as error:
                if fault != 'backend' or str(error) != 'PG_SHUTDOWN_READBACK_REQUIRED':
                    raise
                assert repo.runtime._closing and not repo.runtime.healthy
                assert not repo.runtime._tasks and not any(repo.runtime._counts.values())
                assert all(pool._closed for pool in repo.runtime._pools.values())
                assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_settlements') == int(kind == 'settle' and committed)
        finally:
            if cold is not None:
                await cold.close()
            await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['owner', 'market_deadline'])
async def test_plan_rechecks_physical_commit_and_leaks_no_child_claim_or_cost(database, monkeypatch, fault):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, market, policy = await setup(database, max_market_age_ms=2000)
    financial, cfg, owner = values[:3]
    try:
        if fault == 'owner':
            await install_generation_fault(database)
        captured = await child(database, values)
        original = repo.runtime._commit

        async def changed(tx):
            assert await tx._connection.fetchval("SELECT receipt->>'child_state' FROM life_financial.operations WHERE operation_id='plan1'") == 'PREPARED'
            if fault == 'owner':
                await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            else:
                at = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
                await asyncio.sleep(max(0, (market.observed_at_ms + policy.max_market_age_ms - at) / 1000) + .05)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER' if fault == 'owner' else 'HEDGE_MARKET_EXPIRED_AT_COMMIT'):
            await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_halted_owner_can_retain_hedge_observations_but_cannot_publish_another_child(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        assert (await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).child_state == 'PREPARED'
        await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
        await repo.swap.acknowledge(captured.prepare.operation_id, exchange_order_id='12345678', owner=owner)
        await database[0].execute('UPDATE life_financial.accounts SET halted=true')
        assert (await repo.settle(SettleHedge('late', 'plan1'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'PARTIAL'
        paused = await repo.plan(PlanHedge.capture('halted', market), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert paused.decision['reason_code'] == 'HEDGE_CARRY_UNAVAILABLE' and not paused.trading_permission
        with pytest.raises(StorageError, match='HEDGE_CHILD_NOT_READY'):
            await repo.plan(PlanHedge.capture('not-permitted', market, child=await child(database, values, key='other')),
                            cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_plan_rechecks_independent_funding_schedule_at_commit_not_only_account_age(database, monkeypatch):
    import asyncio
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        b = values[7][0]
        values[7][0] = replace(b, funding=replace(b.funding, sequence=2, observed_at_ms=at, coverage_through_ms=at,
                                                  settlement_ms=at + 1500, next_settlement_ms=at + 1500 + 4 * 3600000))
        assert (await values[8].check_carry(cfg.binding)).allowed
        original = repo.runtime._commit

        async def crossed(tx):
            assert await tx._connection.fetchval("SELECT decision->>'side' FROM life_financial.hedge_plan_facts WHERE operation_id='plan1'") == 'SELL'
            now = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            await asyncio.sleep(max(0, (at + 1500 - now) / 1000) + .05)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', crossed)
        with pytest.raises(StorageError, match='HEDGE_SOURCE_EXPIRED_AT_COMMIT'):
            await repo.plan(PlanHedge.capture('plan1', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_concurrent_identical_hedge_plans_publish_one_child_and_one_cost_hold(database):
    import asyncio
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        command = PlanHedge.capture('plan1', market, child=captured)
        cut = (await financial.snapshot(cfg.binding)).cut
        results = await asyncio.gather(*(repo.plan(command, cut=cut, owner=owner) for _ in range(2)))
        assert sorted(r.replayed for r in results) == [False, True]
        assert all(r.child_state == 'PREPARED' and not r.trading_permission for r in results)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
        assert (await repo.frame(cfg.binding))['plans'][0]['progress']['attempts'] == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_hedge_reporter_can_read_scoped_facts_but_cannot_write_or_forge_private_context(database):
    from uuid import uuid4

    import asyncpg
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    reporter = await asyncpg.connect(database[1].replace('postgres@', 'life_pg2_reporter@'))
    try:
        await repo.plan(PlanHedge.capture('plan1', market), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        frame = json.loads(await reporter.fetchval('SELECT life_financial.hedge_read($1,64,1048576,NULL)', cfg.account_id))
        assert not frame['trading_permission'] and len(frame['plans']) == 1
        with pytest.raises(asyncpg.RaiseError, match='ACCOUNT_ACCESS_DENIED'):
            await reporter.fetchval('SELECT life_financial.hedge_read($1,64,1048576,NULL)', uuid4())
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('SELECT life_financial.hedge_command($1,NULL,NULL,0,NULL,NULL,NULL,NULL)', cfg.account_id)
        for principal in ('life_pg2_reporter', 'life_pg2_runtime'):
            for signature in ('life_financial.hedge_market_captured(uuid,text)', 'life_financial.hedge_context(uuid,bigint)', 'life_financial.hedge_spot_ready(uuid)', 'life_financial.hedge_verified(uuid)'):
                assert not await database[0].fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
            assert not await database[0].fetchval("SELECT has_table_privilege($1,'life_financial.hedge_staging','SELECT,INSERT,UPDATE,DELETE')", principal)
    finally:
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_failed_market_capture_cannot_implicitly_reuse_previous_good_market(database, product):
    values, repo, market, policy = await setup(database, target='5' if product == 'SPOT' else '0')
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values) if product == 'SWAP' else None
        root = await repo.plan(PlanHedge.capture('good', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert root.child_state == ('PREPARED' if product == 'SWAP' else None)
        failed = await repo.plan(PlanHedge.capture('failed', replace(market, sequence=2, scope_complete=False)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert failed.decision['reason_code'] == 'HEDGE_MARKET_UNQUALIFIED'
        if product == 'SPOT':
            assert not await database[0].fetchval('SELECT life_financial.hedge_spot_ready($1)', cfg.account_id)
        else:
            with pytest.raises(StorageError, match='HEDGE_MARKET_REVALIDATION_REQUIRED'):
                await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        # Only a new explicit, qualified capture can restore market readiness.
        # This does not clear any HALT, owner/session expiry or cost/retry state.
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        await repo.plan(PlanHedge.capture('requalified', replace(market, sequence=3, observed_at_ms=at)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        if product == 'SPOT':
            assert await database[0].fetchval('SELECT life_financial.hedge_spot_ready($1)', cfg.account_id)
        else:
            assert (await repo.swap.arm(captured.prepare.operation_id, cut=await repo.swap.cut(cfg.binding), owner=owner)).state == 'SEND_UNKNOWN'
            frame = await repo.frame(cfg.binding)
            assert all(p['progress']['attempts'] == 1 and D(p['progress']['cost_hold']) == D(root.decision['estimated_cost_quote']) for p in frame['plans'])
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_revoked_local_runtime_can_observe_but_cannot_stage_financial_hedge_child(database):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        captured = await child(database, values)
        cut = (await financial.snapshot(cfg.binding)).cut
        repo.runtime._healthy = False  # Models local revocation before SQL admission.
        with pytest.raises(StorageError, match='CLOSED_OR_UNQUALIFIED'):
            await repo.plan(PlanHedge.capture('forbidden', market, child=captured), cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 0
        observed = await repo.plan(PlanHedge.capture('diagnostic', market), cut=cut, owner=owner)
        assert not observed.trading_permission and observed.child_operation_id is None
        assert not repo.runtime.healthy
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['staging', 'history'])
async def test_private_child_prefix_proof_cannot_survive_commit_or_hide_corruption(database, monkeypatch, fault):
    values, repo, market, policy = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        # Test-only privileged fault hook; no production runtime DML grant.
        if fault == 'history':
            # Install the corruption seam before a command schedules deferred
            # FK events; ALTER TABLE during that transaction is rejected first.
            await database[0].execute('ALTER TABLE life_financial.hedge_plan_facts DISABLE TRIGGER immutable_history')
        body = ("INSERT INTO life_financial.hedge_staging SELECT account_id,pg_current_xact_id(),version,'{}' FROM life_financial.accounts WHERE account_id=p_account"
                if fault == 'staging' else "UPDATE life_financial.hedge_plan_facts SET progress=jsonb_set(progress,'{attempts}','0') WHERE account_id=p_account")
        await database[0].execute('CREATE FUNCTION life_financial.test_damage_hedge(p_account uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fault$ BEGIN ' + body + "; END $fault$; REVOKE ALL ON FUNCTION life_financial.test_damage_hedge(uuid) FROM PUBLIC; GRANT EXECUTE ON FUNCTION life_financial.test_damage_hedge(uuid) TO life_pg2_runtime")
        captured = await child(database, values)
        original = repo.runtime._commit

        async def damaged(tx):
            assert await tx._connection.fetchval("SELECT receipt->>'child_state' FROM life_financial.operations WHERE operation_id='plan1'") == 'PREPARED'
            await tx._connection.execute('SELECT life_financial.test_damage_hedge($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', damaged)
        with pytest.raises(StorageError, match='HEDGE_UNVERIFIED_AT_COMMIT'):
            await repo.plan(PlanHedge.capture('plan1', market, child=captured), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_staging') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()
