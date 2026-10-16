"""Signed exact-source normalization uses real archive readers and cold locks."""
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import freeze, maintenance, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.archive_maintenance import ArchiveRepair, open_archive_maintenance
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def archived(tmp_path, component):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_target_import import paid_history

    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
    service, signer, namespace, target = await setup(tmp_path, joint=component in ('shared_capital', 'swap_wal'),
                                                     before_stage=paid_history if component in ('wal', 'reservations') else None,
                                                     quote_kwargs={'recovery_policy': SpotFinancialRecoveryPolicy(10000)})
    grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
    source = await service.store.frozen_configuration_source(service.store.binding)
    await service.close()
    if component in ('wal', 'swap_wal', 'reservations'):
        from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import capacity
        policy = capacity('wal' if component in ('wal', 'swap_wal') else 'risk')
        native = service.swap.sender.wal if component == 'swap_wal' else service.store._wal if component == 'wal' else service.store._ledger
    else:
        from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
        policy = capacity()
        native = service.joint._recovery.capital.journal
    native.migrate_archive(policy)
    raw = native.path.read_bytes()
    command = ArchiveRepair('repair-' + component, grant.operation_id, source.content_digest, component,
                            hashlib.sha256(raw).hexdigest(), hashlib.sha256(service.store._path.read_bytes()).hexdigest(),
                            json.dumps(policy.json()))
    return service, signer, source, snapshot.facts, command, native.path


async def authorized(service, signer, command, facts):
    store = await open_archive_maintenance(service.config, clock=service.sources.clock)
    cut = await store.repair_cut()
    approval = signer.sign('operator', store.binding, 'IMPORT', command.json(), cut, facts,
                           issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
    return store, cut, approval


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['wal', 'reservations', 'shared_capital', 'swap_wal'])
async def test_archived_source_normalizes_exact_bytes_and_cold_readback_stays_unarmed(tmp_path, component):
    service, signer, source, facts, command, path = await archived(tmp_path, component)
    store, cut, approval = await authorized(service, signer, command, facts)
    expected = dict(source.core.journal_images if component in ('wal', 'reservations') else source.ancillary_images)[component]
    root = service.store._path.read_bytes()
    try:
        result = await store.repair(command, approval=approval, facts=facts, cut=cut)
        assert result.state == 'SOURCE_NORMALIZED' and not result.replayed
        assert path.read_bytes() == expected and service.store._path.read_bytes() == root
        assert (await store.repair(command, approval=approval, facts=facts, cut=cut)).replayed
    finally:
        await store.close()
    cold = await maintenance(service, signer)
    try:
        assert cold._cold_unarmed and cold._handoff_frozen
        view = await cold.snapshot(cold.binding)
        assert view.balance.total_base_life == facts.account_base_life
        assert view.balance.total_quote_usdt == facts.account_quote_usdt
        assert view.session.config_version == 1
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'cash', 'scope', 'expired', 'source', 'before', 'root', 'policy', 'segment'])
async def test_unqualified_archive_repair_changes_nothing(tmp_path, fault):
    service, signer, source, facts, command, path = await archived(tmp_path, 'shared_capital')
    if fault == 'source':
        command = replace(command, source_digest='a' * 64)
    elif fault == 'before':
        command = replace(command, before_sha256='a' * 64)
    elif fault == 'root':
        command = replace(command, source_root_sha256='a' * 64)
    elif fault == 'policy':
        policy = json.loads(command.archive_policy_json)
        policy['max_segments'] += 1
        command = replace(command, archive_policy_json=json.dumps(policy))
    elif fault == 'segment':
        # Empty archives have no segment. Tamper the manifest coverage instead.
        value = json.loads(path.read_bytes())
        value['archives'][0]['count'] += 1
        path.write_text(json.dumps(value))
        command = replace(command, before_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if fault == 'cash':
        facts = replace(facts, account_quote_usdt=facts.account_quote_usdt + 1)
    elif fault == 'scope':
        facts = replace(facts, order_scope_complete=False)
    store, cut, approval = await authorized(service, signer, command, facts)
    if fault == 'signature':
        approval = replace(approval, signature='0' * 128)
    elif fault == 'role':
        approval = signer.sign('operator', store.binding, 'HANDOFF', command.json(), cut, facts,
                               issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
    elif fault == 'expired':
        store._clock = lambda: service.sources.clock() + timedelta(seconds=2)
    before, root = path.read_bytes(), service.store._path.read_bytes()
    try:
        with pytest.raises(StorageError):
            await store.repair(command, approval=approval, facts=facts, cut=cut)
        assert path.read_bytes() == before and service.store._path.read_bytes() == root
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_archive_publication_unknown_cold_resumes_exact_original_without_rearming(tmp_path, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.storage import archive_maintenance
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    service, signer, source, facts, command, path = await archived(tmp_path, 'wal')
    store, cut, approval = await authorized(service, signer, command, facts)
    original = archive_maintenance.restore_exact

    def lost(*args):
        if committed:
            original(*args)
        raise CommitUnknown('SYNTHETIC_LOST_REPAIR_REPLY')
    with monkeypatch.context() as patch:
        patch.setattr(archive_maintenance, 'restore_exact', lost)
        with pytest.raises(CommitUnknown):
            await store.repair(command, approval=approval, facts=facts, cut=cut)
    assert store._uncertain
    await store.close()
    cold = await open_archive_maintenance(service.config, clock=service.sources.clock)
    try:
        receipt = await cold.repair(command, approval=approval, facts=facts, cut=cut)
        assert receipt.replayed is committed and cold._cold_unarmed
        assert path.read_bytes() == dict(source.core.journal_images)['wal']
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'symlink'])
async def test_real_archive_segment_failures_are_never_repaired_from_unverified_data(tmp_path, fault):
    service, signer, source, facts, command, path = await archived(tmp_path, 'wal')
    segment = next(path.with_name(path.name + '.segments').glob('*.json'))
    if fault == 'missing':
        segment.unlink()
    elif fault == 'corrupt':
        segment.write_bytes(b'{}')
    else:
        target = tmp_path / 'copied-segment'
        target.write_bytes(segment.read_bytes())
        segment.unlink()
        segment.symlink_to(target)
    store, cut, approval = await authorized(service, signer, command, facts)
    before = path.read_bytes()
    try:
        with pytest.raises(StorageError, match='ARCHIVE_UNVERIFIED'):
            await store.repair(command, approval=approval, facts=facts, cut=cut)
        assert path.read_bytes() == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [False, True])
