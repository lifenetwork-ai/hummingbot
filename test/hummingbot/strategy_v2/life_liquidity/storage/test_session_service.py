"""Configured successor orchestration retains original money and separate grants."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import control_policy, keys
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.config import SessionConfig
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, encode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick


async def paired_session(tmp_path, *, max_age_ms=1000, signer=None):
    signer = signer or keys()
    state = {'now': NOW, 'sequence': 1, 'complete': True}

    def callbacks(cfg, src):
        def capital():
            return replace(src.capital_snapshot(), observed_at_ms=int(state['now'].timestamp() * 1000), sequence=state['sequence'])

        async def collect():
            return replace(await src.joint_collect(), observed_at_ms=int(state['now'].timestamp() * 1000),
                           snapshot_sequence=state['sequence'])

        def spot():
            return (), SpotFinancialRecoveryFacts(cfg.controller.storage_binding, state['now'], D('5'), D('10'), D('0'), D('0'),
                                                  (), (), (), (), True, state['complete'], True)
        return dict(clock=lambda: state['now'], capital_snapshot=capital, joint_collect=collect, joint_spot_recovery=spot,
                    swap_account=lambda: replace(src.swap_account(), observed_at_ms=int(state['now'].timestamp() * 1000),
                                                 snapshot_sequence=state['sequence']),
                    control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})))
    from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import JointServiceConfig
    config = {'control': control_policy(signer), 'joint': JointServiceConfig(
        limits=JointLimits(16, 64, 65536), max_age_ms=max_age_ms, cancel_retry_ms=100,
        max_cancels_per_cycle=2, poll_interval_ms=250, opening_long_entry=D('1'), opening_short_entry=D('0'))}
    session = SessionConfig(duration='1h', on_expiry='switch_to_market_reference', successor_duration='30m')
    service, _, _, _, _ = await joint_service(tmp_path, hedge=False, config_extra=config,
                                              source_extra=callbacks, session_config=session)
    # Explicit source-release port for session orchestration. Native market
    # reference validation is separately exercised by configured quote tests.
    service.spot._transition_anchor = lambda: D('1')
    return service, state, signer


async def expire(service, state):
    state['now'] = service.spot.owner.expires_at
    state['sequence'] += 1
    await service.recover()
    token = service.spot.owner
    tick = SessionTick('expire', str(token.session_id), token.epoch, False, False, False, 1000)
    await service.store.tick_session(tick, cut=(await service.store.snapshot(service.store.binding)).cut, owner=token)
    await service.store._call(service.spot._sync_session)


async def publish_successor(service, snapshot):
    old = service.spot.owner
    account = snapshot.facts
    reconciliation = OrderReconciliation(str(old.session_id), old.epoch, account.observed_at, True, (), (), (), True)
    tick = SessionTick('successor', str(old.session_id), old.epoch, True, True, True, 1000,
                       reconciliation, account, D('1'))
    await service.store.tick_session(tick, cut=snapshot.cut, owner=old)
    await service.store._call(service.spot._sync_session)


@pytest.mark.asyncio
@pytest.mark.parametrize('cold', [False, True])
async def test_joint_successor_keeps_original_observation_owner_until_separate_signed_grant(tmp_path, cold):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, state, signer = await paired_session(tmp_path)
    try:
        await expire(service, state)
        before = await service.store.snapshot(service.store.binding)
        snapshot = await service.control.snapshot()
        await publish_successor(service, snapshot)
        successor = (await service.store.snapshot(service.store.binding)).session
        assert successor.epoch == service.spot.owner.epoch + 1
        assert successor.started_at == service.spot.owner.expires_at
        assert successor.expires_at == service.spot.owner.expires_at + timedelta(minutes=30)
        if cold:
            args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                        market_data_provider=service.controller.market_data_provider)
            await service.close()
            service = await FileLiquidityService.open(**args)
            assert service.store._cold_unarmed
            state['now'] += timedelta(seconds=1)
            state['sequence'] += 1
            await service._joint_safety_cycle()
            assert service.store._cold_unarmed and service.store._observation_generation is None
            assert (await service.store.snapshot(service.store.binding)).session.last_seen_at == state['now']
        snapshot = await service.control.snapshot()
        assert not service.runtime._local_ready() and not service.swap.local_ready()
        assert (await service.store.snapshot(service.store.binding)).balance == before.balance
        assert service.joint.owner == service.spot.owner
        token = replace(service.spot.owner, generation=UUID(int=100021), session_id=UUID(successor.session_id),
                        epoch=successor.epoch, valid_from=state['now'], expires_at=successor.expires_at)
        command = OwnerTransition('activate-successor', OwnerMode.ACTIVE, token)
        approval = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                               issued_at=state['now'], expires_at=state['now'] + timedelta(seconds=1))
        receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert not receipt.replayed and service.spot.owner == service.joint.owner == service.swap.owner == token
        assert not service.store._cold_unarmed
        service.runtime.revoke()
        assert (await service.control.apply(command, approval=approval, snapshot=snapshot)).replayed
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('max_age_ms', [1000, 2000])
async def test_joint_watchdog_finishes_qualified_expiry_without_extending_or_activating_the_old_writer(tmp_path, max_age_ms):
    service, state, _ = await paired_session(tmp_path, max_age_ms=max_age_ms)
    try:
        state['now'] = service.spot.owner.expires_at
        state['sequence'] += 1
        service._safety_pending = True
        await service._joint_safety_cycle()
        successor = (await service.store.snapshot(service.store.binding)).session
        assert successor.epoch == service.spot.owner.epoch + 1
        assert successor.expires_at == service.spot.owner.expires_at + timedelta(minutes=30)
        assert not service.runtime._local_ready() and not service.swap.local_ready()
        assert await service.plan() == []
        assert not service.controller._joint_recovery._armed
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_native', 'after_native', 'receipt'])
async def test_configured_cold_start_finishes_original_successor_tick_before_product_assembly(tmp_path, monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, state, _ = await paired_session(tmp_path)
    try:
        await expire(service, state)
        snapshot = await service.control.snapshot()
        before = await service.store.snapshot(service.store.binding)
        target, method = ((service.store, '_publish_session') if stage == 'receipt' else (service.store._sessions, 'save'))
        original = getattr(target, method)

        def crash(*args, **kwargs):
            if stage == 'receipt':
                if not args[0].startswith('session-done:'):
                    return original(*args, **kwargs)
            elif stage == 'after_native':
                original(*args, **kwargs)
            raise OSError('synthetic successor interruption')
        monkeypatch.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await publish_successor(service, snapshot)
        args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                    market_data_provider=service.controller.market_data_provider)
        await service.close()
        service = await FileLiquidityService.open(**args)
        assert await service.store.pending_session_ticks(service.store.binding) == ()
        after = await service.store.snapshot(service.store.binding)
        assert after.session.epoch == service.spot.owner.epoch + 1
        assert after.session.expires_at == service.spot.owner.expires_at + timedelta(minutes=30)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert service.store._cold_unarmed and not service.runtime._local_ready() and not service.swap.local_ready()
        snapshot = await service.control.snapshot()
        assert snapshot.facts.observed_at == state['now']
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source', 'stale_cut', 'stopped', 'cold', 'anchor'])
async def test_successor_application_denies_bad_or_changed_inputs_without_money_or_grant(tmp_path, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    service, state, _ = await paired_session(tmp_path)
    try:
        await expire(service, state)
        snapshot = await service.control.snapshot()
        before = await service.store.snapshot(service.store.binding)
        if fault == 'source':
            snapshot = replace(snapshot, facts=replace(snapshot.facts, bill_scope_complete=False))
        elif fault == 'stale_cut':
            await service.store._call(lambda: service.store._commit(service.store._read().cut, service.spot.owner,
                                      'synthetic-new-cut', (('test_epoch', encode(2)),), observation=True))
        elif fault == 'stopped':
            service.revoke()
        elif fault == 'cold':
            service.store._cold_unarmed = True  # Explicit admission-state fault; no rearm.
        elif fault == 'anchor':
            service.spot._transition_anchor = lambda: None
        if fault == 'source':
            with pytest.raises(StorageError):
                await service.store._call(service.spot._complete_successor, snapshot)
        else:
            assert not await service.store._call(service.spot._complete_successor, snapshot)
        after = await service.store.snapshot(service.store.binding)
        assert after.session.state == 'TRANSITIONING' and after.session.epoch == service.spot.owner.epoch
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert service.spot.owner == service.joint.owner == service.runtime.owner
        assert not service.runtime._local_ready() and not service.swap.local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_verified_successor_bridge_rejects_native_session_rewrite_even_with_valid_json(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    service, state, _ = await paired_session(tmp_path)
    try:
        await expire(service, state)
        snapshot = await service.control.snapshot()
        await publish_successor(service, snapshot)

        def forge():
            journal = service.store._sessions.load()
            successor = replace(journal.successor, anchors={'LIFE-USDT': D('2')})
            service.store._sessions.save(replace(journal, successor=successor))
            service.spot._sync_session()
        await service.store._call(forge)
        with pytest.raises(StorageError):
            await service.control.snapshot()
        assert not service.runtime._local_ready() and not service.swap.local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_actual_spot_watchdog_waits_for_proposals_then_qualifies_native_reference_and_separate_grant(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import attach_empty_observer
    signer = keys()
    clock = {'now': NOW}
    service, market = await quote_service(tmp_path, control_config=control_policy(signer),
                                          control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                                          risk_sources={'clock': lambda: clock['now']}, session_config=SessionConfig(
                                              duration='1h', on_expiry='switch_to_market_reference', successor_duration='30m'))
    attach_empty_observer(service)
    # Explicit synthetic listing/continuity release. The actual quote reference,
    # own-depth, fee and native session/financial checks are kept intact.
    service.controller._spot_quote_gates_ready = lambda: True
    market['snapshot'] = replace(market['snapshot'], market_anchor_usdt=D('1'))
    try:
        actions = await service.plan()
        assert len(actions) == 1
        clock['now'] = service.spot.owner.expires_at
        before = await service.store.snapshot(service.store.binding)
        await service.spot._safety_cycle()
        pending = await service.store.snapshot(service.store.binding)
        assert pending.session.state == 'TRANSITIONING'
        assert pending.session.epoch == service.spot.owner.epoch
        assert service.controller.order_safety_reason_code == 'SESSION_PROPOSALS_UNRESOLVED'
        service.rejected(actions[0])
        await service.drain_action_events()
        service.controller._pause_reconciliation_required = True  # Prior primary pause awaiting full reconciliation.
        await service.spot._safety_cycle()
        assert not service.controller._pause_reconciliation_required
        successor = (await service.store.snapshot(service.store.binding)).session
        assert successor.epoch == service.spot.owner.epoch + 1
        assert successor.anchors_usdt['LIFE-USDT'] == D('1')
        assert service.controller.order_safety_reason_code == 'SESSION_SUCCESSOR_AWAITING_GRANT'
        assert not service.runtime._local_ready() and await service.plan() == []
        snapshot = await service.control.snapshot()
        token = replace(service.spot.owner, generation=UUID(int=100022), session_id=UUID(successor.session_id),
                        epoch=successor.epoch, valid_from=clock['now'], expires_at=successor.expires_at)
        command = OwnerTransition('activate-spot-successor', OwnerMode.ACTIVE, token)
        approval = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                               issued_at=clock['now'], expires_at=clock['now'] + timedelta(seconds=1))
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        market['snapshot'] = replace(market['snapshot'], session_id=successor.session_id, epoch=successor.epoch)
        assert len(await service.plan()) == 1
        after = await service.store.snapshot(service.store.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('elapsed_monotonic', [False, True])
async def test_waiting_for_new_grant_keeps_successor_deadline_distinct_from_expired_primary(tmp_path, elapsed_monotonic):
    service, state, _ = await paired_session(tmp_path)
    try:
        if elapsed_monotonic:
            import time
            service.store._monotonic_deadline = time.monotonic() - 1  # Simulate the original hour elapsing.
        state['now'] = service.spot.owner.expires_at
        state['sequence'] += 1
        await service._joint_safety_cycle()
        successor = (await service.store.snapshot(service.store.binding)).session
        assert successor.epoch == service.spot.owner.epoch + 1
        old_admission_deadline = service.store._monotonic_deadline
        state['now'] += timedelta(seconds=1)
        state['sequence'] += 1
        await service._joint_safety_cycle()
        waiting = (await service.store.snapshot(service.store.binding)).session
        assert waiting.state == 'ACTIVE'
        assert waiting.last_seen_at == state['now']
        assert (waiting.session_id, waiting.expires_at) == (successor.session_id, successor.expires_at)
        assert service.store._monotonic_deadline == old_admission_deadline
        assert not service.runtime._local_ready() and not service.swap.local_ready()
        snapshot = await service.control.snapshot()
        token = replace(service.spot.owner, generation=UUID(int=100023), session_id=UUID(successor.session_id),
                        epoch=successor.epoch, valid_from=state['now'], expires_at=successor.expires_at)
        command = OwnerTransition('activate-after-wait', OwnerMode.ACTIVE, token)
        # The external signer is retained only by the test client.
        signer = keys()
        approval = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                               issued_at=state['now'], expires_at=state['now'] + timedelta(seconds=1))
        assert not (await service.control.apply(command, approval=approval, snapshot=snapshot)).replayed
        assert service.spot.owner == token
        state['now'] = successor.expires_at
        state['sequence'] += 1
        await service._joint_safety_cycle()
        final = (await service.store.snapshot(service.store.binding)).session
        assert final.state == 'EXPIRED' and final.expires_at == successor.expires_at
    finally:
        await service.close()


@pytest.mark.parametrize('field', ['reference_ready', 'all_gates_ready', 'market_reference_ready', 'market_anchor_usdt'])
def test_observation_only_successor_command_cannot_assert_gate_or_anchor(field):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    tick = SessionTick('observe', 'session', 1, False, False, False, 1000, observe_only_successor=True)
    assert SessionTick.from_json(tick.json()) == tick
    with pytest.raises(StorageError, match='SESSION_OBSERVATION_ONLY_REQUIRED'):
        replace(tick, **{field: D('1') if field == 'market_anchor_usdt' else True})
    assert 'observe_only_successor' not in replace(tick, observe_only_successor=False).json()
