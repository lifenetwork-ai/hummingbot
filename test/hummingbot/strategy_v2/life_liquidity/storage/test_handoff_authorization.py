"""Signed source freeze is recoverable and never a target send grant."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration_service import sign
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
    attach_empty_observer,
    control_policy,
    keys,
)
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig
from hummingbot.strategy_v2.life_liquidity.session import _deadline
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.handoff import CancelConfigurationHandoff

LIMITS = {'max_bytes': 8 * 1024 * 1024, 'max_rows': 128}  # Offline capacities only.


async def setup(tmp_path, *, joint=False, flat=True, before_stage=None, changes=None, quote_kwargs=None):
    existing = keys()
    signer = OperatorKeys(existing.keys, {key: roles | frozenset(('CONFIGURE', 'HANDOFF')) for key, roles in existing.roles.items()})

    def seed(b, o):
        return encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()}))
    if joint:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

        def extra(c, s):
            obs = s.capital_snapshot()
            account = s.swap_account()
            feed = s.carry_observation()
            if flat:
                obs = replace(obs, long_base=D('0'), short_base=D('0'), initial_margin_quote=D('0'), maintenance_margin_quote=D('0'))
                account = replace(account, net_contracts=D('0'), long_contracts=D('0'), short_contracts=D('0'))
                feed = replace(feed, account=replace(feed.account, snapshot=obs))
            return {'control_seed': seed, 'capital_snapshot': lambda: obs, 'swap_account': lambda: account,
                    'carry_observation': lambda: feed, 'joint_spot_recovery': lambda: ((), facts(c.controller.storage_binding))}
        service, _, _, _, _ = await joint_service(tmp_path, carry=True, config_extra={'control': control_policy(signer)},
                                                  source_extra=extra)
    else:
        service, _ = await quote_service(tmp_path, control_config=control_policy(signer), control_seed=seed, **(quote_kwargs or {}))
        attach_empty_observer(service)
    if before_stage is not None:
        await before_stage(service)
    staged, snapshot = await service.control.propose_financial_configuration(
        {'session': {'duration': '2h'}} if changes is None else changes, operation_id='stage')
    await service.control.apply(staged, approval=sign(service, signer, staged, snapshot), snapshot=snapshot)
    journal = await service.store._call(service.store._sessions.load)
    parent = journal.successor or journal.primary
    target = replace(parent, session_id=str(UUID(int=9876)), epoch=parent.epoch + 1,
                     started_at=parent.expires_at, expires_at=_deadline(parent.expires_at, D('7200')),
                     config_version=StrategyConfig.model_validate_json(staged.candidate_json).config_version)
    namespace = replace(service.store.binding, lineage=UUID(int=9877), config_hash=staged.target_hash)
    return service, signer, namespace, target


async def proposal(service, namespace, target):
    return await service.control.propose_configuration_handoff(operation_id='reserve-target', target_binding=namespace,
                                                               target_session_json=encode(target.to_dict()).decode(), **LIMITS)


async def maintenance(service, signer):
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    c = service.config
    return await open_legacy_spot(service.store._files, binding=service.store.binding, limits=c.file_limits,
                                  risk_limits=c.risk_limits, subsidy_policy=c.subsidy_policy, loss_policy=c.loss_policy,
                                  config_version=c.config_version, account_lock_root=c.account_lock_root, clock=service.sources.clock,
                                  fill_policy=c.fill_policy, accounting_policy=c.accounting_policy, terminal_policy=c.terminal_policy,
                                  recovery_policy=c.recovery_policy, settlement_policy=c.settlement_policy, operator_keys=signer.verifier())


async def freeze(service, signer, namespace, target):
    command, snapshot = await proposal(service, namespace, target)
    approval = sign(service, signer, command, snapshot, 'HANDOFF')
    receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
    return command, snapshot, approval, receipt


@pytest.mark.asyncio
async def test_signed_target_freezes_source_without_changing_money_deadline_or_active_revision(tmp_path):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, snapshot = await proposal(service, namespace, target)
        before = await service.store.snapshot(service.store.binding)
        receipt = await service.control.apply(command, approval=sign(service, signer, command, snapshot, 'HANDOFF'), snapshot=snapshot)
        assert receipt.state == 'SOURCE_FROZEN' and not receipt.replayed
        state = await service.store.configuration_handoff(service.store.binding)
        assert state.active == command and state.sequence == 1
        after = await service.store.snapshot(service.store.binding)
        assert after.session == before.session and after.balance == before.balance and after.budget == before.budget
        assert service.controller.config.strategy.config_version == 1 and not service.runtime._local_ready()
        assert (await service.store.current_owner(service.store.binding)).mode is OwnerMode.DRAINING
        source = await service.store.frozen_configuration_source(service.store.binding)
        assert source.content_digest == command.source_digest and source.core.cut == snapshot.cut
        assert list((Path(service.config.state_dir) / 'handoff_sources').glob('*.json'))
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await service.store._call(lambda: None)
        assert not hasattr(receipt, 'send_authorized')
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_maintenance_readback_signed_cancel_and_historical_replay_never_activate(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, signer, namespace, target = await setup(tmp_path)
    command, snapshot, approval, _ = await freeze(service, signer, namespace, target)
    source = await service.store.frozen_configuration_source(service.store.binding)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    await service.close()
    with pytest.raises(StorageError, match='SOURCE_FROZEN_MAINTENANCE_REQUIRED'):
        await FileLiquidityService.open(**args)
    store = await maintenance(service, signer)
    try:
        assert (await store.frozen_configuration_source(store.binding)).content_digest == source.content_digest
        assert store._cold_unarmed
        assert (await store.authorize_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)).replayed
        before = await store.snapshot(store.binding)
        cancel = CancelConfigurationHandoff('cancel', 1, command.owner, command.operation_id)
        # Cancellation is administrative: an external cash change is not
        # adopted into the frozen native wallet just to release the freeze.
        facts = replace(snapshot.facts, account_quote_usdt=D('99'))
        signature = signer.sign('operator', store.binding, 'HANDOFF', cancel.json(), before.cut, facts,
                                issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        result = await store.cancel_configuration_handoff(cancel, approval=signature, facts=facts, cut=before.cut)
        assert result.state == 'CANCELLED' and not result.replayed
        assert (await store.configuration_handoff(store.binding)).active is None
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget and after.session == before.session
        assert (await store.financial_configuration(store.binding)).pending is not None
        assert (await store.current_owner(store.binding)).mode is OwnerMode.DRAINING and store._cold_unarmed
        assert (await store.authorize_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)).replayed
        assert not store._handoff_frozen
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_lost_freeze_reply_keeps_artifact_and_cold_exact_readback(tmp_path, monkeypatch, published):
    service, signer, namespace, target = await setup(tmp_path)
    command, snapshot = await proposal(service, namespace, target)
    approval = sign(service, signer, command, snapshot, 'HANDOFF')
    original = service.store._state.commit

    def lost(state):
        if published:
            original(state)
        raise OSError('synthetic lost freeze reply')
    with monkeypatch.context() as patch:
        patch.setattr(service.store._state, 'commit', lost)
        with pytest.raises(CommitUnknown):
            await service.control.apply(command, approval=approval, snapshot=snapshot)
    assert service.store._uncertain
    await service.close()
    store = await maintenance(service, signer)
    try:
        assert ((await store.configuration_handoff(store.binding)).active is not None) == published
        if published:
            assert (await store.frozen_configuration_source(store.binding)).content_digest == command.source_digest
            assert (await store.authorize_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)).replayed
        else:
            assert await store._call(lambda: True)
            assert list((Path(service.config.state_dir) / 'handoff_sources').glob('*.json'))
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'tamper', 'symlink'])
async def test_frozen_artifact_damage_never_unfreezes_or_bootstraps(tmp_path, fault):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        await freeze(service, signer, namespace, target)
        path = next((Path(service.config.state_dir) / 'handoff_sources').glob('*.json'))
        if fault == 'missing':
            path.unlink()
        elif fault == 'tamper':
            path.write_bytes(b'{}')
        else:
            other = tmp_path / 'copied-source.json'
            other.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(other)
        with pytest.raises(StorageError):
            await service.store.frozen_configuration_source(service.store.binding)
        assert (await service.store.configuration_handoff(service.store.binding)).active is not None
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await service.store._call(lambda: None)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_flat_joint_hedge_carry_freeze_retains_product_sources_and_blocks_callbacks(tmp_path):
    service, signer, namespace, target = await setup(tmp_path, joint=True)
    try:
        command, _, _, _ = await freeze(service, signer, namespace, target)
        image = await service.store.frozen_configuration_source(service.store.binding)
        assert image.content_digest == command.source_digest
        assert {'shared_capital', 'swap_wal', 'swap_actions', 'joint_recovery', 'hedge', 'carry', 'requests'} == set(dict(image.ancillary_images))
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await service.products.cut(service.store.binding)
        assert await service.plan() == [] and not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pending_proposal_prevents_freeze_and_is_never_refunded(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
    service, signer, namespace, target = await setup(tmp_path)
    try:
        parent = (await service.store.snapshot(service.store.binding)).session
        claim = QuoteActionRecord('pending-source', 'life', parent.session_id, parent.epoch, 1, 'LIFE-USDT', 'BUY', 0)
        await service.store._call(service.quotes.native.action_journal.claim_batch, (claim,))
        command, snapshot = await proposal(service, namespace, target)
        with pytest.raises(StorageError, match='SOURCE_NOT_QUIESCENT'):
            await service.control.apply(command, approval=sign(service, signer, command, snapshot, 'HANDOFF'), snapshot=snapshot)
        assert (await service.store.configuration_handoff(service.store.binding)).active is None
        assert await service.store._call(service.quotes.native.action_journal.active_records) == (claim,)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_open_derivative_exposure_prevents_freeze_without_closing_positions(tmp_path):
    service, signer, namespace, target = await setup(tmp_path, joint=True, flat=False)
    try:
        command, snapshot = await proposal(service, namespace, target)
        with pytest.raises(StorageError, match='SOURCE_NOT_QUIESCENT'):
            await service.control.apply(command, approval=sign(service, signer, command, snapshot, 'HANDOFF'), snapshot=snapshot)
        assert (await service.store.configuration_handoff(service.store.binding)).active is None
        assert await service.store._call(lambda: service.joint._recovery.capital.qualified_snapshot().long_base) == D('1')
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_queued_worker_callback_is_fenced_after_freeze_publication(tmp_path, monkeypatch):
    import asyncio
    import threading
    service, signer, namespace, target = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    freezing = queued = None
    mutated = []
    try:
        command, snapshot = await proposal(service, namespace, target)
        approval = sign(service, signer, command, snapshot, 'HANDOFF')
        original = service.store._control_commit

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test publication not released')
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', blocked)
        freezing = asyncio.create_task(service.control.apply(command, approval=approval, snapshot=snapshot))
        assert await asyncio.to_thread(entered.wait, 2)
        queued = asyncio.create_task(service.store._call(mutated.append, 'must-not-run'))
        await asyncio.sleep(0)
        release.set()
        assert (await freezing).state == 'SOURCE_FROZEN'
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await queued
        assert mutated == []
    finally:
        release.set()
        for task in (freezing, queued):
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_authorization_waiter_requires_cold_readback_and_retains_physical_lock(tmp_path, monkeypatch):
    import asyncio
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable
    service, signer, namespace, target = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    freezing = closing = None
    cold = None
    try:
        command, snapshot = await proposal(service, namespace, target)
        original = service.store._control_commit

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test publication not released')
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', blocked)
        freezing = asyncio.create_task(service.control.apply(command, approval=sign(service, signer, command, snapshot, 'HANDOFF'),
                                                             snapshot=snapshot))
        assert await asyncio.to_thread(entered.wait, 2)
        freezing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await freezing
        assert service.store._uncertain
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(AccountLockUnavailable, match='ACCOUNT_LOCK_HELD_ELSEWHERE'):
            await maintenance(service, signer)
        release.set()
        await closing
        cold = await maintenance(service, signer)
        assert (await cold.configuration_handoff(cold.binding)).active == command
        assert (await cold.frozen_configuration_source(cold.binding)).content_digest == command.source_digest
    finally:
        release.set()
        for task in (freezing, closing):
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
async def test_expired_facts_after_artifact_publication_do_not_freeze_source(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage import handoff_artifact
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, snapshot = await proposal(service, namespace, target)
        approval = sign(service, signer, command, snapshot, 'HANDOFF')
        before = await service.store.snapshot(service.store.binding)
        original = handoff_artifact.persist_source

        def delayed(*args):
            result = original(*args)
            service.store._clock = lambda: service.sources.clock() + timedelta(seconds=2)
            return result
        monkeypatch.setattr(handoff_artifact, 'persist_source', delayed)
        with pytest.raises(StorageError):
            await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert (await service.store.configuration_handoff(service.store.binding)).active is None
        assert (await service.store.snapshot(service.store.binding)).cut == before.cut
        assert not service.store._uncertain
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['lineage', 'config', 'pool', 'epoch', 'duration', 'anchors'])
async def test_target_reservation_cannot_change_scope_or_shorten_original_session(tmp_path, field):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        if field == 'lineage':
            namespace = replace(namespace, lineage=service.store.binding.lineage)
        elif field == 'config':
            namespace = replace(namespace, config_hash='0' * 64)
        elif field == 'pool':
            namespace = replace(namespace, risk_pool='different')
        elif field == 'epoch':
            target = replace(target, epoch=target.epoch + 1)
        elif field == 'duration':
            target = replace(target, expires_at=target.expires_at + timedelta(seconds=1))
        else:
            target = replace(target, anchors={'LIFE-USDT': D('2')})
        with pytest.raises(StorageError):
            command, snapshot = await proposal(service, namespace, target)
            await service.control.apply(command, approval=sign(service, signer, command, snapshot, 'HANDOFF'), snapshot=snapshot)
        assert (await service.store.configuration_handoff(service.store.binding)).active is None
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['role', 'signature', 'digest', 'sequence', 'target', 'clock'])
async def test_invalid_authorization_preserves_stage_and_native_state(tmp_path, fault):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, snapshot = await proposal(service, namespace, target)
        if fault == 'digest':
            command = replace(command, source_digest='0' * 64)
        elif fault == 'sequence':
            command = replace(command, expected_sequence=99)
        elif fault == 'target':
            command = replace(command, target_binding=replace(namespace, account_uid='999'))
        approval = sign(service, signer, command, snapshot, 'CONFIGURE' if fault == 'role' else 'HANDOFF')
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'clock':
            approval = replace(approval, expires_at=approval.issued_at + timedelta(microseconds=1))
        before = await service.store.snapshot(service.store.binding)
        with pytest.raises(StorageError):
            await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert (await service.store.snapshot(service.store.binding)).cut == before.cut
        assert (await service.store.configuration_handoff(service.store.binding)).active is None
    finally:
        await service.close()
