"""Actual protected SWAP preparation/arming/ACK belongs to one file worker."""
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_repository import fixture
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

import pytest

from hummingbot.core.data_type.common import PositionAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap, open_file_swap


@pytest.mark.asyncio
async def test_prepare_arm_ack_keep_native_claim_and_permanent_identity(tmp_path):
    joint, store, recovery, values, _, _, owner = await fixture(tmp_path)
    try:
        assert await joint.reconcile(store.binding, owner)
        repo = await open_file_swap(joint)
        config = values[11].model_copy(update={'amount': values[11].amount / 12, 'position_action': PositionAction.CLOSE})
        command = PrepareSwap.from_config('prepare1', 'HBOTSwap1', config)
        receipt = await repo.prepare(command, cut=await repo.cut(store.binding), owner=owner)
        assert receipt.state == 'PREPARED' and not hasattr(receipt, 'send_authorized')
        assert values[5].get(config.id).state == 'PREPARED'
        assert values[1].claim_ids() == (config.id,)
        await repo.arm(command.operation_id, cut=await repo.cut(store.binding), owner=owner)
        assert values[5].get(config.id).state == 'SEND_UNKNOWN'
        await repo.authorize_boundary(command.operation_id, wire_payload=encode(wire_for(config, 'HBOTSwap1',
                                                                                         receipt.permit.position_mode)), owner=owner)
        await repo.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
        assert values[5].get(config.id).state == 'ACKED'
        assert values[1].claim_ids() == (config.id,)
    finally:
        await store.close()


async def prepared(tmp_path):
    joint, store, recovery, values, _, _, owner = await fixture(tmp_path)
    assert await joint.reconcile(store.binding, owner)
    repo = await open_file_swap(joint)
    config = values[11].model_copy(update={'amount': values[11].amount / 12, 'position_action': PositionAction.CLOSE})
    command = PrepareSwap.from_config('prepare1', 'HBOTSwap1', config)
    return repo, joint, store, recovery, values, owner, command


