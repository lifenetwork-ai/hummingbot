"""Cold financial resume completes existing facts; no ownership grant or resend."""
import asyncio
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
    command as fill_command,
    create_fixture,
    prepare_arm,
)
from unittest.mock import patch

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import (
    SpotFinancialRecoveryFacts,
    SpotFinancialRecoveryPolicy,
)

RECOVERY = SpotFinancialRecoveryPolicy(1000)  # Explicit synthetic freshness.


@pytest_asyncio.fixture
async def bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', create_binding=True, owner=owner,
                                   clock=lambda: time[0], fill_policy=POLICY, accounting_policy=ACCOUNTING,
                                   recovery_policy=RECOVERY)
    try:
        yield store, files, binding, owner, time
    finally:
        await store.close()


def facts(binding, *, kind='fill', **changes):
    return replace(SpotFinancialRecoveryFacts(binding, NOW, D('6') if kind == 'fill' else D('5'),
                                              D('8.98') if kind == 'fill' else D('12'), D('0'), D('0'),
                                              ('trade1',) if kind == 'fill' else (), () if kind == 'fill' else ('101',),
                                              ('wire1',) if kind == 'fill' else (), ('wire1',) if kind == 'fill' else (),
                                              True, True, True), **changes)


async def cold_open(files, binding, tmp_path, now=NOW, policy=RECOVERY):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: now,
                                  fill_policy=POLICY, accounting_policy=ACCOUNTING, recovery_policy=policy)


async def pending_fill(store, binding, owner, *, acknowledge=True):
    await prepare_arm(store, binding, owner, acknowledge=acknowledge)
    with patch.object(LossBudgetLedger, 'record', side_effect=OSError('synthetic interrupted budget')):
        with pytest.raises(CommitUnknown):
            await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)


