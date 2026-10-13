"""Actual joint settlement uses the file owner worker; no exchange credentials."""
import asyncio
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY, create_fixture
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import (
    cold_restore,
    report,
    send_close,
    setup_recovery,
)
from types import SimpleNamespace
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits, open_file_joint


async def fixture(tmp_path, *, carry=False, owner_duration_ms=None):
    recovery, values, bundle, canceled = setup_recovery(tmp_path / 'native')
    monitor, feed = None, None
    if carry:
        from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import setup_carry
        from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import install_attribution
        install_attribution(tmp_path / 'native', values)
        monitor, _, feed = setup_carry(tmp_path / 'native', values=values)
    _, binding, owner = create_fixture(tmp_path / 'seed')
    session = values[0]._order_safety_manager.current_session
    binding = replace(binding, config_hash=fingerprint(values[0].config.strategy.model_dump(mode='json')))
    owner = replace(owner, session_id=UUID(session.session_id), epoch=session.epoch,
                    valid_from=values[6].wall, expires_at=session.expires_at)
    if owner_duration_ms is not None:
        owner = replace(owner, expires_at=owner.valid_from + timedelta(milliseconds=owner_duration_ms))
    store = await open_store(Backend.FILE, path=tmp_path / 'joint-owner.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: values[6].wall)
    repository = await open_file_joint(store, recovery=recovery, owner=owner,
                                       limits=JointLimits(16, 64, 64 * 1024))
    repository._test_carry = (monitor, feed)
    return repository, store, recovery, values, bundle, canceled, owner


@pytest.mark.asyncio
async def test_actual_partial_and_terminal_settlement_keep_native_economics_on_worker(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    loop_thread = threading.get_ident()
    original = PolicyState.locked

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        return original(journal, *args, **kwargs)

    try:
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        proof = report(values, bundle, claim, terminal=False)
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            assert await recovery.reconcile()  # Actual installed entry point delegates.
            view = await repo.state(store.binding)
        assert view.claim_ids == (claim.intent_id,)
        assert view.measurement.adjusted_nav_quote == Decimal('19.999')
        assert values[0]._order_safety_reservations.usdt_balance == Decimal('9.999')
        position = await repo.swap_state(store.binding)
        assert position.capital.long_base == Decimal('.75')
        assert position.account.net_contracts == Decimal('3')
        assert position.capital.collateral_quote == Decimal('100')
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, pending=())
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3,
                                  orders=(replace(proof, state='canceled'),), pending_wire_ids=())
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            assert await repo.reconcile(store.binding, owner)
            assert await repo.reconcile(store.binding, owner)
        assert values[1].claim_ids() == ()
        assert values[5].get(claim.intent_id).state == 'TERMINAL'
        assert values[0]._order_safety_reservations.usdt_balance == Decimal('9.999')
        assert not hasattr(view, 'send_authorized')
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_funding_signed_cashflows_preserve_wallet_nav_and_budget_without_double_debit(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh

    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path, carry=True)
    monitor, feed = repo._test_carry
    try:
        assert await repo.reconcile(store.binding, owner)
        now = recovery.clock_ms()
        payments = (FundingPayment('101', now, Decimal('-.1')), FundingPayment('102', now, Decimal('.04')))
        refresh(values, feed, payments=payments, spot_cash_quote=Decimal('9.94'), collateral_quote=Decimal('99.94'))
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, payments=tuple(
            RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms) for p in payments))
        assert await repo.reconcile(store.binding, owner)
        assert await repo.reconcile(store.binding, owner)
        assert values[0]._order_safety_reservations.usdt_balance == Decimal('9.94')
        assert values[8]['snapshot'].collateral_quote == Decimal('99.94')
        assert (await repo.state(store.binding)).measurement.adjusted_nav_quote == Decimal('19.94')
        decision = await store._call(monitor.check)
        assert decision.funding_debits_quote == Decimal('.1')
        assert decision.settled_payment_quote == Decimal('-.06')
        position = await repo.swap_state(store.binding)
        assert sum((p.signed_amount_quote for p in position.settled_payments), Decimal('0')) == Decimal('-.06')
        assert position.capital.funding_liability_quote >= 0
        assert all(p.at_ms == now for p in position.settled_payments)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expired_owner_can_observe_but_never_keeps_native_send_eligibility(tmp_path):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path, owner_duration_ms=500)
    try:
        assert await repo.reconcile(store.binding, owner)
        values[6].advance(1)
        now = recovery.clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, observed_at_ms=now)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2, observed_at_ms=now)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, observed_at_ms=now)
        assert await repo.reconcile(store.binding, owner)
        assert not recovery._armed and not values[2]._runtime_enabled
        assert not await store._call(recovery.allocation_ready)
        assert (await repo.state(store.binding)).measurement.adjusted_nav_quote == Decimal('20')
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancel_checkpoints_run_on_worker_and_ack_retains_claim(tmp_path, monkeypatch):
    repo, store, recovery, values, _, canceled, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        original = recovery.sender.wal.mark_cancel_attempt

        def persist(*args, **kwargs):
            store.assert_worker_thread()
            return original(*args, **kwargs)

        monkeypatch.setattr(recovery.sender.wal, 'mark_cancel_attempt', persist)
        await repo.cancel_working_swaps(store.binding, owner)
        await repo.cancel_working_swaps(store.binding, owner)
        assert canceled == [claim.wire_id]
        assert values[5].get(claim.intent_id).cancel_attempts == 1
        assert values[1].claim_ids() == (claim.intent_id,)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_foreign_owner_and_namespace_deny_before_collection_or_money(tmp_path):
    repo, store, recovery, _, _, _, owner = await fixture(tmp_path)
    calls = []
    original = recovery.collect

    async def collect():
        calls.append(True)
        return await original()

    recovery.collect = collect
    try:
        with pytest.raises(StorageError, match='JOINT_OWNER_MISMATCH'):
            await repo.reconcile(store.binding, replace(owner, epoch=owner.epoch + 1))
        with pytest.raises(StorageError, match='STORAGE_BINDING_MISMATCH'):
            await repo.reconcile(replace(store.binding, account_uid='99'), owner)
        assert calls == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_configuration_change_and_unqualified_position_read_fail_closed(tmp_path):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        with pytest.raises(ValueError, match='JOINT_FRESH_PROOF_REQUIRED'):
            # A new sequence needs a new full joint proof, not only SDK state.
            values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2)
            await repo.swap_state(store.binding)
        assert values[0]._order_safety_reservations.usdt_balance == Decimal('10')
        values[0].config = values[0].config.model_copy(update={'recovery_reconciliation_max_age_ms': 999})
        assert not await repo.reconcile(store.binding, owner)
        assert not recovery._healthy and not recovery._armed
        assert not store._uncertain
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_mutable_collector_facts_are_refused_before_financial_checkpoint(tmp_path):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    try:
        bundle['value'] = replace(bundle['value'], orders=[])
        assert not await repo.reconcile(store.binding, owner)
        assert recovery.reason_code == 'JOINT_HISTORY_SNAPSHOT_INVALID_OR_CAPACITY'
        assert values[0]._order_safety_reservations.joint_cash_events == {}
        assert values[1].claim_ids() == ()
        assert not store._uncertain
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_native_authority_helpers_refuse_loop_reads_before_archive_or_journal_io(tmp_path, monkeypatch):
    repo, store, recovery, values, _, _, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)

        def unexpected(*args, **kwargs):
            pytest.fail('native authority attempted file IO on the event loop')

        with monkeypatch.context() as patches:
            patches.setattr(recovery.journal, 'archive', SimpleNamespace(assert_capacity=unexpected))
            patches.setattr(values[1], 'claims', unexpected)
            assert not recovery.allocation_ready()
            assert not recovery.runner_provenance_complete(())
            with pytest.raises(StorageError, match='STORAGE_WORKER_REQUIRED'):
                with recovery._capital_allocation(values[1]):
                    pytest.fail('worker ownership was bypassed')
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_publish_late_health_or_free_worker(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    assert await repo.reconcile(store.binding, owner)
    claim = await store._call(send_close, values)
    report(values, bundle, claim)
    entered, release = threading.Event(), threading.Event()
    original = recovery._apply

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(recovery, '_apply', blocked)
    task = asyncio.create_task(repo.reconcile(store.binding, owner))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._uncertain and len(store._pending) == 1
        assert recovery._busy  # Physical settlement still owns release qualification.
        with pytest.raises(StorageError):
            await repo.state(store.binding)
    finally:
        release.set()
        await store.close()
    assert not recovery._busy and not recovery._healthy and not recovery._armed
    assert values[0]._order_safety_reservations.usdt_balance == Decimal('9.999')


@pytest.mark.asyncio
@pytest.mark.parametrize('cut', ['before_apply', 'cash', 'wal', 'capital', 'completion', 'clean'])
async def test_cold_readback_keeps_cross_journal_money_and_never_rearms(tmp_path, monkeypatch, cut):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    assert await repo.reconcile(store.binding, owner)
    claim = await store._call(send_close, values)
    report(values, bundle, claim)
    if cut != 'clean':
        target, name = {
            'before_apply': (recovery, '_apply'),
            'cash': (values[0]._order_safety_reservations, 'apply_joint_snapshot'),
            'wal': (values[5], 'mark_terminal'),
            'capital': (values[1], 'settle'),
            'completion': (recovery.journal, 'commit'),
        }[cut]
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if cut == 'completion' and args[0]['phase'] != 'COMMITTED':
                return original(*args, **kwargs)
            if cut in ('cash', 'wal', 'capital'):
                original(*args, **kwargs)
            raise OSError('synthetic interrupted native checkpoint')

        with monkeypatch.context() as patches:
            patches.setattr(target, name, interrupted)
            assert not await repo.reconcile(store.binding, owner)
    else:
        assert await repo.reconcile(store.binding, owner)
    await store.close()
    native, fresh = cold_restore(recovery, values)
    restored = await open_store(Backend.FILE, path=tmp_path / 'joint-owner.json', binding=store.binding,
                                limits=CAPACITY, create=False, clock=lambda: fresh[6].wall)
    try:
        cold = await open_file_joint(restored, recovery=native, owner=owner, limits=repo.limits)
        assert await cold.reconcile(store.binding, owner)
        assert await cold.reconcile(store.binding, owner)
        assert fresh[1].claim_ids() == () and fresh[5].get(claim.intent_id).state == 'TERMINAL'
        assert fresh[0]._order_safety_reservations.usdt_balance == Decimal('9.999')
        assert not native._armed and not fresh[2]._runtime_enabled
        assert len(fresh[4].sent) == 1
        assert (await cold.state(store.binding)).measurement.adjusted_nav_quote == Decimal('19.999')
        with pytest.raises(ValueError, match='SIGNED_REARM_REQUIRED'):
            native.manual_rearm(operator_id='no-signature')
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'scope', 'runner', 'history', 'pending', 'algo',
                                   'cash', 'position', 'fee', 'missing', 'exchange', 'duplicate'])
