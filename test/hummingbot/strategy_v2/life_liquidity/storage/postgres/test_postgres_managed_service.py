"""Managed service, original signed lease and real SDK/V2; fake HTTP only."""
import asyncio
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.managed_process import PgManagedProcessConfig
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgLeaseGrantIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def managed_service(database, *, lease_ms=4000, heartbeat_ms=250, register_change=None, managed=True, makers=False, hedge=False):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import (
        policy as hedge_policy,
    )
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE'))})
    state = SimpleNamespace(keys=keys)

    async def prepare(t, config):
        c = database[0]
        f = PgSpotRepository(await PgRuntime.open(t.cfg))
        try:
            await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', t.cfg.account_id)
            await c.execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,32,1048576,60000)', t.cfg.account_id, t.cfg.binding.policy_hash)
            control = PgControlRepository(c, f, verifier=keys.verifier())
            drain = OwnerTransition('managed-drain', OwnerMode.DRAINING, t.owner)
            await control.transition_owner(drain, **await signed(database, f, t.cfg, keys, drain))
            now = await c.fetchval('SELECT clock_timestamp()')
            t.owner = OwnerToken(uuid4(), uuid4(), t.owner.epoch, t.owner.session_id, now, now + timedelta(seconds=30))
            activate = OwnerTransition('managed-activate', OwnerMode.ACTIVE, t.owner)
            await control.transition_owner(activate, **await signed(database, f, t.cfg, keys, activate))
            await c.execute('INSERT INTO life_financial.process_lease_policies VALUES($1,$2,$3,8)', t.cfg.account_id, t.cfg.binding.policy_hash, lease_ms)
            state.grant = await c.fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='managed-activate'")
            state.issuer = PgLeaseGrantIssuer(control)
            state.t = t
            state.financial = f
        finally:
            if not hasattr(state, 'financial') or not managed:
                await f.close()

    async def register(registration):
        state.registration = registration
        try:
            if register_change is not None:
                await register_change(state, registration)
            else:
                await state.issuer.register_first_generation(state.grant, registration)
        finally:
            await state.financial.close()

    def model(body):
        if makers:
            body.update(maker_schedule=dict(schedule_id='synthetic-managed', levels=[0], refresh_interval_ms=100, max_cycles=1))
    try:
        t, service = await configured(
            database, before_service_open=prepare, maker_cycle=makers, model_change=model,
            hedge_policy=hedge_policy() if hedge else None,
            configuration_extra={'operator_verifier': keys.verifier().policy(),
                                 'process_lease': dict(lease_duration_ms=lease_ms, max_registrations=8)},
            managed_process=PgManagedProcessConfig(claim_operation_id='managed-claim', heartbeat_interval_ms=heartbeat_ms) if managed else None,
            register_process=register if managed else None)
    except BaseException:
        if hasattr(state, 'financial'):
            await state.financial.close()
        raise
    return t, service, state


@pytest.mark.asyncio
async def test_managed_service_starts_unarmed_and_requires_explicit_original_process_activation(database):
    t, service, state = await managed_service(database)
    original_error = None
    try:
        assert service.process_mode == 'UNARMED' and not service._trading_ready()
        await service.controller.update_processed_data()
        assert service.controller.processed_data['process_mode'] == 'UNARMED'
        assert service.controller.to_format_status() == ['PostgreSQL process UNARMED: trading disabled']
        assert service.process_lease.ready and service.events._started and service.reporting.ready
        before = await service.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await service.quote('unarmed-create', TradeType.BUY, 0)
        assert not t.service_orders
        await service.arm_process()
        assert service.process_mode == 'ACTIVE' and service._trading_ready()
        await service.quote('managed-create', TradeType.BUY, 0)
        assert len(t.service_orders) == 1
        with pytest.raises(StorageError, match='STATE'):
            await service.arm_process()
        assert (await service.financial.snapshot(t.cfg.binding)).balance.total_quote_usdt == before.balance.total_quote_usdt
        assert state.registration.capability_hash != service.process_lease.capability._secret
    except BaseException as error:
        original_error = error
        raise
    finally:
        try:
            await service.close()
        except BaseException:
            if original_error is not None:
                raise original_error
            raise
    assert service.process_mode == 'STOPPED' and not service.process_lease.ready
    assert service._process.task.done() and not service.runtime._tasks