async def restart(tmp_path, joint, store, recovery, values, owner):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import cold_restore

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend
    from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
    from hummingbot.strategy_v2.life_liquidity.storage.joint import open_file_joint
    await store.close()
    native, fresh = cold_restore(recovery, values)
    restored = await open_store(Backend.FILE, path=tmp_path / 'joint-owner.json', binding=store.binding,
                                limits=CAPACITY, create=False, clock=lambda: fresh[6].wall)
    coldjoint = await open_file_joint(restored, recovery=native, owner=owner, limits=joint.limits)
    return await open_file_swap(coldjoint), restored, native, fresh


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['claim_before', 'claim_after', 'wal_before', 'wal_after', 'actions_after', 'receipt'])
async def test_cold_prepare_repairs_only_original_bookkeeping_without_permission(tmp_path, monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    repo, joint, store, recovery, values, owner, command = await prepared(tmp_path)
    target, name = {'claim_before': (values[1], 'swap'), 'claim_after': (values[1], 'swap'),
                    'wal_before': (values[5], 'begin'), 'wal_after': (values[5], 'begin'),
                    'actions_after': (repo.sender.journal, 'commit'), 'receipt': (repo, '_publish')}[stage]
    original = getattr(target, name)

    def interrupted(*args, **kwargs):
        if stage == 'receipt' and '-done:' not in args[2]:
            return original(*args, **kwargs)
        if stage == 'actions_after' and command.config().id not in args[0]['actions']:
            return original(*args, **kwargs)
        if stage.endswith('after'):
            original(*args, **kwargs)
        raise OSError('synthetic checkpoint interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, name, interrupted)
        with pytest.raises(CommitUnknown):
            await repo.prepare(command, cut=await repo.cut(store.binding), owner=owner)
    cold, restored, native, fresh = await restart(tmp_path, joint, store, recovery, values, owner)
    try:
        receipt = await cold.resume_prepare(command.operation_id, owner=owner)
        assert receipt.state == ('REFUSED' if stage == 'claim_before' else 'PREPARED')
        assert (await cold.resume_prepare(command.operation_id, owner=owner)).replayed
        assert not native._armed and not fresh[2]._runtime_enabled
        assert fresh[2]._permits == fresh[2]._configs == {}
        assert fresh[4].sent == []
        with pytest.raises(StorageError):
            await cold.arm(command.operation_id, cut=await cold.cut(restored.binding), owner=owner)
        assert fresh[1].claim_ids() == (() if stage == 'claim_before' else (command.config().id,))
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['arm', 'ack'])
@pytest.mark.parametrize('stage', ['before', 'after', 'receipt'])
async def test_cold_arm_ack_preserve_unknown_and_hold_without_resend(tmp_path, monkeypatch, phase, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    repo, joint, store, recovery, values, owner, command = await prepared(tmp_path)
    receipt = await repo.prepare(command, cut=await repo.cut(store.binding), owner=owner)
    wire = encode(wire_for(command.config(), command.client_order_id, receipt.permit.position_mode))
    if phase == 'ack':
        await repo.arm(command.operation_id, cut=await repo.cut(store.binding), owner=owner)
        await repo.authorize_boundary(command.operation_id, wire_payload=wire, owner=owner)
    target, name = (repo, '_publish') if stage == 'receipt' else (values[5], 'arm_send' if phase == 'arm' else 'acknowledge')
    original = getattr(target, name)

    def interrupted(*args, **kwargs):
        if stage == 'receipt' and '-done:' not in args[2]:
            return original(*args, **kwargs)
        if stage == 'after':
            original(*args, **kwargs)
        raise OSError('synthetic checkpoint interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, name, interrupted)
        with pytest.raises(CommitUnknown):
            if phase == 'arm':
                await repo.arm(command.operation_id, cut=await repo.cut(store.binding), owner=owner)
            else:
                await repo.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
    cold, restored, native, fresh = await restart(tmp_path, joint, store, recovery, values, owner)
    try:
        if phase == 'arm':
            receipt = await cold.resume_arm(command.operation_id, owner=owner)
            assert receipt.state == 'SEND_UNKNOWN'
        else:
            receipt = await cold.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
            assert receipt.state == 'ACKED'
            assert (await cold.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)).replayed
            with pytest.raises(StorageError):
                await cold.acknowledge(command.operation_id, exchange_order_id='999', owner=owner)
        assert fresh[1].claim_ids() == (command.config().id,)
        assert fresh[4].sent == [] and not native._armed
        assert fresh[2].unresolved_order_ids() == (command.client_order_id,)
        assert fresh[2]._permits == fresh[2]._configs == {}
        with pytest.raises(StorageError):
            await cold.authorize_boundary(command.operation_id, wire_payload=wire, owner=owner)
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('after_charge', [False, True])
async def test_boundary_charge_failure_is_durable_and_cannot_be_replayed(tmp_path, monkeypatch, after_charge):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    repo, joint, store, recovery, values, owner, command = await prepared(tmp_path)
    receipt = await repo.prepare(command, cut=await repo.cut(store.binding), owner=owner)
    await repo.arm(command.operation_id, cut=await repo.cut(store.binding), owner=owner)
    wire = encode(wire_for(command.config(), command.client_order_id, receipt.permit.position_mode))
    original = repo.sender.request_budget.charge

    def interrupted(*args, **kwargs):
        if after_charge:
            original(*args, **kwargs)
        raise OSError('synthetic charge unavailable')

    with monkeypatch.context() as patches:
        patches.setattr(repo.sender.request_budget, 'charge', interrupted)
        with pytest.raises(CommitUnknown):
            await repo.authorize_boundary(command.operation_id, wire_payload=wire, owner=owner)
    assert store._uncertain
    cold, restored, _, fresh = await restart(tmp_path, joint, store, recovery, values, owner)
    try:
        with pytest.raises(StorageError):
            await cold.authorize_boundary(command.operation_id, wire_payload=wire, owner=owner)
        with pytest.raises(StorageError, match='SWAP_ORIGINAL_ACK_REQUIRED'):
            await cold.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
        assert fresh[5].get(command.config().id).state == 'SEND_UNKNOWN'
        assert fresh[1].claim_ids() == (command.config().id,) and fresh[4].sent == []
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_ack_recovery_rejects_changed_native_record_and_foreign_owner(tmp_path, monkeypatch):
    from dataclasses import replace
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    repo, joint, store, recovery, values, owner, command = await prepared(tmp_path)
    receipt = await repo.prepare(command, cut=await repo.cut(store.binding), owner=owner)
    await repo.arm(command.operation_id, cut=await repo.cut(store.binding), owner=owner)
    await repo.authorize_boundary(command.operation_id, owner=owner, wire_payload=encode(
        wire_for(command.config(), command.client_order_id, receipt.permit.position_mode)))

    def interrupted(*_):
        raise OSError('synthetic ACK unavailable')

    with monkeypatch.context() as patches:
        patches.setattr(values[5], 'acknowledge', interrupted)
        with pytest.raises(CommitUnknown):
            await repo.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
    cold, restored, _, fresh = await restart(tmp_path, joint, store, recovery, values, owner)
    try:
        with pytest.raises(StorageError, match='SWAP_OWNER_MISMATCH'):
            await cold.acknowledge(command.operation_id, exchange_order_id='123456789',
                                   owner=replace(owner, generation=uuid4()))
        await restored._call(fresh[5].mark_cancel_requested, command.config().id)
        with pytest.raises(StorageError, match='SWAP_ACK_RECOVERY_NATIVE_MISMATCH'):
            await cold.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
        assert fresh[5].get(command.config().id).state == 'SEND_UNKNOWN'
        assert fresh[1].claim_ids() == (command.config().id,)
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_prepare_replay_stale_cut_and_conflicting_identity_preserve_claim(tmp_path):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, _, store, _, values, owner, command = await prepared(tmp_path)
    try:
        cut = await repo.cut(store.binding)
        receipt = await repo.prepare(command, cut=cut, owner=owner)
        assert (await repo.prepare(command, cut=cut, owner=owner)).replayed
        with pytest.raises(StorageError, match='SWAP_OPERATION_CONFLICT'):
            await repo.prepare(replace(command, client_order_id='HBOTChanged'), cut=cut, owner=owner)
        with pytest.raises(StorageError, match='SWAP_CUT_STALE'):
            await repo.arm(command.operation_id, cut=cut, owner=owner)
        with pytest.raises(StorageError, match='SWAP_ACK_WITHOUT_BOUNDARY'):
            await repo.acknowledge(command.operation_id, exchange_order_id='123456789', owner=owner)
        assert values[5].get(command.config().id).state == receipt.state == 'PREPARED'
        assert values[1].claim_ids() == (command.config().id,)
    finally:
        await store.close()
