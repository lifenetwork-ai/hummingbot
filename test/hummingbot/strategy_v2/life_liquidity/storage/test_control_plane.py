"""Signed, cut-bound operator commands; snapshots and cold reads never activate."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import ACCOUNTING, transfer
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    POLICY,
    RISK,
    create_fixture,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    RECOVERY,
    facts as financial_facts,
)
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import (
    ApproveSpotCashflow,
    OperatorKeys,
    OperatorVerifier,
    OwnerTransition,
)
from hummingbot.strategy_v2.life_liquidity.storage.import_commands import (
    ImportApprovedCashflows,
    ImportPreparationOrigin,
)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot

KEYS = OperatorKeys({'offline-operator': b's' * 32},
                    {'offline-operator': frozenset(('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE', 'APPROVE_CASHFLOW', 'IMPORT'))})


async def open_it(files, binding, path, owner=None, create=False):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=path / 'account-locks', owner=owner, create_binding=create,
                                  clock=lambda: NOW, fill_policy=POLICY, accounting_policy=ACCOUNTING,
                                  recovery_policy=RECOVERY, operator_keys=KEYS)


def facts(binding):
    return financial_facts(binding, kind='cash', account_quote_usdt=D('10'), account_cashflow_ids=())


@pytest.mark.asyncio
async def test_signed_recover_after_revoke_restores_only_latest_writer_observation_not_admission(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    revoke = OwnerTransition('revoke', OwnerMode.UNARMED, None)
    before = await store.snapshot(binding)
    evidence = facts(binding)
    approval = KEYS.sign('offline-operator', binding, 'REVOKE', revoke.json(), before.cut, evidence,
                         issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
    await store.transition_owner(revoke, approval=approval, facts=evidence, cut=before.cut)
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        recover = OwnerTransition('recover', OwnerMode.RECOVERY_ONLY, owner)
        view = await cold.snapshot(binding)
        wrong = replace(recover, token=replace(owner, generation=UUID(int=99)))
        wrong_signature = KEYS.sign('offline-operator', binding, 'RECOVER', wrong.json(), view.cut, evidence,
                                    issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        with pytest.raises(StorageError, match='CONTROL_OBSERVATION_OWNER_MISMATCH'):
            await cold.transition_owner(wrong, approval=wrong_signature, facts=evidence, cut=view.cut)
        signature = KEYS.sign('offline-operator', binding, 'RECOVER', recover.json(), view.cut, evidence,
                              issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        await cold.transition_owner(recover, approval=signature, facts=evidence, cut=view.cut)
        assert cold._cold_unarmed
        await cold.acknowledge(ack(owner), cut=(await cold.snapshot(binding)).cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == D('2')
        assert not (await cold.check_spot_send(order(owner), owner=owner)).qualified
        assert (await cold.transition_owner(recover, approval=signature, facts=evidence, cut=view.cut)).replayed
        assert cold._cold_unarmed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cold_signed_new_generation_activation_requires_exact_account_cut_and_replays_without_rearm(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    new_owner = replace(owner, process_id=UUID(int=4), generation=UUID(int=5))
    try:
        before = await cold.snapshot(binding)
        command = OwnerTransition('activate1', OwnerMode.ACTIVE, new_owner)
        approval = KEYS.sign('offline-operator', binding, 'ACTIVATE', command.json(), before.cut, facts(binding),
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        receipt = await cold.transition_owner(command, approval=approval, facts=facts(binding), cut=before.cut)
        assert not receipt.replayed and (await cold.current_owner(binding)).token == new_owner
        assert not (await cold.snapshot(binding)).recovery_required
        assert (await cold.transition_owner(command, approval=approval, facts=facts(binding), cut=before.cut)).replayed
    finally:
        await cold.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        assert (await cold.snapshot(binding)).recovery_required
        assert (await cold.transition_owner(command, approval=approval, facts=facts(binding), cut=before.cut)).replayed
        assert (await cold.snapshot(binding)).recovery_required  # Readback cannot activate a process.
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'cut', 'balance', 'generation', 'future', 'expired', 'role'])
async def test_bad_signed_activation_never_changes_owner_or_unarms_gate(tmp_path, fault):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        command = OwnerTransition('activate1', OwnerMode.ACTIVE, replace(owner, process_id=UUID(int=4), generation=UUID(int=5)))
        evidence = facts(binding)
        approval = KEYS.sign('offline-operator', binding, 'ACTIVATE', command.json(), before.cut, evidence,
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'cut':
            command = replace(command, operation_id='other')
        elif fault == 'balance':
            evidence = replace(evidence, account_base_life=D('6'))
        elif fault == 'generation':
            command = replace(command, token=owner)
            approval = KEYS.sign('offline-operator', binding, 'ACTIVATE', command.json(), before.cut, evidence,
                                 issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        else:
            approval = KEYS.sign('offline-operator', binding, 'DRAIN' if fault == 'role' else 'ACTIVATE',
                                 command.json(), before.cut, evidence,
                                 issued_at=NOW + timedelta(seconds=1) if fault == 'future' else NOW - timedelta(seconds=2),
                                 expires_at=NOW + timedelta(seconds=2) if fault == 'future' else NOW if fault == 'expired'
                                 else NOW + timedelta(seconds=1))
        with pytest.raises(StorageError):
            await cold.transition_owner(command, approval=approval, facts=evidence, cut=before.cut)
        after = await cold.snapshot(binding)
        assert after.cut == before.cut and after.recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_signed_cashflow_approval_survives_restart_and_cannot_change_existing_terms(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    try:
        assert isinstance(store._operator_keys, OperatorVerifier) and store._operator_seed_signer is None
        assert not hasattr(store._operator_keys, 'sign')
        before = await store.snapshot(binding)
        bill = ApprovedSpotCashflow('105', 'USDT', D('2'), int(NOW.timestamp() * 1000))
        command = ApproveSpotCashflow('approve1', bill)
        approval = KEYS.sign('offline-operator', binding, 'APPROVE_CASHFLOW', command.json(), before.cut, facts(binding),
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        assert not (await store.approve_cashflow(command, approval=approval, facts=facts(binding), cut=before.cut)).replayed
        assert (await store.snapshot(binding)).balance == before.balance
        assert (await store.approve_cashflow(command, approval=approval, facts=facts(binding), cut=before.cut)).replayed
        await store.apply_cashflow(transfer(operation_id='cash105', bill_id='105'),
                                   cut=(await store.snapshot(binding)).cut, owner=owner)
        assert (await store.snapshot(binding)).balance.total_quote_usdt == D('12')
        changed = ApproveSpotCashflow('approve2', replace(bill, signed_amount=D('3')))
        current = await store.snapshot(binding)
        observed = replace(facts(binding), account_quote_usdt=D('12'), account_cashflow_ids=('105',))
        approved = KEYS.sign('offline-operator', binding, 'APPROVE_CASHFLOW', changed.json(), current.cut, observed,
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        with pytest.raises(StorageError):
            await store.approve_cashflow(changed, approval=approved, facts=observed, cut=current.cut)
        assert (await store.snapshot(binding)).cut == current.cut
    finally:
        await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('12')
        assert (await cold.snapshot(binding)).recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ownership', 'approval', 'seed'])
async def test_rewriting_checksum_valid_checkpoint_cannot_forge_operator_authority(tmp_path, fault):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    try:
        before = await store.snapshot(binding)
        command = ApproveSpotCashflow('approve1', ApprovedSpotCashflow('105', 'USDT', D('2'), int(NOW.timestamp() * 1000)))
        approval = KEYS.sign('offline-operator', binding, 'APPROVE_CASHFLOW', command.json(), before.cut, facts(binding),
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        await store.approve_cashflow(command, approval=approval, facts=facts(binding), cut=before.cut)

        def tamper():
            with store._state.locked() as state:
                if fault == 'ownership':
                    state['ownership']['token']['generation'] = str(UUID(int=99))
                elif fault == 'approval':
                    state['resources']['additional_cashflow_approvals']['105']['signed_amount'] = '999'
                else:
                    state['resources']['control_seed']['body']['ownership']['token']['generation'] = str(UUID(int=99))
                store._state.commit(state)
        await store._call(tamper)
        with pytest.raises(StorageUnavailable):
            await store.snapshot(binding)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_drain_revokes_admission_before_storage_queue_wait(tmp_path):
    import asyncio
    import threading

    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    try:
        before = await store.snapshot(binding)
        command = OwnerTransition('drain1', OwnerMode.DRAINING, owner)
        approval = KEYS.sign('offline-operator', binding, 'DRAIN', command.json(), before.cut, facts(binding),
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))

        def block_worker():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        blocker = asyncio.create_task(store._call(block_worker))
        await asyncio.wait_for(entered.wait(), 2)
        transition = asyncio.create_task(store.transition_owner(command, approval=approval, facts=facts(binding), cut=before.cut))
        await asyncio.sleep(0)
        assert store._cold_unarmed
        release.set()
        await blocker
        await transition
        assert (await store.current_owner(binding)).mode is OwnerMode.DRAINING
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_signed_known_native_cashflow_import_preserves_balances_budgets_and_cold_unarmed(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger

    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    await store.close()
    native = ReservationLedger.restore(files.reservations, limits=RISK)
    native.record_cashflow('101', 'USDT', D('2'))
    cold = await open_it(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        observed = replace(facts(binding), account_quote_usdt=D('12'), account_cashflow_ids=('101',))
        command = ImportApprovedCashflows('import1', owner, (transfer(),))
        approval = KEYS.sign('offline-operator', binding, 'IMPORT', command.json(), before.cut, observed,
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        await cold.import_legacy(command, approval=approval, facts=observed, cut=before.cut)
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert (await cold.lookup_cashflow('cash1', binding)).state == 'APPLIED'
        assert (await cold.import_legacy(command, approval=approval, facts=observed, cut=before.cut)).replayed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_import_restores_only_hash_proven_original_request_then_exact_resume(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_prepare_recovery import pending

    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    original = (await store.snapshot(binding)).cut
    prepared = await pending(store, binding, owner, 'wal')
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        def older_record():
            with cold._state.locked() as state:
                state['resources']['commands']['operation1'].pop('original_cut')
                state['resources']['commands']['operation1'].pop('original_owner')
                cold._state.commit(state)
        await cold._call(older_record)
        before = await cold.snapshot(binding)
        command = ImportPreparationOrigin('import1', 'operation1', original, owner)
        approval = KEYS.sign('offline-operator', binding, 'IMPORT', command.json(), before.cut, facts(binding),
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        await cold.import_legacy(command, approval=approval, facts=facts(binding), cut=before.cut)
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        await cold.resume_prepare(prepared, facts=facts(binding), cut=after.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == D('2.1')
    finally:
        await cold.close()