@pytest.mark.asyncio
async def test_service_heartbeat_keeps_only_original_lease_and_close_never_rearms(database):
    t, service, _ = await managed_service(database, lease_ms=1000, heartbeat_ms=100)
    try:
        first = await database[0].fetchrow('SELECT * FROM life_financial.process_leases')
        await asyncio.sleep(1.15)
        current = await database[0].fetchrow('SELECT * FROM life_financial.process_leases')
        assert current['observed_at'] > first['expires_at'] and current['lease_epoch'] == 1
        assert service.process_mode == 'UNARMED' and service.process_lease.ready
        await service.arm_process()
        assert service._trading_ready()
        service.revoke()
        assert not service._trading_ready()
        with pytest.raises(StorageError):
            await service.arm_process()
        assert (await service.process_lease.lookup('managed-claim')).replayed
        assert not service.process_lease.ready and service.process_mode == 'STOPPED'
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['lost_commit', 'database_error', 'expired_local_deadline'])
async def test_managed_service_loses_admission_without_retry_or_money_release(database, monkeypatch, fault):
    t, service, _ = await managed_service(database, lease_ms=2000, heartbeat_ms=250)
    try:
        await service.arm_process()
        before = await service.financial.snapshot(t.cfg.binding)
        entered = asyncio.Event()
        if fault == 'expired_local_deadline':
            service.runtime._lease_deadline = 0
            # The final synchronous local fence must latch the loss before
            # an already pending heartbeat reply gets another event-loop turn.
            assert not service._trading_ready()
        else:
            original = service.runtime._commit

            async def fail(transaction):
                entered.set()
                if fault == 'lost_commit':
                    await original(transaction)
                raise ConnectionError('synthetic lease reply failure')
            monkeypatch.setattr(service.runtime, '_commit', fail)
        async with asyncio.timeout(3):
            while not service._revoked:
                await asyncio.sleep(.01)
        assert service.process_mode == 'STOPPED' and not service._trading_ready()
        if fault != 'expired_local_deadline':
            assert entered.is_set()
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        with pytest.raises(StorageError):
            await service.quote('after-lease-loss', TradeType.BUY, 0)
        assert not t.service_orders
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_managed_policy_cannot_silently_start_unmanaged_service(database):
    with pytest.raises(StorageError, match='MANAGED_PROCESS_REQUIRED'):
        await managed_service(database, managed=False)
    assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.process_leases)')


@pytest.mark.asyncio
@pytest.mark.parametrize('registration_fault', ['missing', 'wrong_hash'])
async def test_service_refuses_unregistered_or_wrong_process_before_installing_sender(database, registration_fault):
    from dataclasses import replace

    async def wrong(state, registration):
        if registration_fault == 'wrong_hash':
            await state.issuer.register_first_generation(state.grant, replace(registration, capability_hash='0' * 64))
    with pytest.raises(StorageError):
        await managed_service(database, register_change=wrong)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_leases') == 0


@pytest.mark.asyncio
async def test_managed_drain_closes_all_new_risk_and_recovery_requires_signed_native_mode(database):
    t, service, state = await managed_service(database)
    control_financial = None
    try:
        await service.arm_process()
        before = await service.financial.snapshot(t.cfg.binding)
        service.drain_process()
        assert service.process_mode == 'DRAINING' and not service._trading_ready()
        with pytest.raises(StorageError, match='NATIVE_RECOVERY_ONLY_REQUIRED'):
            await service.enter_process_recovery_only()
        for operation in (lambda: service.arm_process(),
                          lambda: service.quote('drained-quote', TradeType.BUY, 0),
                          lambda: service.cancel_makers('drained-cancel'),
                          lambda: service.execute_hedge('drained-hedge')):
            with pytest.raises(StorageError):
                await operation()
        assert not t.service_orders
        control_financial = PgSpotRepository(await PgRuntime.open(t.cfg))
        control = PgControlRepository(database[0], control_financial, verifier=state.keys.verifier())
        recover = OwnerTransition('managed-recovery', OwnerMode.RECOVERY_ONLY, t.owner)
        await control.transition_owner(recover, **await signed(database, control_financial, t.cfg, state.keys, recover))
        await service.enter_process_recovery_only()
        assert service.process_mode == 'RECOVERY_ONLY' and not service._trading_ready()
        assert (await service.controller.runner_facts()).scope_complete
        with pytest.raises(StorageError):
            await service.arm_process()
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
    finally:
        await service.close()
        if control_financial is not None:
            await control_financial.close()


