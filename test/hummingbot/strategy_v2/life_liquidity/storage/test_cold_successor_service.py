"""Explicit observation approval must never become an activation grant."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import keys
from test.hummingbot.strategy_v2.life_liquidity.storage.test_session_service import paired_session

import pytest

from hummingbot.connector.exchange.okx import okx_constants
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys


async def cold_service(tmp_path, *, max_age_ms=1000):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    base = keys()
    signer = OperatorKeys(base.keys, {'operator': base.roles['operator'] | {'OBSERVE_SESSION'}})
    service, state, _ = await paired_session(tmp_path, signer=signer, max_age_ms=max_age_ms)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    await service.close()
    state['now'] = args['config'].owner.expires_at
    state['sequence'] += 1
    service = await FileLiquidityService.open(**args)
    # Synthetic market release isolates observation authorization. This does
    # not establish coupled production market or risk-source qualification.
    service.spot._transition_anchor = lambda **_: D('1')
    return service, state, signer, args


async def signed_grant(service, state, signer, *, operation_id='observe', seconds=10):
    from hummingbot.strategy_v2.life_liquidity.storage.session_observation import AuthorizeSessionObservation
    snapshot = await service.control.snapshot()
    token = service.spot.owner
    command = AuthorizeSessionObservation(operation_id, service.control.observer_id,
                                          str(token.session_id), token.epoch, token,
                                          state['now'] + timedelta(seconds=seconds))
    approval = signer.sign('operator', service.store.binding, 'OBSERVE_SESSION', command.json(), snapshot.cut,
                           snapshot.facts, issued_at=state['now'], expires_at=command.expires_at)
    return command, approval, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('max_age_ms', [1000, 2000])
async def test_cold_new_successor_requires_signed_observation_and_stays_unarmed(tmp_path, max_age_ms):
    service, state, signer, _ = await cold_service(tmp_path, max_age_ms=max_age_ms)
    try:
        before = await service.store.snapshot(service.store.binding)
        with pytest.raises(StorageError):
            await service.control.advance_successor('missing')
        command, approval, snapshot = await signed_grant(service, state, signer)
        receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert not receipt.replayed
        assert await service.control.advance_successor(command.operation_id)
        after = await service.store.snapshot(service.store.binding)
        assert after.session.epoch == before.session.epoch + 1
        assert after.session.started_at == before.session.expires_at
        assert after.session.expires_at == before.session.expires_at + timedelta(minutes=30)
        assert after.balance == before.balance
        assert after.budget == before.budget
        assert (await service.store.current_owner(service.store.binding)).token == service.spot.owner
        assert service.store._cold_unarmed and service.store._observation_generation is None
        assert not service.runtime._local_ready() and not service.swap.local_ready()
        assert not service.controller._joint_recovery._armed
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'observer', 'expiry', 'incomplete'])
async def test_cold_observation_grant_denies_changed_or_stale_scope(tmp_path, fault):
    from dataclasses import replace
    from uuid import uuid4
    service, state, signer, _ = await cold_service(tmp_path)
    try:
        command, approval, snapshot = await signed_grant(service, state, signer)
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'observer':
            command = replace(command, observer_id=uuid4())
        elif fault == 'expiry':
            state['now'] = command.expires_at
        with pytest.raises(StorageError):
            if fault == 'incomplete':
                state['complete'] = False
                snapshot = await service.control.snapshot()
            await service.control.apply(command, approval=approval, snapshot=snapshot)
        view = await service.store.snapshot(service.store.binding)
        assert view.session.epoch == service.spot.owner.epoch
        assert service.store._cold_unarmed and service.store._observation_generation is None
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'drawdown', 'margin', 'stale', 'stop', 'reference'])
async def test_cold_spot_successor_uses_actual_reference_and_read_only_nav_without_risk_rearm(tmp_path, fault):
    from dataclasses import replace
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
        attach_empty_observer,
        control_policy,
    )

    from hummingbot.strategy_v2.life_liquidity.config import SessionConfig
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    base = keys()
    signer = OperatorKeys(base.keys, {'operator': base.roles['operator'] | {'OBSERVE_SESSION'}})
    state, callbacks = sources()
    service, market = await quote_service(tmp_path, risk_config=policy(min_margin_buffer_quote=D('1')),
                                          risk_sources=callbacks, control_config=control_policy(signer),
                                          control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                                          session_config=SessionConfig(duration='1h', on_expiry='switch_to_market_reference',
                                                                       successor_duration='30m'))
    await service.store._call(service.risk.ready)
    await service.store._call(service.risk.ready)
    book_snapshot = service.controller.snapshot_gate.snapshot
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    await service.close()
    state['now'] = service.spot.owner.expires_at
    at = int(state['now'].timestamp() * 1000)
    state['nav'] = replace(state['nav'], value_at_ms=at, observed_at_ms=at)
    market['snapshot'] = replace(market['snapshot'], market_anchor_usdt=D('1'))
    service = await FileLiquidityService.open(**args)
    # Explicit high-throughput fake-HTTP budget for repeated complete source
    # cuts. This is not an exchange rate-limit or deployed freshness claim.
    attach_empty_observer(service, rate_limits=[RateLimit(r.limit_id, 1000, r.time_interval,
                                                          r.weight, r.linked_limits) for r in okx_constants.RATE_LIMITS])
    service.controller._spot_quote_gates_ready = lambda: True  # Synthetic listing/continuity only.
    service.controller.listing_gate.instrument_found = True
    service.controller.listing_gate.instrument_state = 'live'
    service.controller.listing_gate.instrument_rules = market['snapshot'].rules
    service.controller.continuous_gate.ready = True
    service.controller.continuous_gate.exchange_time_ms = 1100
    service.controller.snapshot_gate.clock = lambda: 2.05
    service.controller.snapshot_gate.snapshot = book_snapshot
    service.controller.snapshot_gate.ready = True
    try:
        before = await service.store.snapshot(service.store.binding)
        command, approval, snapshot = await signed_grant(service, state, signer)
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert service.risk.gate.rearm_required
        if fault == 'drawdown':
            state['nav'] = replace(state['nav'], price_usdt=D('.5'))
        elif fault == 'margin':
            state['risk'] = replace(state['risk'], margin_buffer_quote=D('0'))
        elif fault == 'stale':
            state['nav'] = replace(state['nav'], value_at_ms=at - 1001)
        elif fault == 'reference':
            market['snapshot'] = replace(market['snapshot'], market_reference_ready=False)
        elif fault == 'stop':
            service.controller.stop()
        if fault == 'stop':
            with pytest.raises(StorageError):
                await service.control.advance_successor(command.operation_id)
        else:
            assert await service.control.advance_successor(command.operation_id) is (fault == 'none')
        after = await service.store.snapshot(service.store.binding)
        assert (after.session.epoch == before.session.epoch + 1) is (fault == 'none')
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert service.risk.gate.rearm_required
        assert not service.risk._last_allowed and not service.risk._last_reduction_allowed
        assert not await service.store._call(service.risk.ready)
        assert service.store._cold_unarmed and service.store._observation_generation is None
        if fault in ('drawdown', 'margin'):
            assert service.risk.gate.state == 'HALTED'
        if fault == 'none':
            from uuid import UUID, uuid4

            from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
            from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
            snapshot = await service.control.snapshot()
            token = replace(service.spot.owner, generation=uuid4(), session_id=UUID(after.session.session_id),
                            epoch=after.session.epoch, valid_from=state['now'], expires_at=after.session.expires_at)
            activation = OwnerTransition('separate-activation', OwnerMode.ACTIVE, token)
            signed = signer.sign('operator', service.store.binding, 'ACTIVATE', activation.json(), snapshot.cut,
                                 snapshot.facts, issued_at=state['now'], expires_at=state['now'] + timedelta(seconds=1))
            receipt = await service.control.apply(activation, approval=signed, snapshot=snapshot)
            assert not receipt.replayed and not service.store._cold_unarmed
            assert service.spot.owner == token and not service.risk.gate.rearm_required
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_grant_historical_replay_never_installs_process_observation_permission(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, state, signer, args = await cold_service(tmp_path)
    try:
        command, approval, snapshot = await signed_grant(service, state, signer)
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        await service.close()
        service = await FileLiquidityService.open(**args)
        receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert receipt.replayed
        with pytest.raises(StorageError):
            await service.control.advance_successor(command.operation_id)
        assert service.store._cold_unarmed and service.store._observation_generation is None
        assert (await service.store.snapshot(service.store.binding)).session.epoch == service.spot.owner.epoch
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_observation_expiry_at_session_publication_cannot_record_a_new_decision(tmp_path, monkeypatch):
    service, state, signer, _ = await cold_service(tmp_path)
    try:
        command, approval, snapshot = await signed_grant(service, state, signer)
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        original = service.store._publish_session

        def expire_before_commit(*args, **kwargs):
            if args[0].startswith('session-start:'):
                state['now'] = command.expires_at
            return original(*args, **kwargs)

        monkeypatch.setattr(service.store, '_publish_session', expire_before_commit)
        with pytest.raises(StorageError):
            await service.control.advance_successor(command.operation_id)
        meta = await service.store.read(service.store.binding)
        assert not meta.resources.get('session_operations')
        assert service.store._cold_unarmed and service.store._observation_generation is None
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_native', 'after_native', 'receipt'])
async def test_cold_observation_original_decision_repairs_after_its_grant_expires(tmp_path, monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, state, signer, args = await cold_service(tmp_path)
    try:
        command, approval, snapshot = await signed_grant(service, state, signer)
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        target, method = ((service.store, '_publish_session') if stage == 'receipt' else (service.store._sessions, 'save'))
        original = getattr(target, method)

        def crash(*values, **kwargs):
            if stage == 'receipt' and not values[0].startswith('session-done:'):
                return original(*values, **kwargs)
            if stage == 'after_native':
                original(*values, **kwargs)
            raise OSError('synthetic original observation interruption')

        monkeypatch.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await service.control.advance_successor(command.operation_id)
        monkeypatch.setattr(target, method, original)
        await service.close()
        state['now'] = command.expires_at
        state['sequence'] += 1
        service = await FileLiquidityService.open(**args)
        assert not await service.store.pending_session_ticks(service.store.binding)
        assert (await service.store.snapshot(service.store.binding)).session.state == 'TRANSITIONING'
        assert service.store._cold_unarmed and service.store._observation_generation is None
        with pytest.raises(StorageError):
            await service.control.advance_successor(command.operation_id)
    finally:
        await service.close()
