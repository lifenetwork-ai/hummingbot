"""Terminal storage uses explicit synthetic complete reconciliation facts."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import ACCOUNTING
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    POLICY,
    RISK,
    command as fill_command,
    create_fixture,
    fill,
    prepare_arm,
)
from unittest.mock import patch

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, SpotTerminalPolicy, TerminalSpot

TERMINAL_POLICY = SpotTerminalPolicy(1000)  # Explicit offline calibration.


@pytest_asyncio.fixture
async def terminal_bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', owner=owner, create_binding=True,
                                   clock=lambda: time[0], fill_policy=POLICY, accounting_policy=ACCOUNTING,
                                   terminal_policy=TERMINAL_POLICY)
    try:
        yield store, files, binding, owner, time
    finally:
        await store.close()


def facts(binding, **changes):
    return replace(SpotTerminalFacts(binding, NOW, 'CANCELED', D('0'), D('2'), D('1'), (),
                                     D('5'), D('10'), D('0'), D('0'), (), (), (), (), ('wire1',), True, True, True), **changes)


def terminal(binding, owner, evidence=None, **changes):
    return replace(TerminalSpot('terminal1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                                str(owner.session_id), owner.epoch, '123456789', evidence or facts(binding)), **changes)


async def cold_open(files, binding, tmp_path, now=NOW):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: now,
                                  fill_policy=POLICY, accounting_policy=ACCOUNTING, terminal_policy=TERMINAL_POLICY)


@pytest.mark.asyncio
async def test_zero_fill_terminal_releases_principal_fees_and_unused_subsidy(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    receipt = await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert receipt.state == 'TERMINAL' and receipt.subsidy_settled and not receipt.replayed
    assert view.balance.total_quote_usdt == D('10') and view.balance.total_base_life == D('5')
    assert view.balance.reserved_quote_usdt == view.balance.reserved_base_life == 0
    assert view.budget.session_subsidy_committed_usdt == 0 and view.budget.session_execution_loss_usdt == 0
    assert (await store.attempt('intent1', binding)).state is AttemptState.TERMINAL
    assert not view.recovery_required and not hasattr(receipt, 'send_authorized')


@pytest.mark.asyncio
async def test_partial_fill_terminal_releases_order_holds_but_keeps_economic_subsidy(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    evidence = facts(binding, cumulative_base_life=D('1'), fills=(fill(),), account_trade_ids=('trade1',),
                     account_base_life=D('6'), account_quote_usdt=D('8.98'))
    receipt = await store.confirm_terminal(terminal(binding, owner, evidence), cut=before.cut, owner=owner)
    view = await store.snapshot(binding)
    assert not receipt.subsidy_settled and view.budget == before.budget
    assert view.budget.session_subsidy_committed_usdt == D('.12')
    assert view.balance.reserved_quote_usdt == view.balance.reserved_base_life == 0
    assert view.balance.total_quote_usdt == D('8.98')


@pytest.mark.asyncio
async def test_full_fill_terminal_does_not_erase_loss_or_inventory_risk(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    row = fill(quantity_base_life=D('2'))
    await store.apply_fills(fill_command(owner, cumulative_base_life=D('2'), fills=(row,)),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    evidence = facts(binding, exchange_state='FILLED', cumulative_base_life=D('2'), fills=(row,),
                     account_trade_ids=('trade1',), account_base_life=D('7'), account_quote_usdt=D('7.98'))
    receipt = await store.confirm_terminal(terminal(binding, owner, evidence), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert not receipt.subsidy_settled
    assert (await store.snapshot(binding)).budget.session_execution_loss_usdt == D('.22')
    assert (await store.snapshot(binding)).budget.session_subsidy_committed_usdt == D('.22')
    assert (await store.snapshot(binding)).balance.total_base_life == D('7')


@pytest.mark.asyncio
async def test_terminal_before_ack_can_resolve_unknown_without_resending(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner, acknowledge=False)
    await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.attempt('intent1', binding)).state is AttemptState.TERMINAL


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'order_scope_complete': False}, {'bill_scope_complete': False}, {'runner_scope_complete': False},
    {'account_base_life': D('4')}, {'account_quote_usdt': D('11')}, {'liability_quote_usdt': D('.1')},
    {'liability_base_life': D('.1')}, {'account_trade_ids': ('foreign',)}, {'account_cashflow_ids': ('999',)},
    {'pending_wire_ids': ('wire1',)}, {'pending_exchange_order_ids': ('123456789',)},
    {'runner_wire_ids': ('foreign',)}, {'pending_wire_ids': ('foreign',)}, {'pending_exchange_order_ids': ('999',)},
    {'quantity_base_life': D('3')}, {'limit_price_usdt': D('2')},
    {'observed_at': NOW - timedelta(milliseconds=1001)}, {'observed_at': NOW + timedelta(milliseconds=1)},
])
async def test_incomplete_foreign_stale_or_mismatched_facts_cannot_release(terminal_bundle, changes):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner, facts(binding, **changes)), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut
    assert (await store.snapshot(binding)).balance.reserved_quote_usdt == D('2.1')


@pytest.mark.asyncio
async def test_terminal_identity_cut_owner_and_unsent_attempt_guards(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    stale = (await store.snapshot(binding)).cut
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner), cut=stale, owner=owner)
    await store.arm('operation1', cut=stale, owner=owner)
    before = await store.snapshot(binding)
    for changes in ({'intent_id': 'foreign'}, {'client_order_id': 'foreign'}, {'epoch': 2}, {'side': 'SELL'},
                    {'facts': facts(replace(binding, account_uid='999'))}):
        with pytest.raises(StorageError):
            await store.confirm_terminal(terminal(binding, owner, **changes), cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner), cut=stale, owner=owner)
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner), cut=before.cut, owner=replace(owner, epoch=2))
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_terminal_replay_and_changed_exchange_id_cannot_double_release(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    initial = (await store.snapshot(binding)).cut
    await store.confirm_terminal(terminal(binding, owner), cut=initial, owner=owner)
    after = await store.snapshot(binding)
    assert (await store.confirm_terminal(terminal(binding, owner), cut=initial, owner=owner)).replayed
    assert (await store.snapshot(binding)).cut == after.cut
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner, exchange_order_id='999'), cut=after.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.confirm_terminal(terminal(binding, owner, operation_id='terminal2'), cut=after.cut, owner=owner)


@pytest.mark.asyncio
async def test_crash_after_native_release_keeps_coordinator_hold_then_qualified_resume(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(IntentWAL, 'mark_terminal', side_effect=OSError('synthetic interruption')):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageUnavailable):
        await store.snapshot(binding)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.recovery_required and before.balance.reserved_quote_usdt == D('2.1')
        assert ReservationLedger.restore(files.reservations, limits=RISK).reserved_usdt == 0
        assert (await cold.lookup_terminal('terminal1', binding)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError):
            await cold.confirm_terminal(terminal(binding, owner), cut=before.cut, owner=owner)
        await cold.resume_terminal(terminal(binding, owner), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.recovery_required and after.balance.reserved_quote_usdt == 0
        assert after.budget.session_subsidy_committed_usdt == 0
        assert (await cold.lookup_terminal('terminal1', binding)).state == 'TERMINAL'
        assert (await cold.resume_terminal(terminal(binding, owner), cut=after.cut, owner=owner)).replayed
        with pytest.raises(StorageError):
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
            await cold.prepare(order(owner, operation_id='op2', intent_id='intent2', client_order_id='wire2'),
                               cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_noop_and_unknown_budget_completion_do_not_publish_release(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(ReservationLedger, 'confirm_terminal', return_value=None):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.reserved_quote_usdt == D('2.1')
        with patch.object(SubsidyBudgetLedger, 'settle_zero_fill_terminal', side_effect=OSError('synthetic budget interruption')):
            with pytest.raises(CommitUnknown):
                await cold.resume_terminal(terminal(binding, owner), cut=before.cut, owner=owner)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.reserved_quote_usdt == D('2.1')
        await cold.resume_terminal(terminal(binding, owner), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_resume_requires_fresh_facts_exact_pending_operation_and_same_bound_writer(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(IntentWAL, 'mark_terminal', side_effect=OSError('synthetic')):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    later = owner.expires_at + timedelta(seconds=1)
    cold = await cold_open(files, binding, tmp_path, now=later)
    try:
        cut = (await cold.snapshot(binding)).cut
        for cmd, token in ((terminal(binding, owner), owner),
                           (terminal(binding, owner, operation_id='terminal2'), owner),
                           (terminal(binding, owner, facts(binding, observed_at=later)), replace(owner, epoch=2))):
            with pytest.raises(StorageError):
                await cold.resume_terminal(cmd, cut=cut, owner=token)
        fresh = terminal(binding, owner, facts(binding, observed_at=later))
        await cold.resume_terminal(fresh, cut=cut, owner=owner)
        assert (await cold.snapshot(binding)).recovery_required
        assert (await cold.lookup_terminal('terminal1', binding)).state == 'TERMINAL'
    finally:
        await cold.close()


@pytest.mark.parametrize('changes', [
    {'exchange_state': 'LIVE'}, {'fills': (fill(),)}, {'cumulative_base_life': D('1')},
    {'order_scope_complete': 1}, {'pending_wire_ids': ['wire1']}, {'runner_wire_ids': ('wire1', 'wire1')},
    {'exchange_state': 'FILLED'}, {'observed_at': NOW.replace(tzinfo=None)},
])
def test_terminal_model_rejects_unqualified_shapes(changes):
    # Binding independent of any actual account; fixture uses no exchange.
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', UUID(int=1), 1, 'a' * 64, 'b' * 64)
    with pytest.raises(StorageError):
        facts(binding, **changes)


@pytest.mark.asyncio
async def test_final_receipt_interruption_preserves_all_coordinator_holds_and_zero_fill_budget(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    original = type(store)._publish_terminal

    def fail(self, key, *args):
        if key == 'term-done:terminal1':
            raise OSError('synthetic final receipt interruption')
        return original(self, key, *args)

    with patch.object(type(store), '_publish_terminal', new=fail):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.balance.reserved_quote_usdt == D('2.1') and view.recovery_required
        assert view.budget.session_subsidy_committed_usdt == 0
        await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cancelled_terminal_keeps_account_lock_and_durable_principal_overlay(terminal_bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    cut = (await store.snapshot(binding)).cut
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ReservationLedger.confirm_terminal

    def paused(self, *args, **kwargs):
        original(self, *args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)

    with patch.object(ReservationLedger, 'confirm_terminal', new=paused):
        task = asyncio.create_task(store.confirm_terminal(terminal(binding, owner), cut=cut, owner=owner))
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
        assert view.balance.reserved_quote_usdt == D('2.1')
        assert (await cold.lookup_terminal('terminal1', binding)).state == 'RECOVERY_REQUIRED'
        await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['mark_exchange_terminal_observed', 'mark_terminal'])
async def test_native_wal_noop_cannot_publish_terminal_success(terminal_bundle, tmp_path, method):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(IntentWAL, method, return_value=False):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.balance.reserved_quote_usdt == D('2.1')
        await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
        assert (await cold.lookup_terminal('terminal1', binding)).state == 'TERMINAL'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_budget_noop_retains_holds_until_zero_settlement_is_verified(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(SubsidyBudgetLedger, 'settle_zero_fill_terminal', return_value=False):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.budget.session_subsidy_committed_usdt == D('.1')
        assert view.balance.reserved_quote_usdt == D('2.1')
        await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
        assert (await cold.snapshot(binding)).budget.session_subsidy_committed_usdt == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_terminal_tombstones_preserve_wire_uniqueness_and_free_only_quote_slot(terminal_bundle):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    cut = (await store.snapshot(binding)).cut
    with pytest.raises(StorageError):
        await store.prepare(order(owner, operation_id='operation2', intent_id='intent2'), cut=cut, owner=owner)
    await store.prepare(order(owner, operation_id='operation2', intent_id='intent2', client_order_id='wire2'), cut=cut, owner=owner)
    assert (await store.attempt('intent1', binding)).state is AttemptState.TERMINAL
    assert (await store.reservation('intent2', binding)).state != 'TERMINAL'


@pytest.mark.asyncio
async def test_other_partial_financial_saga_cannot_be_repaired_by_terminal_resume(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    with patch.object(IntentWAL, 'mark_terminal', side_effect=OSError('synthetic')):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal(binding, owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        def test_pending_cashflow():
            with cold._state.locked() as state:
                state['resources']['cashflow_operations'] = {'test': {'state': 'CASHFLOWING'}}
                state['versions']['cashflow_operations'] = 0
                cold._state.commit(state)
        await cold._call(test_pending_cashflow)
        view = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == view.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_terminal_policy_cannot_change_on_restore(terminal_bundle, tmp_path):
    store, files, binding, _, _ = terminal_bundle
    await store.close()
    for policy in (None, SpotTerminalPolicy(999)):
        with pytest.raises(StorageUnavailable):
            await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW,
                                   fill_policy=POLICY, accounting_policy=ACCOUNTING, terminal_policy=policy)


@pytest.mark.parametrize('stage', ['principal', 'budget', 'receipt'])
def test_killed_terminal_process_retains_qualified_recovery_or_completed_readback(tmp_path, stage):
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
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
    BUDGET,CAPACITY,NOW,POLICY,RISK,ACCOUNTING,TERMINAL_POLICY,prepare_arm,terminal)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles,open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
async def main():
    path=Path(sys.argv[1])
    files=LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    store=await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
        subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,fill_policy=POLICY,
        accounting_policy=ACCOUNTING,terminal_policy=TERMINAL_POLICY,
        account_lock_root=path/'account-locks',create_binding=True,owner=owner,clock=lambda:NOW)
    await prepare_arm(store,binding,owner)
    if sys.argv[2]=='receipt':
        original=FileCheckpointStore._commit
        def crash(self,*args,**kwargs):
            result=original(self,*args,**kwargs)
            if args[2].startswith('term-done:'):
                os._exit(71)
            return result
        FileCheckpointStore._commit=crash
    else:
        cls=ReservationLedger if sys.argv[2]=='principal' else SubsidyBudgetLedger
        method='confirm_terminal' if sys.argv[2]=='principal' else 'settle_zero_fill_terminal'
        original=getattr(cls,method)
        def crash(self,*args,**kwargs):
            original(self,*args,**kwargs)
            os._exit(71)
        setattr(cls,method,crash)
    await store.confirm_terminal(terminal(binding,owner),cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 71, result.stderr

    async def verify():
        cold = await cold_open(files, binding, tmp_path)
        try:
            view = await cold.snapshot(binding)
            receipt = await cold.lookup_terminal('terminal1', binding)
            assert view.recovery_required
            if stage == 'receipt':
                assert receipt.state == 'TERMINAL' and view.balance.reserved_quote_usdt == 0
            else:
                assert receipt.state == 'RECOVERY_REQUIRED' and view.balance.reserved_quote_usdt == D('2.1')
            result = await cold.resume_terminal(terminal(binding, owner), cut=view.cut, owner=owner)
            assert result.replayed is (stage == 'receipt')
            assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == 0
            assert (await cold.snapshot(binding)).recovery_required  # Cold resume never rearms.
        finally:
            await cold.close()
    asyncio.run(verify())


@pytest.mark.asyncio
async def test_sell_principal_overlay_survives_release_crash(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner, side='SELL')
    cmd = terminal(binding, owner, side='SELL')
    with patch.object(IntentWAL, 'mark_terminal', side_effect=OSError('synthetic')):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.reserved_base_life == D('2.1') and before.balance.reserved_quote_usdt == D('.1')
        await cold.resume_terminal(cmd, cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).balance.reserved_base_life == 0
        assert (await cold.snapshot(binding)).balance.total_base_life == D('5')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_filled_terminal_resume_keeps_loss_and_subsidy_floor(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    evidence = facts(binding, cumulative_base_life=D('1'), fills=(fill(),), account_trade_ids=('trade1',),
                     account_base_life=D('6'), account_quote_usdt=D('8.98'))
    cmd = terminal(binding, owner, evidence)
    with patch.object(IntentWAL, 'mark_terminal', side_effect=OSError('synthetic')):
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert before.balance.reserved_quote_usdt == D('1.08')
        result = await cold.resume_terminal(cmd, cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert not result.subsidy_settled and after.budget == before.budget
        assert after.balance.reserved_quote_usdt == 0
        assert after.budget.session_execution_loss_usdt == after.budget.session_subsidy_committed_usdt == D('.12')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_filled_snapshot_cannot_omit_trade_or_change_fee_valuation(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await prepare_arm(store, binding, owner)
    await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    for row in (fill(trade_id='foreign'), fill(signed_fee=D('-.01')), fill(independent_price_usdt=D('.8'))):
        evidence = facts(binding, cumulative_base_life=D('1'), fills=(row,), account_trade_ids=('trade1',),
                         account_base_life=D('6'), account_quote_usdt=D('8.98'))
        with pytest.raises(StorageError):
            await store.confirm_terminal(terminal(binding, owner, evidence), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_late_hot_terminal_after_expiry_releases_without_rearming(terminal_bundle):
    store, _, binding, owner, time = terminal_bundle
    await prepare_arm(store, binding, owner)
    time[0] = owner.expires_at + timedelta(seconds=1)
    cmd = terminal(binding, owner, facts(binding, observed_at=time[0]))
    await store.confirm_terminal(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.snapshot(binding)).balance.reserved_quote_usdt == 0
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    with pytest.raises(StorageError):
        await store.prepare(order(owner, operation_id='op2', intent_id='intent2', client_order_id='wire2'),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