@pytest.mark.asyncio
async def test_pending_observation_closes_admission_without_spurious_process_revocation(database):
    t, service, _ = await managed_service(database, lease_ms=2000, heartbeat_ms=100)
    try:
        await service.arm_process()
        service.runtime._observation_inflight += 1
        try:
            await asyncio.sleep(.25)
            assert service.process_mode == 'ACTIVE' and not service.runtime.lease_ready
            assert service._local_ready()  # Original preparation observations may finish.
            assert not service._revoked
        finally:
            service.runtime._observation_inflight -= 1
        assert service._trading_ready()
        # Heartbeats may continue during observation: they neither change the
        # financial cut nor reopen risk. Only the signed owner's deadline caps
        # renewal. A stopped heartbeat must still close admission on expiry.
        await asyncio.sleep(.2)
        assert service._trading_ready()
        service._process.task.cancel()
        await asyncio.gather(service._process.task, return_exceptions=True)
        assert service.process_mode == 'STOPPED'
        assert not t.service_orders
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unarmed_clock_ticks_do_not_start_makers_or_revoke_observation_service(database, monkeypatch):
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    t, service, _ = await managed_service(database, makers=True)
    try:
        monkeypatch.setattr(MarketDataProvider, 'ready', property(lambda _: True))
        for _ in range(3):
            service.runner.on_tick()
            await asyncio.sleep(0)
        assert service.process_mode == 'UNARMED' and not service._revoked
        assert service.makers.task is None and not t.service_orders
        assert service.reporting.ready and service.events._started
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_managed_heartbeat_interval_requires_headroom_in_imported_policy(database):
    with pytest.raises(StorageError, match='HEARTBEAT_POLICY_INVALID'):
        await managed_service(database, lease_ms=1000, heartbeat_ms=500)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_leases') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0


@pytest.mark.asyncio
async def test_final_service_fence_rejects_inherited_process_capability_before_next_heartbeat(database):
    t, service, _ = await managed_service(database)
    try:
        await service.arm_process()
        capability = service.process_lease.capability
        object.__setattr__(capability, '_pid', capability._pid + 1)
        assert not service._trading_ready() and service.process_mode == 'STOPPED'
        assert not t.service_orders and service._revoked
        with pytest.raises(StorageError):
            await service.quote('inherited-process-create', TradeType.BUY, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_removing_managed_service_alias_cannot_fall_back_to_unmanaged_admission(database):
    t, service, _ = await managed_service(database)
    owned = service._process
    try:
        service._process = None
        assert not service._trading_ready() and service._revoked
        assert not service.runtime.lease_valid and owned._mode == 'STOPPED'
        with pytest.raises(StorageError, match='CONTEXT_CHANGED'):
            await service.arm_process()
        assert not t.service_orders
    finally:
        # Cleanup must retain the original heartbeat even if an alias changed.
        await service.close()
    assert owned.task.done() and not service.runtime._tasks


@pytest.mark.asyncio
async def test_managed_swap_hedge_requires_arm_and_preserves_original_ack_claim_on_stop(database):
    t, service, _ = await managed_service(database, hedge=True)
    try:
        with pytest.raises(StorageError):
            await service.execute_hedge('unarmed-swap')
        assert not t.service_orders
        await service.arm_process()
        receipt = await service.execute_hedge('managed-swap')
        assert receipt.child_operation_id is not None
        attempt = await database[0].fetchrow("SELECT * FROM life_financial.send_attempts WHERE product='SWAP'")
        assert attempt['state'] == 'ACKED' and len(t.service_orders) == 1
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        service.drain_process()
        with pytest.raises(StorageError):
            await service.execute_hedge('drained-swap')
    finally:
        await service.close()
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
