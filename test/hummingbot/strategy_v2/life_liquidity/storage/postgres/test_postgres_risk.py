"""Native safety uses accounted NAV; no caller-supplied drawdown or permission."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import capital_store, nav
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def risk_store(database, *, min_margin='1', max_drawdown='1000', probe_campaign='2', financial_values=None):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, at, ms = await capital_store(database, flows=(), financial_values=financial_values)
    # Explicit synthetic policy; none of these values is a live default.
    await database[0].execute('''INSERT INTO life_financial.risk_service_policies VALUES(
        $1,$2,0,$3,$4,0,.25,$5,1000,1000,64,1048576)''',
                              cfg.account_id, cfg.binding.policy_hash, D(max_drawdown), D(min_margin), D(probe_campaign))
    await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return repo, cfg, owner, PgRiskRepository(repo)


async def observe(database, repo, cfg, owner, risk, operation='risk1', **changes):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import ObserveNativeRisk
    now = await database[0].fetchval('SELECT clock_timestamp()')
    command = replace(ObserveNativeRisk(operation, now, True, True, True, True), **changes)
    return await risk.observe(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_native_risk_stages_probe_then_normal_and_enforces_original_epoch_at_financial_commit(database):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        first = await observe(database, repo, cfg, owner, risk)
        assert first.state == 'DEGRADED' and first.risk_epoch == 1 and not first.trading_permission
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_RISK_PROBE_CAPACITY'):
            await repo.prepare(order(owner, risk_epoch=first.risk_epoch), cut=before.cut, owner=owner)
        probe = order(owner, risk_epoch=first.risk_epoch, quantity_base=D('.2'))
        await repo.prepare(probe, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        second = await observe(database, repo, cfg, owner, risk, 'risk2')
        assert second.state == 'NORMAL' and second.risk_epoch == 2
        with pytest.raises(StorageError, match='PG_RISK_EPOCH_CHANGED'):
            await repo.arm(probe.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('.4')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_margin_halt_is_permanent_and_good_late_observations_never_clear_it(database):
    repo, cfg, owner, risk = await risk_store(database, min_margin='11')
    try:
        before = await repo.snapshot(cfg.binding)
        receipt = await observe(database, repo, cfg, owner, risk)
        assert receipt.state == 'HALTED' and receipt.reason_code == 'MARGIN_BUFFER_BREACHED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await observe(database, repo, cfg, owner, risk, 'risk2')).state == 'HALTED'
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await repo.snapshot(cfg.binding)).budget == before.budget
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, risk_epoch=receipt.risk_epoch), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_risk_feed_failure_pauses_and_cold_readback_cannot_rearm_original_generation(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, risk = await risk_store(database)
    cold = None
    try:
        await observe(database, repo, cfg, owner, risk)
        normal = await observe(database, repo, cfg, owner, risk, 'risk2')
        assert normal.state == 'NORMAL'
        paused = await observe(database, repo, cfg, owner, risk, 'risk3', market_data_fresh=False)
        assert paused.state == 'PAUSED' and paused.reason_code == 'MARKET_DATA_STALE'
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgRiskRepository(cold)
        assert not (await restored.frame()).trading_permission
        denied = await observe(database, cold, cfg, owner, restored, 'risk4')
        assert denied.state == 'PAUSED' and denied.reason_code == 'MANUAL_REARM_REQUIRED'
        assert (await observe(database, cold, cfg, owner, restored, 'risk5')).reason_code == 'MANUAL_REARM_REQUIRED'
        with pytest.raises(StorageError):
            await cold.prepare(order(owner, risk_epoch=denied.risk_epoch), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
async def test_current_native_risk_allows_normal_prepare_and_arm_without_self_invalidating_held_funds(database):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        normal = await observe(database, repo, cfg, owner, risk, 'risk2')
        child = order(owner, risk_epoch=normal.risk_epoch)
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        await repo.arm(child.operation_id, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await repo.attempt(child.intent_id, cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['nav-changed', 'feed', 'future', 'stale', 'nonce'])
async def test_native_risk_refuses_old_permission_and_never_releases_holds(database, fault):
    from datetime import timedelta
    from uuid import uuid4
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        current = await observe(database, repo, cfg, owner, risk, 'risk2')
        if fault == 'nav-changed':
            at = await database[0].fetchval('SELECT clock_timestamp()')
            ms = int(at.timestamp() * 1000)
            await repo.observe_nav(nav(at, ms, operation_id='nav2', independent_price_usdt=D('.99')),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        elif fault in ('feed', 'future', 'stale'):
            at = await database[0].fetchval('SELECT clock_timestamp()')
            changes = dict(market_data_fresh=False) if fault == 'feed' else dict(observed_at=at + timedelta(seconds=1 if fault == 'future' else -2))
            paused = await observe(database, repo, cfg, owner, risk, 'risk3', **changes)
            assert paused.state == 'PAUSED'
            current = paused
        else:
            repo._runtime._risk_observer = uuid4()
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, risk_epoch=current.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_drawdown_halt_uses_real_accounted_nav_and_survives_price_recovery(database):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        value = await repo.observe_nav(nav(at, ms, operation_id='drop', independent_price_usdt=D('.1')),
                                       cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert value.capital.drawdown_bps > 1000
        halted = await observe(database, repo, cfg, owner, risk)
        assert halted.state == 'HALTED' and halted.reason_code == 'DRAWDOWN_LIMIT_BREACHED'
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        await repo.observe_nav(nav(at, ms, operation_id='recover-price'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await observe(database, repo, cfg, owner, risk, 'still-halted')).reason_code == halted.reason_code
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_missing_nav_records_paused_without_accepting_caller_drawdown_or_any_order(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner = await spot(database)
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,2,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        risk = PgRiskRepository(repo)
        receipt = await observe(database, repo, cfg, owner, risk)
        assert receipt.state == 'PAUSED' and receipt.reason_code == 'RISK_DATA_UNAVAILABLE'
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, risk_epoch=receipt.risk_epoch), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await risk.frame()).state == 'PAUSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_signed_new_generation_must_revalidate_native_risk_and_cannot_reuse_old_normal_receipt(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import setup, signed
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    financial, cfg, owner, keys, control = await setup(database)
    repo, cfg, owner, risk = await risk_store(database, financial_values=(financial, cfg, owner))
    cold = None
    try:
        await observe(database, repo, cfg, owner, risk)
        old = await observe(database, repo, cfg, owner, risk, 'risk2')
        at = await database[0].fetchval('SELECT clock_timestamp()')
        target = replace(owner, process_id=uuid4(), generation=uuid4(), valid_from=at)
        activate = OwnerTransition('activate', OwnerMode.ACTIVE, target)
        await control.transition_owner(activate, **await signed(database, repo, cfg, keys, activate))
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        new = PgRiskRepository(cold)
        assert (await new.frame()).risk_epoch == old.risk_epoch
        with pytest.raises(StorageError):
            await cold.prepare(order(target, risk_epoch=old.risk_epoch), cut=(await cold.snapshot(cfg.binding)).cut, owner=target)
        # A fresh generation requires fresh independent NAV; do not stretch its
        # original one-second source age to accommodate control/driver startup.
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        await cold.observe_nav(nav(at, ms, operation_id='new-owner-nav'), cut=(await cold.snapshot(cfg.binding)).cut, owner=target)
        assert (await observe(database, cold, cfg, target, new, 'new-probe')).state == 'DEGRADED'
        normal = await observe(database, cold, cfg, target, new, 'new-normal')
        assert normal.state == 'NORMAL'
        await cold.prepare(order(target, risk_epoch=normal.risk_epoch), cut=(await cold.snapshot(cfg.binding)).cut, owner=target)
    finally:
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
async def test_good_refresh_retains_permission_epoch_and_keeps_each_original_observation(database):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        before = await observe(database, repo, cfg, owner, risk, 'risk2')
        after = await observe(database, repo, cfg, owner, risk, 'risk3')
        assert before.state == after.state == 'NORMAL' and before.risk_epoch == after.risk_epoch
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == 3
        assert await database[0].fetchval('SELECT max(event_version) FROM life_financial.risk_events') == 3
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_native_risk_commit_is_original_id_recoverable_and_cold_readback_never_heals(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import ObserveNativeRisk, PgRiskRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, risk = await risk_store(database)
    killer = await asyncpg.connect(database[1])
    cold = None
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = ObserveNativeRisk('risk1', at, True, True, True, True)
        cut = (await repo.snapshot(cfg.binding)).cut
        original = repo._runtime._commit

        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await killer.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic lost risk commit reply')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await risk.observe(cmd, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgRiskRepository(cold)
        assert ((await restored.frame()).operation_id is not None) is committed
        retry = await restored.observe(cmd, cut=cut, owner=owner)
        assert retry.replayed is committed and not retry.trading_permission
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == 1
        # A committed replay retains the original runtime nonce. It cannot be
        # consumed as new permission by a cold runtime.
        with pytest.raises(StorageError):
            await cold.prepare(order(owner, risk_epoch=retry.risk_epoch), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        if cold is not None:
            await cold.close()
        await killer.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['body', 'receipt', 'epoch'])
async def test_native_risk_history_corruption_refuses_new_money_but_preserves_original_balances(database, fault):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        normal = await observe(database, repo, cfg, owner, risk, 'risk2')
        c = database[0]
        table = 'operations' if fault == 'receipt' else 'risk_events'
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        query = {'body': "UPDATE life_financial.risk_events SET body=jsonb_set(body,'{decision,state}','\"PAUSED\"') WHERE operation_id='risk2'",
                 'receipt': "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{risk_epoch}','99') WHERE operation_id='risk2'",
                 'epoch': "UPDATE life_financial.risk_events SET risk_epoch=99 WHERE operation_id='risk2'"}[fault]
        await c.execute(query)
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_RISK_UNVERIFIED'):
            await risk.frame()
        with pytest.raises(StorageError, match='PG_RISK_UNVERIFIED'):
            await repo.prepare(order(owner, risk_epoch=normal.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert before.balance == after.balance and before.budget == after.budget
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_risk_policy_history_and_calculators_are_not_runtime_writable(database):
    import asyncpg
    repo, cfg, owner, risk = await risk_store(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        for table in ('risk_service_policies', 'risk_events'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.' + table)
        for signature in ('risk_source(uuid)', 'risk_decide(jsonb,jsonb,jsonb,jsonb,bigint,jsonb,text,boolean)',
                          'risk_verified(uuid)', 'risk_admit(uuid,life_financial.operations)', 'risk_commit_guard()'):
            assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'life_financial.' + signature)
        assert (await risk.frame()).state == 'PAUSED'
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_external_native_halt_never_inherits_previous_safety_ready_reason(database):
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        assert (await observe(database, repo, cfg, owner, risk, 'risk2')).reason_code == 'SAFETY_READY'
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        halt = await observe(database, repo, cfg, owner, risk, 'risk3')
        assert halt.state == 'HALTED' and halt.reason_code == 'HALT_LATCHED'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['event', 'bytes'])
async def test_native_risk_explicit_capacity_rolls_back_without_clearing_prior_halt_or_spending_money(database, fault):
    repo, cfg, owner, at, ms = await capital_store(database, flows=())
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,11,0,.25,2,1000,1000,$3,$4)',
                                  cfg.account_id, cfg.binding.policy_hash, 1 if fault == 'event' else 64, 1048576 if fault == 'event' else 1)
        await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        risk = PgRiskRepository(repo)
        if fault == 'event':
            assert (await observe(database, repo, cfg, owner, risk)).state == 'HALTED'
        before = await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        with pytest.raises(StorageError, match='PG_RISK_HISTORY_BOUND'):
            await observe(database, repo, cfg, owner, risk, 'over-bound')
        assert before == await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == (1 if fault == 'event' else 0)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['risk', 'prepare'])
async def test_native_risk_rechecks_source_expiry_at_actual_commit_without_widening_freshness(database, monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import ObserveNativeRisk
    repo, cfg, owner, risk = await risk_store(database)
    try:
        if stage == 'prepare':
            await observe(database, repo, cfg, owner, risk)
            normal = await observe(database, repo, cfg, owner, risk, 'risk2')
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        # Actual fresh native value, followed by a COMMIT after its original age.
        await repo.observe_nav(nav(at, ms, operation_id='last-nav'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        if stage == 'prepare':
            normal = await observe(database, repo, cfg, owner, risk, 'risk3')
        original = repo._runtime._commit
        reached = False

        async def expired(tx):
            nonlocal reached
            reached = True
            await tx._connection.execute('SELECT pg_sleep(greatest(0,extract(epoch FROM ($1::timestamptz-clock_timestamp())))+.01)', at + __import__('datetime').timedelta(milliseconds=1000))
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', expired)
        before = await repo.snapshot(cfg.binding)
        if stage == 'risk':
            command = ObserveNativeRisk('expire-at-commit', at, True, True, True, True)
            with pytest.raises(StorageError, match='PG_RISK_SOURCE_STALE_AT_COMMIT'):
                await risk.observe(command, cut=before.cut, owner=owner)
        else:
            with pytest.raises(StorageError, match='PG_RISK_(VALUE_CHANGED_OR_STALE|PERMISSION_REQUIRED)'):
                await repo.prepare(order(owner, risk_epoch=normal.risk_epoch), cut=before.cut, owner=owner)
        assert reached
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_safety_decision_matches_file_stability_and_breach_boundaries(database, tmp_path):
    import json
    from datetime import datetime, timezone

    from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
    repo, cfg, owner, risk = await risk_store(database)
    try:
        policy = json.loads(await database[0].fetchval('SELECT to_jsonb(p) FROM life_financial.risk_service_policies p'))
        policy['stable_data_ms'] = 100
        file = SafetyGate(tmp_path / 'safety.json', max_drawdown_bps=D('1000'), min_margin_buffer_quote=D('1'), stable_data_ms=100, recovery_probe_base=D('.25'))
        file.initialize_empty()
        previous = None
        # Independent File comparison, including stability reset and exact
        # inclusive financial thresholds before the permanent HALT transition.
        for offset, market, drawdown, margin in [(0, True, '0', '10'), (99, True, '0', '10'), (100, True, '0', '10'),
                                                 (200, True, '1000', '1'), (250, False, '0', '10'), (300, True, '0', '10'),
                                                 (400, True, '0', '10'), (500, True, '1000.0001', '10'), (600, True, '0', '10')]:
            at_ms = 1700000000000 + offset
            command = dict(observed_at=datetime.fromtimestamp(at_ms / 1000, timezone.utc).isoformat(), market_data_fresh=market, latency_ok=True, account_ready=True, model_ready=True)
            source = dict(drawdown_bps=drawdown, margin_buffer_quote=margin)
            raw = await database[0].fetchval('SELECT life_financial.risk_decide($1::jsonb,$2::jsonb,$3::jsonb,$4::jsonb,$5,$6::jsonb,$7,false)',
                                             json.dumps(policy), json.dumps(previous) if previous is not None else None, json.dumps(source), json.dumps(command), at_ms, json.dumps(owner.json()), str(repo._runtime._risk_observer))
            current = json.loads(raw)
            expected = file.evaluate(SafetyObservation(at_ms, market, True, True, True, D(drawdown), D(margin)))
            assert (current['state'], current['reason_code']) == (expected.state, expected.reason_code)
            previous = current
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_portfolio_risk_claim_refresh_prepare_and_arm_share_one_permission_epoch(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh, setup
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.core.data_type.common import PositionAction, TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
    values, portfolio, fills, original_fill = await setup(database)
    financial, cfg, owner = values[:3]
    swap = portfolio.swap
    capital = values[5]
    try:
        opening = await database[0].fetchval('SELECT risk_epoch FROM life_financial.accounts')
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,$3,1000,1,0,.25,2,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash, opening)
        risk = PgRiskRepository(financial)
        await refresh(database, values)
        await portfolio.measure(MeasurePortfolio('risk-nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await observe(database, financial, cfg, owner, risk)
        normal = await observe(database, financial, cfg, owner, risk, 'risk2')
        assert normal.state == 'NORMAL'
        candidate = replace(native_claim(owner, 'new-swap', product='SWAP', side='SELL', action='OPEN', amount='.1'), risk_epoch=normal.risk_epoch, wire_id='NewSwapWire1')
        reserved = await capital.reserve(ReserveCapital('new-claim', candidate), await capital.cut(cfg.binding), owner)
        assert reserved.decision.allowed
        # Refresh native qualified source and NAV without changing the original
        # claim's epoch. Actual native settled money/positions are rechecked.
        await refresh(database, values)
        await portfolio.measure(MeasurePortfolio('risk-nav2'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        current = await observe(database, financial, cfg, owner, risk, 'risk3')
        assert current.state == 'NORMAL' and current.risk_epoch == normal.risk_epoch
        config = OrderExecutorConfig(id=candidate.intent_id, timestamp=values[4][0].observed_at_ms / 1000,
                                     connector_name='okx_perpetual', trading_pair='LIFE-USDT', side=TradeType.SELL, position_action=PositionAction.OPEN, amount=D('.1'), price=D('1'), leverage=2,
                                     level_id='1', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        prepared = PrepareSwap.from_config('new-prepare', candidate.wire_id, config)
        await swap.prepare(prepared, cut=await swap.cut(cfg.binding), owner=owner)
        armed = await swap.arm(prepared.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
        assert armed.state == 'SEND_UNKNOWN'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_halt_process_exit_is_atomic_and_cold_state_never_clears_it(database, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, risk = await risk_store(database, min_margin='11')
    config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    child = r'''
import asyncio,json,os,sys
from datetime import datetime,timezone
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository,ObserveNativeRisk
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import nav
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for k in ('account_id','database_lineage'):cfg[k]=UUID(cfg[k])
    cfg=PgConfig(**cfg);repo=PgSpotRepository(await PgRuntime.open(cfg));owner=OwnerToken.from_json(data['owner'])
    observed=await repo.observation_cut(cfg.binding);at=observed.observed_at;ms=int(at.timestamp()*1000)
    await repo.observe_nav(nav(at,ms,operation_id='child-nav'),cut=observed.cut,owner=owner)
    risk=PgRiskRepository(repo);original=repo._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    at=datetime.now(timezone.utc)
    await risk.observe(ObserveNativeRisk('child-halt',at,True,True,True,True),cut=(await repo.snapshot(cfg.binding)).cut,owner=owner)
asyncio.run(run())
'''
    cold = None
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(json.dumps(dict(config=config, owner=owner.json(), committed=committed)).encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgRiskRepository(cold)
        view = await restored.frame()
        assert (view.state == 'HALTED') is committed and not view.trading_permission
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts') is committed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == (1 if committed else 0)
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        with pytest.raises(StorageError):
            await cold.prepare(order(owner, risk_epoch=view.risk_epoch or 1), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_halt_caller_cancel_retains_physical_work_and_never_heals_admission(database, monkeypatch, committed):
    import asyncio
    repo, cfg, owner, risk = await risk_store(database, min_margin='11')
    entered, finish = asyncio.Event(), asyncio.Event()
    original = repo._runtime._commit

    async def held(tx):
        if committed:
            await original(tx)
        entered.set()
        await finish.wait()
        if not committed:
            await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', held)
    try:
        task = asyncio.create_task(observe(database, repo, cfg, owner, risk))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo._runtime._tasks and not repo._runtime.healthy
        finish.set()
        await asyncio.wait_for(asyncio.gather(*tuple(repo._runtime._tasks)), 2)
        assert (await risk.frame()).state == 'HALTED' and not repo._runtime.healthy
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        finish.set()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('privilege', ['command', 'frame'])
async def test_enabled_native_risk_requires_its_explicit_role_grants_on_every_primary_admission(database, privilege):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import NATIVE_RISK_ENTRYPOINT
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    repo, cfg, owner, risk = await risk_store(database)
    try:
        signature = NATIVE_RISK_ENTRYPOINT if privilege == 'command' else 'life_financial.risk_frame(uuid)'
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + signature + ' FROM life_pg2_runtime')
        with pytest.raises(StorageError, match='PG_RISK_ROLE_PROFILE_INVALID'):
            await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_STARTUP_UNQUALIFIED'):
            await PgRuntime.open(cfg)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_events') == 0
        assert await database[0].fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == 10
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_halt_keeps_armed_order_accountable_for_late_fill_fee_and_ack(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
    repo, cfg, owner, risk = await risk_store(database)
    try:
        await observe(database, repo, cfg, owner, risk)
        normal = await observe(database, repo, cfg, owner, risk, 'risk2')
        child = order(owner, risk_epoch=normal.risk_epoch)
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm(child.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        await repo.observe_nav(nav(at, ms, operation_id='drop', independent_price_usdt=D('.1')),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await observe(database, repo, cfg, owner, risk, 'halt')).state == 'HALTED'
        before = await repo.snapshot(cfg.binding)
        at = (await repo.observation_cut(cfg.binding)).observed_at
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert after.balance.total_base_life == 6 and after.balance.total_quote_usdt == D('8.98')
        assert after.budget.campaign_execution_loss_usdt == before.budget.campaign_execution_loss_usdt + D('.12')
        assert after.balance.reserved_quote_usdt > 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await risk.frame()).state == 'HALTED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
    finally:
        await repo.close()
