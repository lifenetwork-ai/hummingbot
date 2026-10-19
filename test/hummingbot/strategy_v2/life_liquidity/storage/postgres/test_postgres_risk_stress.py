"""Reservation-derived native stress never trusts caller inventory or netting."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk import observe, risk_store
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk_markout import refresh

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def stress_store(database, *, capacity='10', target='5', loss='100'):
    repo, cfg, owner, risk = await risk_store(database)
    await database[0].execute('INSERT INTO life_financial.risk_stress_policies VALUES($1,$2,60000,$3,$4,$5,1000)', cfg.account_id, cfg.binding.policy_hash, D(capacity), D(target), D(loss))
    await refresh(database, repo, cfg, owner, risk, 'stress-nav')
    ready = await observe(database, repo, cfg, owner, risk, 'normal')
    return repo, cfg, owner, risk, ready


@pytest.mark.asyncio
async def test_enabled_native_stress_cannot_prepare_without_original_source(database):
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_STRESS_FRESH_OBSERVATION_REQUIRED'):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


async def stress_observe(database, repo, cfg, owner, operation='stress1', **changes):
    from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotStressObservation
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import ObserveNativeStress, PgStressRepository
    now = await database[0].fetchval('SELECT clock_timestamp()'); ms = int(now.timestamp() * 1000)
    source = replace(SpotStressObservation(ms, ms, 'independent_market', D('1'), D('.8'), D('1.2'), D('100'), D('100'), D('.01'), D('.01')), **changes)
    command = ObserveNativeStress(operation, source)
    cut = (await repo.snapshot(cfg.binding)).cut
    receipt = await PgStressRepository(repo).observe(command, cut=cut, owner=owner)
    return command, cut, receipt


@pytest.mark.asyncio
async def test_native_stress_real_holds_match_file_economics_and_arm_keeps_original_reservation(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import PgStressRepository
    from hummingbot.strategy_v2.life_liquidity.stress import StressInputs, evaluate_stress
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        _, _, receipt = await stress_observe(database, repo, cfg, owner)
        assert receipt['decision']['allowed'] and receipt['trading_permission'] is False
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        child = order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch)
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await refresh(database, repo, cfg, owner, risk, 'arm-nav')
        _, _, held = await stress_observe(database, repo, cfg, owner, 'stress2')
        expected = evaluate_stress(StressInputs(D('.2'), D('0'), D('5'), D('1'), D('.8'), D('1.2'), D('1'), D('1'), D('.0436'), D('0'), False, False, D('10'), D('100')))
        assert D(held['decision']['worst_loss_quote']) == expected.worst_loss_quote == D('1.0836')
        before = await repo.snapshot(cfg.binding)
        await repo.arm(child.operation_id, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await PgStressRepository(repo).frame())['trading_permission'] is False
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,capacity,loss,expected', [
    ({'exit_depth_base': D('5.1')}, '10', '100', 'STRESS_EXIT_DEPTH_UNAVAILABLE'),
    ({'collateral_quote': D('.5')}, '10', '100', 'COLLATERAL_BUFFER_EXCEEDED'),
    ({}, '10', '.5', 'STRESS_LOSS_EXCEEDED'),
    ({}, '.1', '100', 'ROLLING_FILL_CAPACITY_EXHAUSTED'),
    ({'reference_usdt': D('2'), 'stressed_high_usdt': D('2.4')}, '10', '100', 'STRESS_REFERENCE_MISMATCH')])
async def test_native_stress_denials_rollback_candidate_and_preserve_original_money(database, changes, capacity, loss, expected):
    repo, cfg, owner, risk, ready = await stress_store(database, capacity=capacity, loss=loss)
    try:
        await stress_observe(database, repo, cfg, owner, **changes)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_' + expected):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_stress_lost_commit_recovers_original_id_without_healing_admission(database, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotStressObservation
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import ObserveNativeStress, PgStressRepository
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()'); ms = int(at.timestamp() * 1000)
        command = ObserveNativeStress('unknown', SpotStressObservation(ms, ms, 'independent_market', D('1'), D('.8'), D('1.2'), D('100'), D('100'), D('.01'), D('.01')))
        cut = (await repo.snapshot(cfg.binding)).cut
        original = repo._runtime._commit
        once = True

        async def lost(tx):
            nonlocal once
            if not once:
                return await original(tx)
            once = False
            if committed:
                await original(tx)
            raise ConnectionError('synthetic stress COMMIT reply lost')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        stress = PgStressRepository(repo)
        with pytest.raises(CommitUnknown):
            await stress.observe(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.risk_stress_events') == int(committed)
        replay = await stress.observe(command, cut=cut, owner=owner)
        assert replay['replayed'] is committed and replay['trading_permission'] is False
        assert not repo._runtime.healthy
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['event', 'receipt', 'missing'])
async def test_native_stress_corrupt_or_missing_history_denies_candidates_atomically(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import PgStressRepository
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        await stress_observe(database, repo, cfg, owner)
        table = 'operations' if fault == 'receipt' else 'risk_stress_events'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        query = "UPDATE life_financial.operations SET receipt=receipt-'decision' WHERE operation_id='stress1'" if fault == 'receipt' else "UPDATE life_financial.risk_stress_events SET body=body-'observer_id'" if fault == 'event' else 'DELETE FROM life_financial.risk_stress_events'
        await database[0].execute(query)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_STRESS_UNVERIFIED'):
            await PgStressRepository(repo).frame()
        with pytest.raises(StorageError, match='PG_STRESS_UNVERIFIED'):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_stress_cold_history_is_diagnostic_and_cannot_reuse_old_runtime_permission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import PgStressRepository
    repo, cfg, owner, risk, ready = await stress_store(database)
    cold = None
    try:
        await stress_observe(database, repo, cfg, owner)
        await repo.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        record = await PgStressRepository(cold).frame()
        assert record['operation_id'] == 'stress1' and record['trading_permission'] is False
        with pytest.raises(StorageError, match='PG_RISK_PERMISSION_REQUIRED'):
            await cold.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await (cold or repo).close()


@pytest.mark.asyncio
async def test_native_stress_policy_and_private_calculators_cannot_be_written_by_runtime(database):
    import asyncpg
    repo, cfg, owner, risk, _ = await stress_store(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        for table in ('risk_stress_policies', 'risk_stress_events'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.' + table)
        for signature in ('risk_stress_source_valid(jsonb)', 'risk_stress_evaluate(uuid,jsonb,text)', 'risk_stress_verified(uuid)', 'risk_stress_admit(uuid,life_financial.operations)', 'risk_stress_commit_guard()'):
            assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'life_financial.' + signature)
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('window', [1, 60000])
async def test_native_rolling_capacity_counts_actual_side_fills_after_terminal_completion(database, window):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_settlement import cycle

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, _ = await cycle(database)
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.risk_stress_policies VALUES($1,$2,$3,1.5,5,100,1000)', cfg.account_id, cfg.binding.policy_hash, window)
        risk = PgRiskRepository(repo)
        await refresh(database, repo, cfg, owner, risk, 'rolling-nav')
        ready = await observe(database, repo, cfg, owner, risk, 'normal')
        await stress_observe(database, repo, cfg, owner)
        before = await repo.snapshot(cfg.binding)
        child = order(owner, operation_id='new', intent_id='new', client_order_id='NewWire', quantity_base=D('.6'), risk_epoch=ready.risk_epoch)
        if window == 60000:
            with pytest.raises(StorageError, match='PG_ROLLING_FILL_CAPACITY_EXHAUSTED'):
                await repo.prepare(child, cut=before.cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).budget == before.budget
        else:
            assert (await repo.prepare(child, cut=before.cut, owner=owner)).state == 'PREPARED'
        assert await database[0].fetchval("SELECT sum(quantity) FROM life_financial.fills WHERE product='SPOT'") == 2
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_stress_source_expiring_during_physical_commit_is_rejected(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.stress import PgStressRepository
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        # Refresh safety evidence first; stress value is separately 600 ms old,
        # so only the stress source expires during this 410 ms commit wait.
        await refresh(database, repo, cfg, owner, risk, 'before-expiry')
        at = await database[0].fetchval('SELECT clock_timestamp()'); ms = int(at.timestamp() * 1000)
        command, cut, _ = await stress_observe(database, repo, cfg, owner, value_at_ms=ms - 600, observed_at_ms=ms - 600)
        original = repo._runtime._commit
        reached = False

        async def expired(tx):
            nonlocal reached
            reached = True
            await tx._connection.execute('SELECT pg_sleep(greatest(0,($1-floor(extract(epoch FROM clock_timestamp())*1000))/1000)+.01)', ms + 400)
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', expired)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_SPOT_RISK_UNAVAILABLE'):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert reached and (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await PgStressRepository(repo).frame())['trading_permission'] is False
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_revoked_native_stress_grant_is_rechecked_before_every_admission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import NATIVE_STRESS_ENTRYPOINT
    repo, cfg, owner, risk, ready = await stress_store(database)
    try:
        await stress_observe(database, repo, cfg, owner)
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + NATIVE_STRESS_ENTRYPOINT + ' FROM ' + cfg.principal)
        with pytest.raises(StorageError, match='PG_STRESS_ROLE_PROFILE_INVALID'):
            await repo.snapshot(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await repo.close()