async def pending_cashflow(store, binding):
    original = type(store)._publish_cashflow

    def fail(self, key, *args):
        if key == 'cash-done:cash1':
            raise OSError('synthetic interrupted receipt')
        return original(self, key, *args)

    owner = (await store.current_owner(binding)).token
    with patch.object(type(store), '_publish_cashflow', new=fail):
        with pytest.raises(CommitUnknown):
            await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_resume_fill_finishes_loss_and_subsidy_once_and_cold_remains_unarmed(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.total_quote_usdt == D('8.98') and before.budget.session_execution_loss_usdt == 0
        receipt = await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert receipt.state == 'APPLIED' and not receipt.replayed
        assert after.balance == before.balance and after.budget.session_execution_loss_usdt == D('.12')
        assert after.budget.session_subsidy_committed_usdt == D('.12') and after.recovery_required
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
        assert (await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=after.cut, owner=owner)).replayed
        assert (await cold.snapshot(binding)).cut == after.cut
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=after.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_resume_cashflow_deduplicates_native_transfer_and_invalidates_only_accounting_cut(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_cashflow(store, binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        receipt = await cold.resume_cashflow(transfer(), facts=facts(binding, kind='cash'), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert receipt.state == 'APPLIED' and after.balance == before.balance
        assert after.balance.total_quote_usdt == D('12') and after.budget == before.budget and after.recovery_required
        assert (await cold.lookup_cashflow('cash1', binding)).state == 'APPLIED'
        assert (await cold.resume_cashflow(transfer(), facts=facts(binding, kind='cash'), cut=after.cut, owner=owner)).replayed
        assert (await cold.snapshot(binding)).cut == after.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'order_scope_complete': False}, {'bill_scope_complete': False}, {'runner_scope_complete': False},
    {'account_base_life': D('5')}, {'account_quote_usdt': D('9')}, {'account_trade_ids': ()},
    {'account_cashflow_ids': ('999',)}, {'accepted_wire_ids': ()}, {'runner_wire_ids': ('foreign',)},
    {'liability_quote_usdt': D('.1')}, {'liability_base_life': D('.1')},
    {'observed_at': NOW - timedelta(milliseconds=1001)}, {'observed_at': NOW + timedelta(milliseconds=1)},
])
async def test_unqualified_recovery_facts_cannot_change_accounting(bundle, tmp_path, changes):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_fill(fill_command(owner), facts=facts(binding, **changes), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
        assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_changed_original_operation_binding_owner_or_cut_cannot_resume(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    stale = (await store.snapshot(binding)).cut
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        changed = replace(fill_command(owner), observed_at=NOW - timedelta(milliseconds=1))
        for cmd, evidence, cut, token in (
            (changed, facts(binding), before.cut, owner),
            (fill_command(owner, operation_id='new'), facts(binding), before.cut, owner),
            (fill_command(owner), facts(binding), before.cut, replace(owner, epoch=2)),
            (fill_command(owner), facts(replace(binding, account_uid='999')), before.cut, owner),
            (fill_command(owner), facts(binding), stale, owner),
        ):
            with pytest.raises(StorageError):
                await cold.resume_fill(cmd, facts=evidence, cut=cut, owner=token)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_late_resume_uses_fresh_account_facts_without_replacing_fill_time_value(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner, acknowledge=False)
    await store.close()
    later = owner.expires_at + timedelta(seconds=1)
    cold = await cold_open(files, binding, tmp_path, now=later)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        await cold.resume_fill(fill_command(owner), facts=facts(binding, observed_at=later), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == D('.12')
        assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
        assert (await cold.snapshot(binding)).recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_resume_before_wallet_checkpoint_applies_missing_fill_and_fee_once(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await prepare_arm(store, binding, owner)
    with patch.object(ReservationLedger, 'apply_fills_snapshot', side_effect=OSError('synthetic before native apply')):
        with pytest.raises(CommitUnknown):
            await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.total_quote_usdt == D('10')
        await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.balance.total_quote_usdt == D('8.98') and after.balance.fee_reserved_quote_usdt == D('.08')
        assert after.budget.session_execution_loss_usdt == D('.12')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_resume_before_native_cashflow_checkpoint_applies_approved_money_once(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    with patch.object(ReservationLedger, 'record_cashflow', side_effect=OSError('synthetic before native cashflow')):
        with pytest.raises(CommitUnknown):
            await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.total_quote_usdt == D('10')
        await cold.resume_cashflow(transfer(), facts=facts(binding, kind='cash'), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('12')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_failed_resume_can_retry_only_after_cold_readback(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with patch.object(SubsidyBudgetLedger, 'record_fill_floor', side_effect=OSError('synthetic interrupted resume')):
            with pytest.raises(CommitUnknown):
                await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
        with pytest.raises(StorageUnavailable):
            await cold.snapshot(binding)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.budget.session_execution_loss_usdt == D('.12')
        await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == D('.12')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_recovery_policy_cannot_change_or_be_implicitly_added_on_restore(bundle, tmp_path):
    store, files, binding, _, _ = bundle
    await store.close()
    for policy in (None, SpotFinancialRecoveryPolicy(999)):
        with pytest.raises(StorageUnavailable):
            await cold_open(files, binding, tmp_path, policy=policy)


@pytest.mark.asyncio
async def test_foreign_native_cashflow_cannot_be_adopted_during_fill_resume(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    native = ReservationLedger.restore(files.reservations, limits=RISK)
    native.record_cashflow('999', 'USDT', D('1'))  # Explicit synthetic external mutation.
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
        assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_unrelated_partial_phase_cannot_be_completed_by_financial_resume(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        def fixture_extra_phase():
            with cold._state.locked() as state:
                record = dict(state['resources']['commands']['operation1'])
                record['state'] = 'ACKING'
                state['resources']['commands']['operation2'] = record
                cold._state.commit(state)
        await cold._call(fixture_extra_phase)
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cashflow_resume_rejects_changed_bill_terms_or_unqualified_wallet_before_writes(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_cashflow(store, binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        for cmd, evidence in ((transfer(signed_amount=D('3')), facts(binding, kind='cash')),
                              (transfer(operation_id='cash2'), facts(binding, kind='cash')),
                              (transfer(), facts(binding, kind='cash', account_cashflow_ids=())),
                              (transfer(), facts(binding, kind='cash', account_quote_usdt=D('13')))):
            with pytest.raises(StorageError):
                await cold.resume_cashflow(cmd, facts=evidence, cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_fill_resume_noop_never_publishes_applied(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await prepare_arm(store, binding, owner)
    with patch.object(ReservationLedger, 'apply_fills_snapshot', side_effect=OSError('synthetic initial missing checkpoint')):
        with pytest.raises(CommitUnknown):
            await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with patch.object(ReservationLedger, 'apply_fills_snapshot', return_value=False):
            with pytest.raises(CommitUnknown):
                await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        assert (await cold.lookup_fill('fill1', binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('10')
        await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
        assert (await cold.lookup_fill('fill1', binding)).state == 'APPLIED'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cancelled_resume_retains_account_lock_and_deduplicated_money(bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ReservationLedger.apply_fills_snapshot

    def paused(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    with patch.object(ReservationLedger, 'apply_fills_snapshot', new=paused):
        task = asyncio.create_task(cold.resume_fill(fill_command(owner), facts=facts(binding),
                                                    cut=(await cold.snapshot(binding)).cut, owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(AccountLockUnavailable):
                await cold_open(files, binding, tmp_path)
        finally:
            release.set()
            await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.total_quote_usdt == D('8.98') and before.recovery_required
        await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('8.98')
        assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == D('.12')
    finally:
        await cold.close()


@pytest.mark.parametrize('changes', [
    {'account_quote_usdt': float('nan')}, {'liability_quote_usdt': D('NaN')},
    {'account_trade_ids': ['trade1']}, {'runner_scope_complete': 1},
    {'accepted_wire_ids': ('wire1', 'wire1')}, {'observed_at': NOW.replace(tzinfo=None)},
])
def test_invalid_recovery_fact_shapes(changes):
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', UUID(int=1), 1, 'a' * 64, 'b' * 64)
    with pytest.raises(StorageError):
        facts(binding, **changes)


@pytest.mark.parametrize('kind,stage', [('fill', 'native'), ('fill', 'receipt'), ('cash', 'native'), ('cash', 'receipt')])
def test_killed_financial_resume_retains_pending_or_completed_operation(tmp_path, kind, stage):
    import subprocess
    import sys
    from pathlib import Path

    files, binding, owner = create_fixture(tmp_path)
    child = r'''
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    BUDGET,CAPACITY,NOW,POLICY,RISK,ACCOUNTING,RECOVERY,pending_fill,pending_cashflow,fill_command,transfer,facts)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles,open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
async def main():
    path=Path(sys.argv[1])
    files=LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    async def open_it(create=False):
        return await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
            subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,fill_policy=POLICY,
            accounting_policy=ACCOUNTING,recovery_policy=RECOVERY,
            account_lock_root=path/'account-locks',create_binding=create,owner=owner if create else None,clock=lambda:NOW)
    store=await open_it(True)
    if sys.argv[2]=='fill':
        await pending_fill(store,binding,owner)
    else:
        await pending_cashflow(store,binding)
    await store.close()
    cold=await open_it()
    if sys.argv[3]=='native':
        method='apply_fills_snapshot' if sys.argv[2]=='fill' else 'record_cashflow'
        original=getattr(ReservationLedger,method)
        def crash(self,*args,**kwargs):
            original(self,*args,**kwargs)
            os._exit(81)
        setattr(ReservationLedger,method,crash)
    else:
        original=FileCheckpointStore._commit
        def crash(self,*args,**kwargs):
            result=original(self,*args,**kwargs)
            if args[2].startswith(('fill-done:', 'cash-done:')):
                os._exit(81)
            return result
        FileCheckpointStore._commit=crash
    if sys.argv[2]=='fill':
        await cold.resume_fill(fill_command(owner),facts=facts(binding),cut=(await cold.snapshot(binding)).cut,owner=owner)
    else:
        await cold.resume_cashflow(transfer(),facts=facts(binding,kind='cash'),cut=(await cold.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), kind, stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 81, result.stderr

    async def readback():
        cold = await cold_open(files, binding, tmp_path)
        try:
            before = await cold.snapshot(binding)
            if kind == 'fill':
                receipt = await cold.lookup_fill('fill1', binding)
                expected_quote = D('8.98')
                result = await cold.resume_fill(fill_command(owner), facts=facts(binding), cut=before.cut, owner=owner)
                assert (await cold.snapshot(binding)).budget.session_execution_loss_usdt == D('.12')
            else:
                receipt = await cold.lookup_cashflow('cash1', binding)
                expected_quote = D('12')
                result = await cold.resume_cashflow(transfer(), facts=facts(binding, kind='cash'), cut=before.cut, owner=owner)
            assert receipt.state == ('APPLIED' if stage == 'receipt' else 'RECOVERY_REQUIRED')
            assert result.replayed is (stage == 'receipt')
            assert (await cold.snapshot(binding)).balance.total_quote_usdt == expected_quote
            assert (await cold.snapshot(binding)).recovery_required
        finally:
            await cold.close()
    asyncio.run(readback())