async def test_signed_plain_native_mismatch_restores_only_original_sealed_bytes(tmp_path, missing):
    service, signer, source, facts, command, path = await archived(tmp_path, 'reservations')
    before = None if missing else b'{"unknown": "never adopted"}'
    if missing:
        path.unlink()
    else:
        path.write_bytes(before)
    command = replace(command, archive_policy_json=None, before_sha256=None if missing else hashlib.sha256(before).hexdigest())
    store, cut, approval = await authorized(service, signer, command, facts)
    try:
        await store.repair(command, approval=approval, facts=facts, cut=cut)
        assert path.read_bytes() == dict(source.core.journal_images)['reservations']
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_repair_and_shutdown_retain_physical_worker_and_account_lock(tmp_path, monkeypatch):
    import asyncio
    import threading

    from hummingbot.strategy_v2.life_liquidity.storage import archive_maintenance
    service, signer, source, facts, command, path = await archived(tmp_path, 'wal')
    store, cut, approval = await authorized(service, signer, command, facts)
    entered, release = threading.Event(), threading.Event()
    original = archive_maintenance.restore_exact

    def blocked(*args):
        entered.set()
        if not release.wait(10):
            raise AssertionError('synthetic repair was not released')
        return original(*args)
    monkeypatch.setattr(archive_maintenance, 'restore_exact', blocked)
    task = asyncio.create_task(store.repair(command, approval=approval, facts=facts, cut=cut))
    closer = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closer = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closer.done() and store._account_lock._fd is not None
        from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable
        with pytest.raises(AccountLockUnavailable, match='ACCOUNT_LOCK_HELD_ELSEWHERE'):
            await open_archive_maintenance(service.config, clock=service.sources.clock)
        release.set()
        await closer
        assert store._account_lock._fd is None
        assert path.read_bytes() == dict(source.core.journal_images)['wal']
    finally:
        release.set()
        if closer is not None:
            await closer
        await store.close()


@pytest.mark.asyncio
async def test_completed_repair_operation_cannot_be_reused_for_another_component(tmp_path):
    service, signer, source, facts, command, path = await archived(tmp_path, 'wal')
    store, cut, approval = await authorized(service, signer, command, facts)
    try:
        await store.repair(command, approval=approval, facts=facts, cut=cut)
        other = replace(command, component='reservations', archive_policy_json=None,
                        before_sha256=hashlib.sha256(service.store._files.reservations.read_bytes()).hexdigest())
        current = await store.repair_cut()
        signed = signer.sign('operator', store.binding, 'IMPORT', other.json(), current, facts,
                             issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await store.repair(other, approval=signed, facts=facts, cut=current)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expiry_after_repair_temporary_file_fsync_does_not_replace_native_state(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage import archive_maintenance
    service, signer, source, facts, command, path = await archived(tmp_path, 'wal')
    store, cut, approval = await authorized(service, signer, command, facts)
    original = archive_maintenance.restore_exact

    def delayed(path, before, after, guard):
        def expired():
            store._clock = lambda: service.sources.clock() + timedelta(seconds=2)
            guard()
        return original(path, before, after, expired)
    monkeypatch.setattr(archive_maintenance, 'restore_exact', delayed)
    before = path.read_bytes()
    try:
        with pytest.raises(StorageError):
            await store.repair(command, approval=approval, facts=facts, cut=cut)
        assert path.read_bytes() == before and not store._uncertain
    finally:
        await store.close()
