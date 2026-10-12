"""Approved transfers and independently valued capital use actual native journals."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    AT,
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

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.capital import (
    ApprovedSpotCashflow,
    CashflowSpot,
    ObserveSpotNAV,
    SpotAccountingPolicy,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot

ACCOUNTING = SpotAccountingPolicy(D('5'), D('10'), D('1'), NOW - timedelta(seconds=1), '100', (
    ApprovedSpotCashflow('101', 'USDT', D('2'), AT),
    ApprovedSpotCashflow('102', 'USDT', D('-1'), AT + 1),
    ApprovedSpotCashflow('103', 'LIFE', D('1'), AT + 2),
    ApprovedSpotCashflow('104', 'LIFE', D('-1'), AT + 3),
))  # Explicit synthetic approval/calibration fixture.


@pytest_asyncio.fixture
async def capital_bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', owner=owner,
                                   create_binding=True, clock=lambda: time[0], fill_policy=POLICY,
                                   accounting_policy=ACCOUNTING)
    try:
        yield store, files, binding, owner, time
    finally:
        await store.close()


def transfer(**changes):
    return replace(CashflowSpot('cash1', '101', 'USDT', D('2'), AT,
                                D('1'), AT, AT, 'quote_unit', NOW), **changes)


def nav(**changes):
    return replace(ObserveSpotNAV('nav1', D('1'), AT, AT, 'independent_market', NOW), **changes)


async def cold_open(files, binding, tmp_path, accounting=ACCOUNTING):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW,
                                  fill_policy=POLICY, accounting_policy=accounting)


@pytest.mark.asyncio
async def test_deposit_is_not_profit_and_does_not_change_budgets(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    before = await store.snapshot(binding)
    await store.apply_cashflow(transfer(), cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert after.balance.total_quote_usdt == D('12') and after.budget == before.budget
    receipt = await store.observe_nav(nav(), cut=after.cut, owner=owner)
    assert receipt.capital.nav_usdt == D('17') and receipt.capital.adjusted_nav_usdt == D('15')
    assert receipt.capital.net_cashflows_usdt == D('2') and receipt.capital.highwater_usdt == D('15')
    assert receipt.capital.drawdown_bps == 0 and receipt.capital.current
    assert not hasattr(receipt, 'send_authorized')


@pytest.mark.asyncio
async def test_cashflow_replay_new_operation_and_changed_bill_payload(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    original = (await store.snapshot(binding)).cut
    await store.apply_cashflow(transfer(), cut=original, owner=owner)
    after = await store.snapshot(binding)
    for cut in (original, after.cut):
        assert (await store.apply_cashflow(transfer(), cut=cut, owner=owner)).replayed
        assert (await store.snapshot(binding)).cut == after.cut
    await store.apply_cashflow(transfer(operation_id='cash2'), cut=after.cut, owner=owner)
    assert (await store.snapshot(binding)).balance == after.balance
    with pytest.raises(StorageError):
        await store.apply_cashflow(transfer(signed_amount=D('3')), cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'bill_id': '999'}, {'currency': 'LIFE', 'source_kind': 'independent_market'},
    {'signed_amount': D('-2')}, {'transfer_at_ms': AT + 1}, {'observed_at': NOW + timedelta(seconds=1)},
])
async def test_unapproved_or_future_cashflow_never_touches_native_money(capital_bundle, changes):
    store, _, binding, owner, _ = capital_bundle
    view = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.apply_cashflow(transfer(**changes), cut=view.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == view.cut


@pytest.mark.asyncio
async def test_life_transfer_uses_independent_value_and_preserves_starting_inventory_attribution(capital_bundle):
    store, _, binding, owner, time = capital_bundle
    time[0] = NOW + timedelta(milliseconds=3)
    await store.apply_cashflow(transfer(bill_id='103', currency='LIFE', signed_amount=D('1'), transfer_at_ms=AT + 2,
                                        independent_price_usdt=D('2'), value_at_ms=AT + 2, reference_observed_at_ms=AT + 2,
                                        source_kind='independent_market', observed_at=time[0]),
                               cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_cashflow(transfer(operation_id='cash2', bill_id='104', currency='LIFE', signed_amount=D('-1'),
                                        transfer_at_ms=AT + 3, independent_price_usdt=D('2'), value_at_ms=AT + 3,
                                        reference_observed_at_ms=AT + 3, source_kind='independent_market', observed_at=time[0]),
                               cut=(await store.snapshot(binding)).cut, owner=owner)
    receipt = await store.observe_nav(nav(independent_price_usdt=D('2'), value_at_ms=AT + 3,
                                          reference_observed_at_ms=AT + 3, observed_at=time[0]),
                                      cut=(await store.snapshot(binding)).cut, owner=owner)
    assert receipt.capital.nav_usdt == D('20') and receipt.capital.adjusted_nav_usdt == D('20')
    assert receipt.capital.net_cashflows_usdt == 0 and receipt.capital.starting_inventory_pnl_usdt == D('5')


@pytest.mark.asyncio
async def test_fill_invalidates_last_nav_and_next_observation_keeps_loss_and_highwater(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    await store.observe_nav(nav(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await prepare_arm(store, binding, owner)
    await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert not (await store.capital(binding)).current
    receipt = await store.observe_nav(nav(operation_id='nav2', independent_price_usdt=D('.9')),
                                      cut=(await store.snapshot(binding)).cut, owner=owner)
    assert receipt.capital.nav_usdt == D('14.38') and receipt.capital.highwater_usdt == D('15')
    assert receipt.capital.execution_loss_usdt == D('.12')
    assert receipt.capital.starting_inventory_pnl_usdt == D('-.5')


@pytest.mark.asyncio
async def test_withdrawal_adjusts_nav_without_profit_or_highwater_reset(capital_bundle):
    store, _, binding, owner, time = capital_bundle
    await store.observe_nav(nav(independent_price_usdt=D('2')), cut=(await store.snapshot(binding)).cut, owner=owner)
    time[0] = NOW + timedelta(milliseconds=1)
    await store.apply_cashflow(transfer(bill_id='102', signed_amount=D('-1'), transfer_at_ms=AT + 1,
                                        value_at_ms=AT + 1, reference_observed_at_ms=AT + 1, observed_at=time[0]),
                               cut=(await store.snapshot(binding)).cut, owner=owner)
    capital = (await store.observe_nav(nav(operation_id='nav2', value_at_ms=AT + 1,
                                           reference_observed_at_ms=AT + 1, observed_at=time[0]),
                                       cut=(await store.snapshot(binding)).cut, owner=owner)).capital
    assert capital.nav_usdt == D('14') and capital.adjusted_nav_usdt == D('15')
    assert capital.highwater_usdt == D('20') and capital.drawdown_bps == D('2500')


@pytest.mark.asyncio
async def test_stale_price_cut_or_owner_cannot_publish_nav(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.observe_nav(nav(value_at_ms=AT - 1001), cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.observe_nav(nav(), cut=before.cut, owner=replace(owner, epoch=2))
    await store.apply_cashflow(transfer(), cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.observe_nav(nav(), cut=before.cut, owner=owner)
    assert await store.capital(binding) is None


@pytest.mark.asyncio
async def test_nav_replay_and_highwater_survive_cold_restart(capital_bundle, tmp_path):
    store, files, binding, owner, _ = capital_bundle
    original = (await store.snapshot(binding)).cut
    receipt = await store.observe_nav(nav(independent_price_usdt=D('2')), cut=original, owner=owner)
    assert (await store.observe_nav(nav(independent_price_usdt=D('2')), cut=original, owner=owner)).replayed
    with pytest.raises(StorageError):
        await store.observe_nav(nav(independent_price_usdt=D('3')), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        capital = await cold.capital(binding)
        assert capital.highwater_usdt == receipt.capital.highwater_usdt == D('20')
        assert capital.current and (await cold.snapshot(binding)).recovery_required
        with pytest.raises(StorageError):
            await cold.observe_nav(nav(operation_id='nav2'), cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cashflow_interruption_retains_native_transfer_and_pending_receipt(capital_bundle, tmp_path):
    store, files, binding, owner, _ = capital_bundle
    original = type(store)._publish_cashflow

    def fail(self, key, *args):
        if key == 'cash-done:cash1':
            raise OSError('synthetic interrupted final publication')
        return original(self, key, *args)

    with patch.object(type(store), '_publish_cashflow', new=fail):
        with pytest.raises(CommitUnknown):
            await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageUnavailable):
        await store.snapshot(binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.total_quote_usdt == D('12')
        assert (await cold.lookup_cashflow('cash1', binding)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError):
            await cold.apply_cashflow(transfer(), cut=view.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_cashflow_noop_cannot_publish_success(capital_bundle, tmp_path):
    store, files, binding, owner, _ = capital_bundle
    with patch.object(ReservationLedger, 'record_cashflow', return_value=False):
        with pytest.raises(CommitUnknown):
            await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        assert (await cold.lookup_cashflow('cash1', binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('10')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_opening_capital_and_approval_changes_rejected_on_restore(capital_bundle, tmp_path):
    store, files, binding, _, _ = capital_bundle
    await store.close()
    for policy in (replace(ACCOUNTING, opening_quote_usdt=D('11')),
                   replace(ACCOUNTING, approved_cashflows=())):
        with pytest.raises(StorageUnavailable):
            await cold_open(files, binding, tmp_path, policy)


@pytest.mark.parametrize('changes', [
    {'signed_amount': D('0')}, {'signed_amount': float('nan')}, {'bill_id': '00101'},
    {'currency': 'BTC'}, {'independent_price_usdt': D('2')}, {'transfer_at_ms': True},
])
def test_invalid_cashflow_model(changes):
    with pytest.raises(StorageError):
        transfer(**changes)


def test_approval_ids_and_opening_capital_are_explicit_and_immutable():
    with pytest.raises(StorageError):
        replace(ACCOUNTING, opening_quote_usdt=D('0'), opening_base_life=D('0'))
    with pytest.raises(StorageError):
        replace(ACCOUNTING, approved_cashflows=(ACCOUNTING.approved_cashflows[0], ACCOUNTING.approved_cashflows[0]))
    with pytest.raises(StorageError):
        replace(ACCOUNTING, anchor_bill_id='101')


@pytest.mark.asyncio
async def test_old_nav_observation_cannot_replace_latest_record(capital_bundle):
    store, _, binding, owner, time = capital_bundle
    time[0] = NOW + timedelta(milliseconds=10)
    latest = nav(value_at_ms=AT + 10, reference_observed_at_ms=AT + 10, observed_at=time[0])
    await store.observe_nav(latest, cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.observe_nav(nav(operation_id='nav2'), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut
    assert (await store.capital(binding)).value_at_ms == AT + 10


@pytest.mark.asyncio
async def test_unfunded_opening_capital_cannot_be_assumed_from_the_policy(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    with pytest.raises(StorageUnavailable):
        await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                               subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                               account_lock_root=tmp_path / 'account-locks', create_binding=True, owner=owner,
                               clock=lambda: NOW, fill_policy=POLICY,
                               accounting_policy=replace(ACCOUNTING, opening_quote_usdt=D('100')))
    assert ReservationLedger.restore(files.reservations, limits=RISK).usdt_balance == D('10')


@pytest.mark.asyncio
async def test_late_cashflow_updates_actual_money_without_rearming(capital_bundle):
    store, _, binding, owner, time = capital_bundle
    time[0] = owner.expires_at + timedelta(seconds=1)
    before = await store.snapshot(binding)
    await store.apply_cashflow(transfer(observed_at=time[0]), cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert after.balance.total_quote_usdt == D('12') and after.budget == before.budget
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    with pytest.raises(StorageError):
        await store.prepare(order(owner), cut=after.cut, owner=owner)


@pytest.mark.asyncio
async def test_cashflow_cannot_borrow_from_a_later_fill_even_when_current_wallet_has_funds(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import fill
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

    files, binding, owner = create_fixture(tmp_path)
    files.reservations.unlink()  # Disposable fixture replacement only.
    ReservationLedger(life_balance=D('0'), usdt_balance=D('10'), limits=RISK, path=files.reservations)
    accounting = SpotAccountingPolicy(D('0'), D('10'), D('1'), NOW - timedelta(seconds=1), '100', (
        ApprovedSpotCashflow('101', 'LIFE', D('-.5'), AT + 100),))
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', create_binding=True, owner=owner,
                                   clock=lambda: time[0], fill_policy=POLICY, accounting_policy=accounting)
    try:
        await store.prepare(order(owner, fee_buffer_quote_usdt=D('.1')),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
        time[0] = NOW + timedelta(milliseconds=200)
        row = fill(fill_at_ms=AT + 200, independent_value_at_ms=AT + 200, independent_observed_at_ms=AT + 200)
        await store.apply_fills(fill_command(owner, fills=(row,), observed_at=time[0]),
                                cut=(await store.snapshot(binding)).cut, owner=owner)
        before = await store.snapshot(binding)
        assert before.balance.total_base_life == D('1')
        with pytest.raises(StorageError):
            await store.apply_cashflow(transfer(currency='LIFE', signed_amount=D('-.5'), transfer_at_ms=AT + 100,
                                                independent_price_usdt=D('1'), value_at_ms=AT + 100,
                                                reference_observed_at_ms=AT + 100, source_kind='independent_market',
                                                observed_at=time[0]), cut=before.cut, owner=owner)
        assert (await store.snapshot(binding)).cut == before.cut
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_cashflow_retains_account_lock_and_native_money(capital_bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = capital_bundle
    cut = (await store.snapshot(binding)).cut
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ReservationLedger.record_cashflow

    def paused(self, *args):
        result = original(self, *args)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    with patch.object(ReservationLedger, 'record_cashflow', new=paused):
        task = asyncio.create_task(store.apply_cashflow(transfer(), cut=cut, owner=owner))
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
        assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('12')
        assert (await cold.lookup_cashflow('cash1', binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await cold.close()


@pytest.mark.parametrize('stage', ['cashflow', 'nav'])
def test_killed_process_preserves_cashflow_or_nav_checkpoint(tmp_path, stage):
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
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import (
    NOW,POLICY,ACCOUNTING,BUDGET,CAPACITY,RISK,transfer,nav)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles,open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
async def main():
    path=Path(sys.argv[1])
    files=LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    store=await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
        subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,fill_policy=POLICY,accounting_policy=ACCOUNTING,
        account_lock_root=path/'account-locks',create_binding=True,owner=owner,clock=lambda:NOW)
    if sys.argv[2]=='cashflow':
        original=ReservationLedger.record_cashflow
        def crash(self,*args):
            original(self,*args)
            os._exit(61)
        ReservationLedger.record_cashflow=crash
    await store.apply_cashflow(transfer(),cut=(await store.snapshot(binding)).cut,owner=owner)
    original=FileCheckpointStore._commit
    def crash(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        if args[2].startswith('nav:'):
            os._exit(62)
        return result
    FileCheckpointStore._commit=crash
    await store.observe_nav(nav(),cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == (61 if stage == 'cashflow' else 62), result.stderr

    async def readback():
        cold = await cold_open(files, binding, tmp_path)
        try:
            assert (await cold.snapshot(binding)).balance.total_quote_usdt == D('12')
            if stage == 'cashflow':
                assert (await cold.lookup_cashflow('cash1', binding)).state == 'RECOVERY_REQUIRED'
            else:
                capital = await cold.capital(binding)
                assert capital.net_cashflows_usdt == D('2') and capital.highwater_usdt == D('15')
            assert (await cold.snapshot(binding)).recovery_required
        finally:
            await cold.close()
    asyncio.run(readback())


@pytest.mark.asyncio
async def test_deposit_after_loss_does_not_reset_subsidy_or_execution_spending(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    assert before.budget.session_execution_loss_usdt == D('.12')
    await store.apply_cashflow(transfer(), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).budget == before.budget
    capital = (await store.observe_nav(nav(independent_price_usdt=D('.9')),
                                       cut=(await store.snapshot(binding)).cut, owner=owner)).capital
    assert capital.nav_usdt == D('16.38') and capital.adjusted_nav_usdt == D('14.38')
    assert capital.execution_loss_usdt == D('.12')


@pytest.mark.asyncio
async def test_underfunded_withdrawal_keeps_proposed_facts_and_native_debit_without_releasing_holds(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    policy = replace(ACCOUNTING, approved_cashflows=(ApprovedSpotCashflow('101', 'USDT', D('-8'), AT),))
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', create_binding=True, owner=owner,
                                   clock=lambda: NOW, fill_policy=POLICY, accounting_policy=policy)
    try:
        await prepare_arm(store, binding, owner)
        with pytest.raises(CommitUnknown):
            await store.apply_cashflow(transfer(signed_amount=D('-8')), cut=(await store.snapshot(binding)).cut, owner=owner)
    finally:
        await store.close()
    ledger = ReservationLedger.restore(files.reservations, limits=RISK)
    assert ledger.usdt_balance == D('2') and ledger.reserved_usdt == D('2')
    assert ledger.cashflow_events == {'101': ('USDT', D('-8'))}
    # The remaining quote fee buffer makes spendable funds negative. No healthy
    # snapshot/automatic rearm is fabricated; coordinator retains proposed facts.
    import json
    raw = json.loads(files.binding.read_text())
    assert raw['state']['resources']['cashflow_operations']['cash1']['state'] == 'CASHFLOWING'
    assert raw['state']['resources']['commands']['operation1']['command']['fee_buffer_quote_usdt'] == '0.1'
