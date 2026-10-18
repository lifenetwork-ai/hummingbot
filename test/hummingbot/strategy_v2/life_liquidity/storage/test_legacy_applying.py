"""Actual interrupted File money, signed retirement and cold source readback."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS, NOW, facts, open_it
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_settlement import command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import create_fixture
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import RetireLegacyApplying


async def interrupted(tmp_path, monkeypatch, stage, *, mixed=False):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    cmd = command(binding, owner)
    if mixed:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
            command as fill_command,
            prepare_arm,
        )
        await prepare_arm(store, binding, owner, acknowledge=False)
        cmd = replace(cmd, spot_fills=(fill_command(owner),), account_base_life=D('6'), account_quote_usdt=D('8.944'))
    cut = (await store.snapshot(binding)).cut
    target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet')
                      else (store._loss, 'record') if stage == 'loss_after'
                      else (store._subsidy, 'record_fill_floor') if stage == 'floor_after' else (store, '_publish_joint_cash'))
    original = getattr(target, method)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage in ('wallet_after', 'loss_after', 'floor_after'):
            original(*args, **kwargs)
        raise OSError('synthetic interrupted File publication')

    with monkeypatch.context() as patches:
        patches.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_joint_cash(cmd, cut=cut, owner=owner)
    await store.close()
    return await open_it(files, binding, tmp_path), files, binding, owner, cmd, cut


async def drain(store, binding, owner):
    cut = (await store.snapshot(binding)).cut
    cmd = OwnerTransition('drain', OwnerMode.DRAINING, owner)
    evidence = facts(binding)
    signature = KEYS.sign('offline-operator', binding, 'DRAIN', cmd.json(), cut, evidence,
                          issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
    await store.transition_owner(cmd, approval=signature, facts=evidence, cut=cut)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,mixed', [(stage, mixed) for mixed in (False, True)
                                         for stage in ('wallet_before', 'wallet_after', 'loss_after', 'receipt')]
                         + [('floor_after', True)])
async def test_capture_and_retirement_preserve_partial_money_and_original_images(tmp_path, monkeypatch, stage, mixed):
    store, files, binding, owner, original, original_cut = await interrupted(tmp_path, monkeypatch, stage, mixed=mixed)
    try:
        await drain(store, binding, owner)
        before = await store.snapshot(binding)
        evidence = replace(facts(binding), account_base_life=original.account_base_life, account_quote_usdt=original.account_quote_usdt)
        source = await store.legacy_applying_source(binding, facts=evidence, cut=before.cut)
        assert source.pending_operation_ids == ('joint1',)
        assert source.original_operations['joint1']['cut'] == original_cut.json()
        assert source.original_operations['joint1']['command'] == original.json()
        assert (await store.snapshot(binding)).cut == before.cut
        retirement = RetireLegacyApplying('retire1', source.content_digest,
                                          replace(binding, lineage=UUID(int=999)), UUID(int=1000))
        signature = KEYS.sign('offline-operator', binding, 'IMPORT', retirement.json(), before.cut, evidence,
                              issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        receipt = await store.retire_legacy_applying(retirement, source=source, approval=signature)
        assert receipt.state == 'RETIRED' and not receipt.trading_permission
        after = await store.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.components == before.cut.components and after.recovery_required
        assert await store.frozen_legacy_applying_source(binding) == source
        assert (await store.retire_legacy_applying(retirement, source=source, approval=signature)).replayed
        with pytest.raises(StorageError, match='LEGACY_APPLYING_SOURCE_RETIRED'):
            await store.resume_joint_cash(original, cut=original_cut, owner=owner)
    finally:
        await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        assert await cold.frozen_legacy_applying_source(binding) == source
        assert (await cold.snapshot(binding)).balance == before.balance
        cold._clock = lambda: NOW + timedelta(seconds=2)
        assert (await cold.retire_legacy_applying(retirement, source=source, approval=signature)).replayed
        cold._clock = lambda: NOW
        with pytest.raises(StorageError, match='LEGACY_APPLYING_SOURCE_RETIRED'):
            await cold.resume_joint_cash(original, cut=original_cut, owner=owner)
        cmd = OwnerTransition('drain2', OwnerMode.DRAINING, owner)
        cut = (await cold.snapshot(binding)).cut
        signed = KEYS.sign('offline-operator', binding, 'DRAIN', cmd.json(), cut, evidence,
                           issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        with pytest.raises(StorageError, match='LEGACY_APPLYING_SOURCE_RETIRED'):
            await cold.transition_owner(cmd, approval=signed, facts=evidence, cut=cut)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['active', 'no_pending', 'stale_cut', 'incomplete', 'liability', 'future', 'amount', 'signature', 'role', 'target'])
async def test_bad_source_or_retirement_never_changes_native_money(tmp_path, monkeypatch, fault):
    store, _, binding, owner, original, original_cut = await interrupted(tmp_path, monkeypatch, 'wallet_after')
    try:
        if fault == 'no_pending':
            await store.resume_joint_cash(original, cut=original_cut, owner=owner)
        if fault != 'active':
            await drain(store, binding, owner)
        before = await store.snapshot(binding)
        evidence = replace(facts(binding), account_quote_usdt=D('9.964'))
        cut = before.cut
        if fault == 'stale_cut':
            cut = original_cut
        elif fault == 'incomplete':
            evidence = replace(evidence, runner_scope_complete=False)
        elif fault == 'liability':
            evidence = replace(evidence, liability_quote_usdt=D('1'))
        elif fault == 'future':
            evidence = replace(evidence, observed_at=NOW + timedelta(seconds=1))
        elif fault == 'amount':
            evidence = replace(evidence, account_quote_usdt=D('100'))
        with pytest.raises(StorageError):
            source = await store.legacy_applying_source(binding, facts=evidence, cut=cut)
            cmd = RetireLegacyApplying('retire1', source.content_digest,
                                       binding if fault == 'target' else replace(binding, lineage=UUID(int=999)), UUID(int=1000))
            approval = KEYS.sign('offline-operator', binding, 'DRAIN' if fault == 'role' else 'IMPORT', cmd.json(), cut, evidence,
                                 issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
            if fault == 'signature':
                approval = replace(approval, signature='0' * 128)
            await store.retire_legacy_applying(cmd, source=source, approval=approval)
        after = await store.snapshot(binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
    finally:
        await store.close()


async def source_and_command(store, binding, owner):
    await drain(store, binding, owner)
    cut = (await store.snapshot(binding)).cut
    evidence = replace(facts(binding), account_quote_usdt=D('9.964'))
    source = await store.legacy_applying_source(binding, facts=evidence, cut=cut)
    command = RetireLegacyApplying('retire1', source.content_digest, replace(binding, lineage=UUID(int=999)), UUID(int=1000))
    signature = KEYS.sign('offline-operator', binding, 'IMPORT', command.json(), cut, evidence,
                          issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
    return source, command, signature


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_retirement_reply_loss_cold_readback_uses_original_id_and_never_finishes_money(tmp_path, monkeypatch, published):
    store, files, binding, owner, _, _ = await interrupted(tmp_path, monkeypatch, 'wallet_after')
    source, cmd, signature = await source_and_command(store, binding, owner)
    before = await store.snapshot(binding)
    original = store._state.commit

    def crash(state):
        if published:
            original(state)
        raise OSError('synthetic lost retirement publication reply')

    with monkeypatch.context() as patches:
        patches.setattr(store._state, 'commit', crash)
        with pytest.raises(CommitUnknown):
            await store.retire_legacy_applying(cmd, source=source, approval=signature)
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.balance == before.balance and view.budget == before.budget
        if published:
            assert await cold.frozen_legacy_applying_source(binding) == source
        else:
            assert view.cut == before.cut
            with pytest.raises(StorageError, match='LEGACY_APPLYING_RETIREMENT_REQUIRED'):
                await cold.frozen_legacy_applying_source(binding)
        receipt = await cold.retire_legacy_applying(cmd, source=source, approval=signature)
        assert receipt.replayed is published
        assert (await cold.snapshot(binding)).balance == before.balance
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['artifact', 'native', 'coordinator', 'signature', 'artifact_symlink'])
async def test_cold_retired_source_corruption_never_becomes_valid_import_evidence(tmp_path, monkeypatch, fault):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
    store, files, binding, owner, _, _ = await interrupted(tmp_path, monkeypatch, 'wallet_after')
    source, cmd, signature = await source_and_command(store, binding, owner)
    await store.retire_legacy_applying(cmd, source=source, approval=signature)
    _, artifact = store._artifact(cmd)
    await store.close()
    if fault == 'artifact':
        artifact.write_bytes(b'{}')
    elif fault == 'artifact_symlink':
        content = artifact.read_bytes()
        artifact.unlink()
        other = tmp_path / 'other.json'
        other.write_bytes(content)
        artifact.symlink_to(other)
    elif fault == 'native':
        value = json.loads(files.reservations.read_bytes())
        value['usdt_balance'] = '999'
        files.reservations.write_bytes(encode(value))
    else:
        value = json.loads(files.binding.read_bytes())
        if fault == 'coordinator':
            # An unrelated resource not covered by prior money projections is
            # still sealed by the exact source checkpoint digest.
            value['state']['resources']['unexpected'] = {'cash': '999'}
            value['state']['versions']['unexpected'] = 0
        else:
            value['state']['resources']['control_operations']['retire1']['signature'] = '0' * 128
        files.binding.write_bytes(encode(value))
    cold = None
    try:
        with pytest.raises(StorageError):
            cold = await open_it(files, binding, tmp_path)
            await cold.frozen_legacy_applying_source(binding)
        if cold is not None:
            with pytest.raises(StorageError):
                await cold.snapshot(binding)
    finally:
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['artifact', 'physical_commit'])
async def test_facts_expiring_during_publication_cannot_freeze_a_source(tmp_path, monkeypatch, stage):
    store, _, binding, owner, _, _ = await interrupted(tmp_path, monkeypatch, 'wallet_after')
    try:
        source, cmd, signature = await source_and_command(store, binding, owner)
        # Distinct deadline: facts expire before the operator signature does.
        signature = KEYS.sign('offline-operator', binding, 'IMPORT', cmd.json(), source.cut, source.facts,
                              issued_at=NOW, expires_at=NOW + timedelta(seconds=10))
        before = await store.snapshot(binding)
        target = '_persist_applying' if stage == 'artifact' else '_control_commit'
        original = getattr(store, target)

        def expire(*args, **kwargs):
            if stage == 'physical_commit':
                store._clock = lambda: NOW + timedelta(seconds=2)
            result = original(*args, **kwargs)
            store._clock = lambda: NOW + timedelta(seconds=2)
            return result

        with monkeypatch.context() as patches:
            patches.setattr(store, target, expire)
            with pytest.raises(StorageError, match='LEGACY_APPLYING_FACTS'):
                await store.retire_legacy_applying(cmd, source=source, approval=signature)
        store._clock = lambda: NOW
        assert (await store.snapshot(binding)).cut == before.cut
        assert (await store.snapshot(binding)).balance == before.balance
    finally:
        await store.close()