async def test_unqualified_facts_never_release_actual_capital(tmp_path, fault):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        proof = report(values, bundle, claim)
        changes = {
            'uid': {'account_uid': '999'}, 'scope': {'scope_complete': False},
            'runner': {'runner_scope_complete': False},
            'history': {'coverage_from_ms': bundle['value'].observed_at_ms + 1},
            'pending': {'pending_wire_ids': ('foreign',)}, 'algo': {'algo_wire_ids': ('algo',)},
            'missing': {'orders': ()},
        }
        if fault in changes:
            bundle['value'] = replace(bundle['value'], **changes[fault])
        elif fault in ('cash', 'position'):
            values[8]['snapshot'] = replace(values[8]['snapshot'], **(
                {'spot_cash_quote': Decimal('10')} if fault == 'cash' else {'long_base': Decimal('1')}))
        elif fault == 'fee':
            bundle['value'] = replace(bundle['value'], orders=(replace(
                proof, trades=(replace(proof.trades[0], fee_currency='BTC'),)),))
        elif fault == 'exchange':
            values[5].acknowledge(claim.intent_id, 'other-exchange-id')
        else:
            bundle['value'] = replace(bundle['value'], orders=(proof, proof))
        assert not await repo.reconcile(store.binding, owner)
        assert values[1].claim_ids() == (claim.intent_id,)
        assert values[0]._order_safety_reservations.usdt_balance == Decimal('10')
        assert not recovery._healthy and not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_refresh_has_bounded_admission_and_stop_discards_late_collection(tmp_path):
    repo, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def collect():
        entered.set()
        await release.wait()
        return bundle['value']

    recovery.collect = collect
    first = asyncio.create_task(repo.reconcile(store.binding, owner))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(repo.reconcile(store.binding, owner))
        await asyncio.sleep(0)
        assert len(repo._admitted) == 2
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await repo.reconcile(store.binding, owner)
        repo.revoke()
        release.set()
        assert await first is False and await second is False
        assert not repo._admitted and not recovery._healthy and not recovery._armed
        assert values[0]._order_safety_reservations.joint_cash_events == {}
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
        await store.close()
