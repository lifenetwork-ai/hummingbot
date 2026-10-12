"""Complete synthetic fill snapshots exercise actual capital/fee/budget journals."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    create_fixture,
    order,
)
from unittest.mock import patch

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AttemptState,
    FillSpot,
    SpotFillObservation,
    SpotFillPolicy,
)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot

AT = int(NOW.timestamp() * 1000)
POLICY = SpotFillPolicy(max_reference_skew_ms=1000)  # Synthetic calibration only.


@pytest_asyncio.fixture
async def filled_bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', create_binding=True,
                                   owner=owner, clock=lambda: time[0], fill_policy=POLICY)
    try:
        yield store, files, binding, owner, time
    finally:
        await store.close()


def fill(**changes):
    return replace(SpotFillObservation('trade1', D('1'), D('1'), 'USDT', D('-.02'), AT,
                                       D('.9'), AT, AT, 'independent_market'), **changes)


def command(owner, **changes):
    return replace(FillSpot('fill1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                            str(owner.session_id), owner.epoch, '123456789', D('1'), (fill(),), NOW), **changes)


async def prepare_arm(store, binding, owner, *, acknowledge=True, **changes):
    await store.prepare(order(owner, fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.1'), **changes),
                        cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    if acknowledge:
        await store.acknowledge(ack(owner, side=changes.get('side', 'BUY')),
                                cut=(await store.snapshot(binding)).cut, owner=owner)


async def cold_open(files, binding, tmp_path):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW, fill_policy=POLICY)


@pytest.mark.asyncio
async def test_fill_and_fee_balance_hold_loss_and_subsidy_match_existing_accounting(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    receipt = await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_base_life == D('6') and view.balance.total_quote_usdt == D('8.98')
    assert view.balance.fee_reserved_quote_usdt == D('.08')
    assert view.balance.reserved_quote_usdt == D('1.08')
    assert (await store.reservation('intent1', binding)).remaining_base_life == D('1')
    assert view.budget.session_execution_loss_usdt == D('.12')
    assert view.budget.session_subsidy_committed_usdt == D('.12')
    capital = CapitalLedger(opening_life=D('5'), opening_usdt=D('10'), opening_independent_price_usdt=D('.9'))
    capital.record_fill('trade1', 'BUY', D('1'), D('1'), D('.02'), independent_value_usdt=D('.9'),
                        fee_currency='USDT', signed_fee=D('-.02'))
    assert view.balance.total_quote_usdt == capital.usdt_balance
    assert receipt.execution_loss_usdt == capital.execution_loss_quote
    assert not hasattr(receipt, 'send_authorized')


@pytest.mark.asyncio
async def test_repeated_fill_snapshot_and_new_operation_do_not_double_apply(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    initial = (await store.snapshot(binding)).cut
    await store.apply_fills(command(owner), cut=initial, owner=owner)
    completed = await store.snapshot(binding)
    for cut in (initial, completed.cut):
        assert (await store.apply_fills(command(owner), cut=cut, owner=owner)).replayed
        assert (await store.snapshot(binding)).cut == completed.cut
    await store.apply_fills(command(owner, operation_id='fill2'), cut=completed.cut, owner=owner)
    assert (await store.snapshot(binding)).balance == completed.balance
    assert (await store.snapshot(binding)).budget == completed.budget


@pytest.mark.asyncio
async def test_progressive_snapshot_preserves_each_fee_loss_and_zero_remaining_does_not_release(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    second = fill(trade_id='trade2', independent_price_usdt=D('1.1'), signed_fee=D('.01'))
    await store.apply_fills(command(owner, operation_id='fill2', cumulative_base_life=D('2'), fills=(fill(), second)),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_base_life == D('7') and view.balance.total_quote_usdt == D('7.99')
    assert view.balance.fee_reserved_quote_usdt == D('.08')  # Rebate does not refund consumed buffer.
    assert view.budget.session_execution_loss_usdt == D('.12')
    assert view.budget.session_subsidy_committed_usdt == D('.12')
    assert (await store.reservation('intent1', binding)).state != 'TERMINAL'
    assert (await store.attempt('intent1', binding)).state is AttemptState.ACKED


@pytest.mark.asyncio
async def test_life_fee_is_paid_in_life_and_valued_independently(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner, fills=(fill(fee_currency='LIFE', signed_fee=D('-.02')),)),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_base_life == D('5.98') and view.balance.total_quote_usdt == D('9')
    assert view.balance.fee_reserved_base_life == D('.08')
    assert view.budget.session_execution_loss_usdt == D('.118')


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'client_order_id': 'foreign'}, {'exchange_order_id': '999'}, {'epoch': 2}, {'side': 'SELL'},
    {'observed_at': NOW + timedelta(seconds=1)}, {'prepare_operation_id': 'foreign'},
])
async def test_foreign_or_future_fill_command_has_no_side_effect(filled_bundle, changes):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    view = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.apply_fills(command(owner, **changes), cut=view.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == view.cut


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'independent_value_at_ms': AT - 1001}, {'independent_observed_at_ms': AT + 1001},
    {'price_usdt': D('1.01')}, {'quantity_base_life': D('3')},
])
async def test_unqualified_value_or_fill_exceeding_intent_rejected_before_writes(filled_bundle, changes):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    row = fill(**changes)
    view = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.apply_fills(command(owner, cumulative_base_life=row.quantity_base_life, fills=(row,)),
                                cut=view.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == view.cut


@pytest.mark.asyncio
async def test_conflicting_fee_or_value_and_incomplete_snapshot_cannot_change_existing_fill(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    for row in (fill(signed_fee=D('-.03')), fill(independent_price_usdt=D('.8')),
                fill(trade_id='trade2')):
        with pytest.raises(StorageError):
            await store.apply_fills(command(owner, operation_id='fill2', fills=(row,)), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_fill_before_ack_can_be_accounted_and_late_ack_does_not_require_unfilled_hold(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner, acknowledge=False)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.snapshot(binding)).recovery_required
    await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert not (await store.snapshot(binding)).recovery_required


@pytest.mark.asyncio
async def test_fee_overrun_is_recorded_and_closes_new_risk_without_refund(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner, fills=(fill(signed_fee=D('-.2')),)),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_quote_usdt == D('8.8') and view.balance.fee_reserved_quote_usdt == 0
    assert view.recovery_required and view.budget.session_execution_loss_usdt == D('.3')
    with pytest.raises(StorageError):
        await store.prepare(order(owner, operation_id='op2', intent_id='intent2', client_order_id='wire2'),
                            cut=view.cut, owner=owner)


@pytest.mark.asyncio
async def test_late_fill_and_exhausted_loss_budget_are_still_accounted(filled_bundle):
    store, _, binding, owner, time = filled_bundle
    await prepare_arm(store, binding, owner, quantity_base=D('4'))
    time[0] = owner.expires_at + timedelta(seconds=1)
    row = fill(quantity_base_life=D('4'), independent_price_usdt=D('.1'))
    await store.apply_fills(command(owner, cumulative_base_life=D('4'), fills=(row,), observed_at=time[0]),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.budget.loss_exhausted and view.budget.session_execution_loss_usdt == D('3.62')
    assert view.budget.session_subsidy_committed_usdt == D('3.62')
    assert (await store.attempt('intent1', binding)).state is AttemptState.ACKED


@pytest.mark.asyncio
async def test_interrupted_loss_application_keeps_fill_fees_and_recovery_barrier(filled_bundle, tmp_path):
    store, files, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(LossBudgetLedger, 'record', side_effect=OSError('synthetic loss failure')):
        with pytest.raises(CommitUnknown):
            await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageUnavailable):
        await store.snapshot(binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.total_quote_usdt == D('8.98')
        assert view.balance.reserved_quote_usdt == D('1.08')
        assert (await cold.lookup_fill('fill1', binding)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError):
            await cold.apply_fills(command(owner), cut=view.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_completed_fill_balances_budgets_and_receipt_survive_cold_restart(filled_bundle, tmp_path):
    store, files, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert (await cold.lookup_fill('fill1', binding)).state == 'APPLIED'
    finally:
        await cold.close()


@pytest.mark.parametrize('changes', [
    {'signed_fee': float('nan')}, {'signed_fee': D('NaN')}, {'fee_currency': 'BTC'},
    {'quantity_base_life': D('0')}, {'fill_at_ms': True}, {'source_kind': 'self_quote'},
    {'independent_price_usdt': D('0')},
])
def test_invalid_fill_model(changes):
    with pytest.raises(StorageError):
        fill(**changes)


def test_duplicate_trade_and_cumulative_mismatch_rejected():
    from types import SimpleNamespace
    owner = SimpleNamespace(session_id='session1', epoch=1)
    with pytest.raises(StorageError):
        command(owner, fills=(fill(), fill()), cumulative_base_life=D('2'))
    with pytest.raises(StorageError):
        command(owner, cumulative_base_life=D('2'))


@pytest.mark.asyncio
async def test_sell_fill_life_fee_keeps_remaining_inventory_hold(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner, side='SELL')
    row = fill(fee_currency='LIFE', signed_fee=D('-.02'), independent_price_usdt=D('1.1'))
    await store.apply_fills(command(owner, side='SELL', fills=(row,)), cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_base_life == D('3.98') and view.balance.total_quote_usdt == D('11')
    assert view.balance.reserved_base_life == D('1.08')
    assert view.budget.session_execution_loss_usdt == D('.122')


@pytest.mark.asyncio
async def test_early_fill_exchange_identity_is_binding_for_later_ack(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner, acknowledge=False)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.acknowledge(ack(owner, exchange_order_id='999'), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut
    assert (await store.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN


@pytest.mark.asyncio
async def test_stale_cut_foreign_owner_and_binding_rejected(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    stale = (await store.snapshot(binding)).cut
    await prepare_arm(store, binding, owner)
    before = await store.snapshot(binding)
    for cut, token in ((stale, owner), (before.cut, replace(owner, epoch=2)),
                       (replace(before.cut, checkpoint=replace(before.cut.checkpoint,
                                                               binding=replace(binding, account_uid='999'))), owner)):
        with pytest.raises(StorageError):
            await store.apply_fills(command(owner), cut=cut, owner=token)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_reusing_operation_for_changed_observation_or_omitting_original_fill_conflicts(filled_bundle):
    store, _, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.apply_fills(command(owner, fills=(fill(signed_fee=D('-.03')),)), cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.apply_fills(command(owner, operation_id='fill2', cumulative_base_life=D('2'),
                                        fills=(fill(trade_id='trade2', quantity_base_life=D('2')),)),
                                cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_missing_native_fill_postcondition_cannot_publish_success(filled_bundle, tmp_path):
    store, files, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(ReservationLedger, 'apply_fills_snapshot', return_value=False):
        with pytest.raises(CommitUnknown):
            await store.apply_fills(command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        assert (await cold.lookup_fill('fill1', binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('10')
        assert (await cold.reservation('intent1', binding)).filled_base_life == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cancelled_fill_retains_lock_and_completed_native_fees(filled_bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = filled_bundle
    await prepare_arm(store, binding, owner)
    cut = (await store.snapshot(binding)).cut
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ReservationLedger.apply_fills_snapshot

    def paused(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    with patch.object(ReservationLedger, 'apply_fills_snapshot', new=paused):
        task = asyncio.create_task(store.apply_fills(command(owner), cut=cut, owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(AccountLockUnavailable):
                await cold_open(files, binding, tmp_path)
        finally:
            release.set()
            await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.total_quote_usdt == D('8.98')
        assert (await cold.lookup_fill('fill1', binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await cold.close()


@pytest.mark.parametrize('stage', ['wallet', 'budget'])
def test_killed_fill_worker_keeps_wallet_fees_and_pending_receipt(tmp_path, stage):
    import subprocess
    import sys
    from pathlib import Path

    files, binding, _ = create_fixture(tmp_path)
    child = r'''
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    BUDGET,CAPACITY,NOW,RISK,POLICY,prepare_arm,command)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles,open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
async def main():
    path=Path(sys.argv[1])
    files=LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    store=await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
        subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,fill_policy=POLICY,
        account_lock_root=path/'account-locks',create_binding=True,owner=owner,clock=lambda:NOW)
    await prepare_arm(store,binding,owner)
    cls=ReservationLedger if sys.argv[2]=='wallet' else SubsidyBudgetLedger
    method='apply_fills_snapshot' if sys.argv[2]=='wallet' else 'record_fill_floor'
    original=getattr(cls,method)
    def crash(self,*args,**kwargs):
        original(self,*args,**kwargs)
        os._exit(51)
    setattr(cls,method,crash)
    await store.apply_fills(command(owner),cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 51, result.stderr

    async def readback():
        cold = await cold_open(files, binding, tmp_path)
        try:
            view = await cold.snapshot(binding)
            assert view.recovery_required and view.balance.total_quote_usdt == D('8.98')
            assert view.balance.reserved_quote_usdt == D('1.08')
            assert view.budget.session_execution_loss_usdt == (D('.12') if stage == 'budget' else D('0'))
            assert (await cold.lookup_fill('fill1', binding)).state == 'RECOVERY_REQUIRED'
        finally:
            await cold.close()
    asyncio.run(readback())


@pytest.mark.asyncio
async def test_fill_policy_cannot_be_changed_or_omitted_on_restore(filled_bundle, tmp_path):
    store, files, binding, _, _ = filled_bundle
    await store.close()
    for policy in (None, SpotFillPolicy(999)):
        with pytest.raises(StorageUnavailable):
            await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW, fill_policy=policy)


@pytest.mark.asyncio
async def test_fill_policy_required_before_writes(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', owner=owner, create_binding=True,
                                   clock=lambda: NOW)
    try:
        await prepare_arm(store, binding, owner)
        view = await store.snapshot(binding)
        with pytest.raises(StorageError):
            await store.apply_fills(command(owner), cut=view.cut, owner=owner)
        assert (await store.snapshot(binding)).cut == view.cut
    finally:
        await store.close()
