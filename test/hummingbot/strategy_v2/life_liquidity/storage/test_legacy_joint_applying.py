"""Capture real outer APPLYING before and after core cash completion."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS, facts
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import RetireLegacyApplying


async def interrupted(tmp_path, monkeypatch, stage, *, before_report=None):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, operator_keys=KEYS)
    assert await repo.reconcile(store.binding, owner), recovery.reason_code
    claim = await store._call(send_close, values)
    if before_report is not None:
        restored = await before_report(repo, store, recovery, values, claim, owner)
        if restored is not None:
            repo, store, recovery, values = restored
    report(values, bundle, claim)
    target, name = {
        'outer_before_cash': (repo, 'apply_cash_settlement'),
        'wallet_after': (store._ledger, 'apply_joint_snapshot'),
        'loss_after': (store._loss, 'record'),
        'receipt': (store, '_publish_joint_cash'),
        'cash_done': (recovery, '_apply'),
        'wal_after': (values[5], 'mark_terminal'),
        'shared_after': (values[1], 'settle'),
        **{f'abort_{part}_{when}': target for part, target in {
            'wal': (store._wal, 'abort_before_send'), 'principal': (store._ledger, 'abort_unsent'),
            'subsidy': (store._subsidy, 'release_unsent'), 'receipt': (store, '_publish_lifecycle')}.items()
           for when in ('before', 'after')},
    }[stage]
    original = getattr(target, name)
    fired = False

    def crash(*args, **kwargs):
        nonlocal fired
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage.startswith('abort_receipt_') and not args[0].startswith('lifecycle-done:'):
            return original(*args, **kwargs)
        fired = True
        if stage in ('wallet_after', 'loss_after', 'wal_after', 'shared_after') or stage.startswith('abort_') and stage.endswith('_after'):
            original(*args, **kwargs)
        raise OSError('synthetic original outer saga interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, name, crash)
        assert not await repo.reconcile(store.binding, owner)
        assert fired, recovery.reason_code
    repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
    return repo, store, recovery, values, owner


async def source(store, repo, owner):
    now = datetime.fromtimestamp(repo._recovery.clock_ms() / 1000, timezone.utc)
    binding = store.binding
    evidence = replace(facts(binding), observed_at=now, account_base_life=D('10'), account_quote_usdt=D('9.999'))
    cut = (await store.snapshot(binding)).cut
    drain = OwnerTransition('drain', OwnerMode.DRAINING, owner)
    approval = KEYS.sign('offline-operator', binding, 'DRAIN', drain.json(), cut, evidence,
                         issued_at=now, expires_at=now + timedelta(seconds=1))
    await store.transition_owner(drain, approval=approval, facts=evidence, cut=cut)
    cut = (await store.snapshot(binding)).cut
    captured = await store.legacy_applying_source(binding, facts=evidence, cut=cut, joint=repo)
    cmd = RetireLegacyApplying('retire', captured.content_digest, replace(binding, lineage=UUID(int=999)), UUID(int=1000))
    signature = KEYS.sign('offline-operator', binding, 'IMPORT', cmd.json(), cut, evidence,
                          issued_at=now, expires_at=now + timedelta(seconds=1))
    return captured, cmd, signature


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'loss_after', 'receipt', 'cash_done', 'wal_after', 'shared_after'])
async def test_outer_applying_capture_retains_every_financial_component_without_resuming_it(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, owner = await interrupted(tmp_path, monkeypatch, stage)
    try:
        before = await store.snapshot(store.binding)
        captured, cmd, approval = await source(store, repo, owner)
        assert captured.scope == 'JOINT_ACCOUNTING_APPLYING'
        assert {'joint_recovery', 'shared_capital', 'swap_actions', 'swap_wal', 'requests'} <= {n for n, _, _ in captured.ancillary_images}
        assert captured.pending_operation_ids == ('joint:' + captured.joint_state['proof_id'],)
        assert captured.joint_state['phase'] == 'APPLYING'
        assert (await store.snapshot(store.binding)).balance == before.balance
        assert (await store.snapshot(store.binding)).budget == before.budget
        original_claims = await store._call(recovery.capital.claim_ids)
        assert not (await store.retire_legacy_applying(cmd, source=captured, approval=approval, joint=repo)).trading_permission
        assert await store.frozen_legacy_applying_source(store.binding) == captured
        assert not await repo.reconcile(store.binding, owner)
        assert await store.frozen_legacy_applying_source(store.binding) == captured
        assert len(values[4].sent) == 1
        assert not recovery._armed and not values[2]._runtime_enabled
        assert (await store.snapshot(store.binding)).balance == before.balance
        # The sealed capsule retains the actual partially released or retained
        # claims; retirement never decides that an order is complete.
        import json
        capital = next(raw for name, _, raw in captured.ancillary_images if name == 'shared_capital')
        assert tuple(sorted(json.loads(capital)['state']['claims'])) == original_claims
    finally:
        await store.close()
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import (
        BUDGET,
        CAPACITY,
        open_legacy_spot,
    )
    cold = await open_legacy_spot(store._files, binding=store.binding, limits=CAPACITY,
                                  risk_limits=store._risk_limits, subsidy_policy=BUDGET, loss_policy=BUDGET,
                                  config_version=1, account_lock_root=tmp_path / 'account-locks',
                                  clock=lambda: values[6].wall, fill_policy=store._fill_policy,
                                  accounting_policy=store._accounting_policy, recovery_policy=store._recovery_policy,
                                  operator_keys=KEYS)
    try:
        assert await cold.frozen_legacy_applying_source(cold.binding) == captured
        assert (await cold.retire_legacy_applying(cmd, source=captured, approval=approval)).replayed
        assert (await cold.snapshot(cold.binding)).balance == before.balance
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_ancillary_change_at_physical_retirement_cannot_publish_a_fence(tmp_path, monkeypatch):
    repo, store, _, _, owner = await interrupted(tmp_path, monkeypatch, 'cash_done')
    original_bytes = None
    try:
        captured, cmd, approval = await source(store, repo, owner)
        before = await store.snapshot(store.binding)
        from pathlib import Path
        path = Path(next(path for name, path, _ in captured.ancillary_images if name == 'swap_wal'))
        original_bytes = path.read_bytes()
        original = store._control_commit

        def change_at_commit(*args, **kwargs):
            path.write_bytes(original_bytes + b'\n')
            return original(*args, **kwargs)

        with monkeypatch.context() as patches:
            patches.setattr(store, '_control_commit', change_at_commit)
            with pytest.raises(StorageError, match='LEGACY_JOINT_APPLYING_SOURCE_CHANGED'):
                await store.retire_legacy_applying(cmd, source=captured, approval=approval, joint=repo)
        assert (await store.snapshot(store.binding)).cut == before.cut
    finally:
        if original_bytes is not None:
            path.write_bytes(original_bytes)
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['joint_recovery', 'shared_capital', 'swap_actions', 'swap_wal', 'requests'])
async def test_retired_ancillary_drift_is_detected_before_any_readback_or_retry(tmp_path, monkeypatch, component):
    from pathlib import Path
    repo, store, _, _, owner = await interrupted(tmp_path, monkeypatch, 'wal_after')
    try:
        captured, cmd, approval = await source(store, repo, owner)
        await store.retire_legacy_applying(cmd, source=captured, approval=approval, joint=repo)
        path = Path(next(path for name, path, _ in captured.ancillary_images if name == component))
        path.write_bytes(path.read_bytes() + b'\n')
        with pytest.raises(StorageError, match='LEGACY_JOINT_APPLYING_SOURCE_CHANGED'):
            await store.frozen_legacy_applying_source(store.binding)
        with pytest.raises(StorageError, match='LEGACY_JOINT_APPLYING_SOURCE_CHANGED'):
            await store.retire_legacy_applying(cmd, source=captured, approval=approval)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['omit_component', 'redirect_path', 'changed_phase', 'changed_trade', 'changed_source', 'foreign_owner'])
async def test_changed_joint_source_or_repository_cannot_replace_original_capsule(tmp_path, monkeypatch, fault):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
    repo, store, _, _, owner = await interrupted(tmp_path, monkeypatch, 'wallet_after')
    try:
        captured, cmd, approval = await source(store, repo, owner)
        before = await store.snapshot(store.binding)
        images = list(captured.ancillary_images)
        with pytest.raises(StorageError):
            if fault == 'foreign_owner':
                repo.owner = replace(owner, epoch=owner.epoch + 1)
            else:
                i = next(i for i, (name, _, _) in enumerate(images) if name == 'joint_recovery')
                name, path, raw = images[i]
                value = json.loads(raw)
                if fault == 'omit_component':
                    images = [row for row in images if row[0] != 'requests']
                elif fault == 'redirect_path':
                    images[i] = (name, str(tmp_path / 'other.json'), raw)
                else:
                    if fault == 'changed_phase':
                        value['state']['phase'] = 'COMMITTED'
                    elif fault == 'changed_trade':
                        value['state']['bundle']['orders'][0]['trades'][0]['signed_fee'] = '1'
                    else:
                        value['state']['bundle']['source'] = 'different'
                    images[i] = (name, path, encode(value))
                captured = replace(captured, ancillary_images=tuple(images))
            await store.retire_legacy_applying(cmd, source=captured, approval=approval, joint=repo)
        after = await store.snapshot(store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_source_unpack_is_independent_of_callers_decimal_context(tmp_path, monkeypatch):
    from decimal import ROUND_DOWN, localcontext

    from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import LegacyApplyingSource
    repo, store, _, _, owner = await interrupted(tmp_path, monkeypatch, 'loss_after')
    try:
        captured, _, _ = await source(store, repo, owner)
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 2, ROUND_DOWN
            restored = LegacyApplyingSource.unpack(captured.pack())
        assert restored == captured and restored.content_digest == captured.content_digest
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_configured_file_joint_capture_includes_actual_hedge_and_carry_origins(tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import control_policy
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode

    def extra(config, sources):
        return {'control_seed': lambda b, o: encode(KEYS.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                'joint_spot_recovery': lambda: ((), replace(facts(config.controller.storage_binding), observed_at=sources.clock()))}

    service, config, _, _, _ = await joint_service(tmp_path, carry=True, config_extra={'control': control_policy(KEYS)}, source_extra=extra)
    store, repo = service.store, service.joint
    try:
        recovery = repo._recovery
        original = recovery.journal.commit

        def interrupted_completion(state):
            if state['phase'] == 'COMMITTED':
                raise OSError('synthetic final outer receipt loss')
            return original(state)

        with monkeypatch.context() as patches:
            patches.setattr(recovery.journal, 'commit', interrupted_completion)
            assert not await repo.reconcile(store.binding, config.owner)
        now = service.sources.clock()
        evidence = replace(facts(store.binding), observed_at=now)
        before = await store.snapshot(store.binding)
        drain = OwnerTransition('drain', OwnerMode.DRAINING, config.owner)
        approval = KEYS.sign('offline-operator', store.binding, 'DRAIN', drain.json(), before.cut, evidence,
                             issued_at=now, expires_at=now + timedelta(seconds=1))
        await store.transition_owner(drain, approval=approval, facts=evidence, cut=before.cut)
        cut = (await store.snapshot(store.binding)).cut
        captured = await store.legacy_applying_source(store.binding, facts=evidence, cut=cut, joint=repo)
        assert {name for name, _, _ in captured.ancillary_images} == {
            'joint_recovery', 'shared_capital', 'swap_actions', 'swap_wal', 'requests', 'hedge', 'carry'}
        assert (await store.snapshot(store.binding)).balance == before.balance
        assert (await store.snapshot(store.binding)).budget == before.budget
        cmd = RetireLegacyApplying('retire-configured', captured.content_digest, replace(store.binding, lineage=UUID(int=999)), UUID(int=1000))
        approval = KEYS.sign('offline-operator', store.binding, 'IMPORT', cmd.json(), cut, evidence,
                             issued_at=now, expires_at=now + timedelta(seconds=1))
        await store.retire_legacy_applying(cmd, source=captured, approval=approval, joint=repo)
        assert await store.frozen_legacy_applying_source(store.binding) == captured
    finally:
        await service.close()
