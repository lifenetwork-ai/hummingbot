"""One signed consumption permanently excludes cancellation and another target."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import freeze, maintenance, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.handoff import (
    CancelConfigurationHandoff,
    ConsumeConfigurationHandoff,
)


async def consuming(service, signer, grant, snapshot, *, operation_id='consume', manifest='a' * 64):
    store = service.store
    view = await store.snapshot(store.binding)
    state = await store.configuration_handoff(store.binding)
    command = ConsumeConfigurationHandoff(operation_id, state.sequence, grant.owner, grant.operation_id, manifest)
    approval = signer.sign('operator', store.binding, 'HANDOFF', command.json(), view.cut, snapshot.facts,
                           issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
    return command, approval, view.cut


@pytest.mark.asyncio
async def test_consumption_keeps_original_money_and_artifact_and_permanently_blocks_cancel(tmp_path):
    service, signer, namespace, target = await setup(tmp_path)
    cold = None
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        before = await service.store.snapshot(service.store.binding)
        command, approval, cut = await consuming(service, signer, grant, snapshot)
        receipt = await service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)
        assert receipt.state == 'SOURCE_CONSUMED' and not receipt.replayed
        state = await service.store.configuration_handoff(service.store.binding)
        assert state.consumed == command and state.active == grant
        after = await service.store.snapshot(service.store.binding)
        assert (after.balance, after.budget, after.session) == (before.balance, before.budget, before.session)
        assert (await service.store.frozen_configuration_source(service.store.binding)).content_digest == grant.source_digest
        cancel = CancelConfigurationHandoff('cannot-cancel', state.sequence, grant.owner, grant.operation_id)
        signature = signer.sign('operator', service.store.binding, 'HANDOFF', cancel.json(), after.cut, snapshot.facts,
                                issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        with pytest.raises(StorageError, match='ALREADY_CONSUMED'):
            await service.store.cancel_configuration_handoff(cancel, approval=signature, facts=snapshot.facts, cut=after.cut)
        assert (await service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)).replayed
        await service.close()
        cold = await maintenance(service, signer)
        assert (await cold.configuration_handoff(cold.binding)).consumed == command
        assert (await cold.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)).replayed
        assert cold._handoff_frozen and cold._cold_unarmed
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['cash', 'debt', 'scope', 'orders', 'sequence', 'role', 'signature', 'artifact', 'expired_target'])
async def test_invalid_consumption_leaves_reversible_freeze_unchanged(tmp_path, fault):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        command, _, cut = await consuming(service, signer, grant, snapshot)
        facts = snapshot.facts
        if fault == 'cash':
            facts = replace(facts, account_quote_usdt=D('99'))
        elif fault == 'debt':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'scope':
            facts = replace(facts, bill_scope_complete=False)
        elif fault == 'orders':
            facts = replace(facts, accepted_wire_ids=('foreign-wire',))
        elif fault == 'sequence':
            command = replace(command, expected_sequence=99)
        elif fault == 'artifact':
            next((service.store._path.parent / 'handoff_sources').glob('*.json')).unlink()
        elif fault == 'expired_target':
            service.store._clock = lambda: target.expires_at
            service.sources = replace(service.sources, clock=lambda: target.expires_at)
            facts = replace(facts, observed_at=target.expires_at)
        approval = signer.sign('operator', service.store.binding, 'CONFIGURE' if fault == 'role' else 'HANDOFF',
                               command.json(), cut, facts, issued_at=service.sources.clock(),
                               expires_at=service.sources.clock() + timedelta(seconds=1))
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        with pytest.raises(StorageError):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        state = await service.store.configuration_handoff(service.store.binding)
        assert state.active == grant and state.consumed is None and state.sequence == 1
        assert (await service.store.snapshot(service.store.binding)).cut == cut
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_lost_consumption_reply_requires_exact_cold_readback(tmp_path, monkeypatch, published):
    service, signer, namespace, target = await setup(tmp_path)
    grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
    command, approval, cut = await consuming(service, signer, grant, snapshot)
    original = service.store._state.commit

    def lost(state):
        if published:
            original(state)
        raise OSError('synthetic lost consumption reply')
    with monkeypatch.context() as patch:
        patch.setattr(service.store._state, 'commit', lost)
        with pytest.raises(CommitUnknown):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)
    await service.close()
    cold = await maintenance(service, signer)
    try:
        assert ((await cold.configuration_handoff(cold.binding)).consumed is not None) == published
        result = await cold.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)
        assert result.replayed is published
        assert (await cold.configuration_handoff(cold.binding)).consumed == command
        assert cold._handoff_frozen and cold._cold_unarmed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_competing_consumers_and_changed_manifest_cannot_retarget_a_consumed_grant(tmp_path):
    import asyncio
    service, signer, namespace, target = await setup(tmp_path)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        left, left_approval, cut = await consuming(service, signer, grant, snapshot)
        right, right_approval, _ = await consuming(service, signer, grant, snapshot, operation_id='other', manifest='b' * 64)
        results = await asyncio.gather(
            service.store.consume_configuration_handoff(left, approval=left_approval, facts=snapshot.facts, cut=cut),
            service.store.consume_configuration_handoff(right, approval=right_approval, facts=snapshot.facts, cut=cut),
            return_exceptions=True)
        assert sum(not isinstance(value, BaseException) for value in results) == 1
        winner = (await service.store.configuration_handoff(service.store.binding)).consumed
        assert winner in (left, right)
        before = (await service.store.snapshot(service.store.binding)).cut
        changed = replace(winner, target_manifest_digest='c' * 64)
        approval = left_approval if winner == left else right_approval
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await service.store.consume_configuration_handoff(changed, approval=approval, facts=snapshot.facts, cut=cut)
        assert (await service.store.snapshot(service.store.binding)).cut == before
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancellation_racing_consumption_cannot_restore_a_consumed_source(tmp_path):
    import asyncio
    service, signer, namespace, target = await setup(tmp_path)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        command, approval, cut = await consuming(service, signer, grant, snapshot)
        cancel = CancelConfigurationHandoff('racing-cancel', 1, grant.owner, grant.operation_id)
        cancellation = signer.sign('operator', service.store.binding, 'HANDOFF', cancel.json(), cut, snapshot.facts,
                                   issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        results = await asyncio.gather(
            service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut),
            service.store.cancel_configuration_handoff(cancel, approval=cancellation, facts=snapshot.facts, cut=cut),
            return_exceptions=True)
        assert results[0].state == 'SOURCE_CONSUMED' and isinstance(results[1], StorageError)
        state = await service.store.configuration_handoff(service.store.binding)
        assert state.consumed == command and state.active == grant
        assert service.store._handoff_frozen and service.store._cold_unarmed
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expiry_at_physical_consumption_commit_preserves_reversible_source(tmp_path, monkeypatch):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        command, approval, cut = await consuming(service, signer, grant, snapshot)
        original = service.store._control_commit

        def expired(*args, **kwargs):
            service.store._clock = lambda: approval.expires_at
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', expired)
        with pytest.raises(StorageError, match='EXPIRED_AT_COMMIT'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)
        state = await service.store.configuration_handoff(service.store.binding)
        assert state.consumed is None and state.active == grant
        assert (await service.store.snapshot(service.store.binding)).cut == cut
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_target_expiring_at_physical_commit_does_not_irreversibly_consume_grant(tmp_path, monkeypatch):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        now = target.expires_at - timedelta(microseconds=500)
        service.store._clock = lambda: now
        service.sources = replace(service.sources, clock=lambda: now)
        snapshot = replace(snapshot, facts=replace(snapshot.facts, observed_at=now))
        command, approval, cut = await consuming(service, signer, grant, snapshot)
        original = service.store._control_commit

        def expired(*args, **kwargs):
            service.store._clock = lambda: target.expires_at
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', expired)
        with pytest.raises(StorageError, match='TARGET_EXPIRED_AT_COMMIT'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=cut)
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is None
        assert (await service.store.snapshot(service.store.binding)).cut == cut
    finally:
        await service.close()
