"""Pending ACK resumes only the exact captured exchange identity; no send/rearm."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    NOW,
    command as fill_command,
    prepare_arm,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    bundle as _bundle_fixture,
    cold_open,
    facts as account_facts,
)
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts

bundle = _bundle_fixture


def facts(binding, **changes):
    account = account_facts(binding, kind='cash', account_quote_usdt=D('10'), account_cashflow_ids=(),
                            accepted_wire_ids=('wire1',), runner_wire_ids=('wire1',))
    return replace(SpotAckRecoveryFacts(account, 'wire1', '123456789', 'LIFE-USDT', 'BUY', D('2'), D('1'),
                                        D('0'), 'LIVE'), **changes)


async def pending_ack(store, binding, owner, stage='wal', *, filled=False):
    await prepare_arm(store, binding, owner, acknowledge=False)
    if filled:
        await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    if stage == 'wal':
        with patch.object(IntentWAL, 'acknowledge', side_effect=OSError('synthetic missing WAL ACK')):
            with pytest.raises(CommitUnknown):
                await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    else:
        original = type(store)._publish_ack

        def interrupted(self, key, *args):
            if key == 'ack-done:ack1':
                raise OSError('synthetic missing final ACK receipt')
            return original(self, key, *args)

        with patch.object(type(store), '_publish_ack', new=interrupted):
            with pytest.raises(CommitUnknown):
                await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'receipt'])
async def test_ack_resume_before_or_after_native_checkpoint_preserves_holds_and_cold_unarmed(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner, stage)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        receipt = await cold.resume_ack(ack(owner), facts=facts(binding), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert receipt.state == 'ACKED' and not receipt.replayed
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
        assert (await cold.attempt('intent1', binding)).exchange_order_id == '123456789'
        assert (await cold.resume_ack(ack(owner), facts=facts(binding), cut=after.cut, owner=owner)).replayed
        assert (await cold.snapshot(binding)).cut == after.cut
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=after.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'client_order_id': 'wire2'}, {'exchange_order_id': '999'}, {'side': 'SELL'},
    {'quantity_base_life': D('3')}, {'limit_price_usdt': D('2')},
    {'cumulative_base_life': D('1'), 'exchange_state': 'PARTIALLY_FILLED'},
])
async def test_foreign_order_payload_or_unaccounted_fill_cannot_resume_ack(bundle, tmp_path, changes):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_ack(ack(owner), facts=facts(binding, **changes), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
        assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'order_scope_complete': False}, {'bill_scope_complete': False}, {'runner_scope_complete': False},
    {'account_quote_usdt': D('9')}, {'account_trade_ids': ('foreign',)},
    {'account_cashflow_ids': ('999',)}, {'accepted_wire_ids': ()}, {'runner_wire_ids': ('foreign',)},
    {'observed_at': NOW - timedelta(milliseconds=1001)}, {'observed_at': NOW + timedelta(milliseconds=1)},
])
async def test_unqualified_account_facts_cannot_adopt_ack(bundle, tmp_path, changes):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        evidence = facts(binding)
        evidence = replace(evidence, account=replace(evidence.account, **changes))
        with pytest.raises(StorageError):
            await cold.resume_ack(ack(owner), facts=evidence, cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_ack_operation_owner_binding_and_cut_remain_exact(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    stale = (await store.snapshot(binding)).cut
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        foreign = facts(binding)
        foreign = replace(foreign, account=replace(foreign.account, binding=replace(binding, account_uid='999')))
        for cmd, evidence, cut, token in (
            (ack(owner, operation_id='new'), facts(binding), before.cut, owner),
            (ack(owner, exchange_order_id='999'), facts(binding), before.cut, owner),
            (ack(owner, observed_at=NOW - timedelta(milliseconds=1)), facts(binding), before.cut, owner),
            (ack(owner), facts(binding), before.cut, replace(owner, epoch=2)),
            (ack(owner), foreign, before.cut, owner),
            (ack(owner), facts(binding), stale, owner),
        ):
            with pytest.raises(StorageError):
                await cold.resume_ack(cmd, facts=evidence, cut=cut, owner=token)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_terminal_status_in_ack_facts_does_not_release_any_hold(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        await cold.resume_ack(ack(owner), facts=facts(binding, exchange_state='CANCELED'), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert (await cold.reservation('intent1', binding)).state != 'TERMINAL'
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_ack_after_stored_partial_fill_preserves_accounting_and_fee_buffer(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner, filled=True)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        evidence = facts(binding, account=account_facts(binding), cumulative_base_life=D('1'), exchange_state='PARTIALLY_FILLED')
        await cold.resume_ack(ack(owner), facts=evidence, cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.balance.total_quote_usdt == D('8.98') and after.balance.fee_reserved_quote_usdt == D('.08')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_expired_original_ack_requires_fresh_account_order_observation_not_new_command(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    later = owner.expires_at + timedelta(seconds=1)
    cold = await cold_open(files, binding, tmp_path, now=later)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_ack(ack(owner), facts=facts(binding), cut=before.cut, owner=owner)
        evidence = facts(binding)
        evidence = replace(evidence, account=replace(evidence.account, observed_at=later))
        await cold.resume_ack(ack(owner), facts=evidence, cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).recovery_required
        assert (await cold.lookup_ack('ack1', binding)).state == 'ACKED'
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side,quantity', [('BUY', D('2')), ('SELL', D('1')), ('SELL', D('2'))])
async def test_ack_resume_preserves_partial_and_full_buy_sell_fill_economics(bundle, tmp_path, side, quantity):
    store, files, binding, owner, _ = bundle
    await prepare_arm(store, binding, owner, acknowledge=False, side=side)
    original = fill_command(owner)
    fill = replace(original.fills[0], quantity_base_life=quantity)
    await store.apply_fills(replace(original, side=side, cumulative_base_life=quantity, fills=(fill,)),
                            cut=(await store.snapshot(binding)).cut, owner=owner)
    with patch.object(IntentWAL, 'acknowledge', side_effect=OSError('synthetic missing ACK')):
        with pytest.raises(CommitUnknown):
            await store.acknowledge(ack(owner, side=side), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        account = account_facts(binding, account_base_life=before.balance.total_base_life,
                                account_quote_usdt=before.balance.total_quote_usdt)
        evidence = facts(binding, account=account, side=side, cumulative_base_life=quantity,
                         exchange_state='FILLED' if quantity == 2 else 'PARTIALLY_FILLED')
        await cold.resume_ack(ack(owner, side=side), facts=evidence, cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert (await cold.reservation('intent1', binding)).state != 'TERMINAL'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_ack_noop_latches_uncertainty_and_requires_next_cold_resume(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with patch.object(IntentWAL, 'acknowledge', return_value=False):
            with pytest.raises(CommitUnknown):
                await cold.resume_ack(ack(owner), facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
        with pytest.raises(StorageUnavailable):
            await cold.snapshot(binding)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        assert (await cold.lookup_ack('ack1', binding)).state == 'RECOVERY_REQUIRED'
        await cold.resume_ack(ack(owner), facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
        assert (await cold.lookup_ack('ack1', binding)).state == 'ACKED'
    finally:
        await cold.close()


@pytest.mark.parametrize('changes', [
    {'market': 'BTC-USDT'}, {'exchange_order_id': 'invalid'}, {'client_order_id': 'invalid-wire'},
    {'exchange_state': 'UNKNOWN'}, {'quantity_base_life': D('0')},
    {'cumulative_base_life': D('3')}, {'cumulative_base_life': D('1')}, {'exchange_state': 'FILLED'},
])
def test_invalid_ack_order_fact_shapes(changes):
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', UUID(int=1), 1, 'a' * 64, 'b' * 64)
    with pytest.raises(StorageError):
        facts(binding, **changes)


@pytest.mark.asyncio
async def test_unrelated_pending_operation_cannot_be_completed_by_ack_resume(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        def extra_phase():
            with cold._state.locked() as state:
                row = dict(state['resources']['commands']['operation1'])
                row['state'] = 'ARMING'
                state['resources']['commands']['operation2'] = row
                cold._state.commit(state)
        await cold._call(extra_phase)
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_ack(ack(owner), facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cancelled_ack_resume_keeps_account_exclusion_until_worker_finishes(bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = IntentWAL.acknowledge

    def paused(self, *args):
        result = original(self, *args)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    with patch.object(IntentWAL, 'acknowledge', new=paused):
        before = await cold.snapshot(binding)
        task = asyncio.create_task(cold.resume_ack(ack(owner), facts=facts(binding), cut=before.cut, owner=owner))
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
        after = await cold.snapshot(binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        await cold.resume_ack(ack(owner), facts=facts(binding), cut=after.cut, owner=owner)
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
    finally:
        await cold.close()


@pytest.mark.parametrize('stage', ['native', 'receipt'])
def test_killed_ack_resume_keeps_exact_identity_holds_and_unarmed_restart(tmp_path, stage):
    import subprocess
    import sys
    from pathlib import Path
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import create_fixture

    files, binding, owner = create_fixture(tmp_path)
    child = '''
import asyncio, os, sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_recovery import ack, facts, pending_ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    BUDGET, CAPACITY, NOW, POLICY, RISK, ACCOUNTING, RECOVERY)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles, open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
async def main():
    path = Path(sys.argv[1])
    files = LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    session = SessionStore(files.session).load().primary
    binding = StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner = OwnerToken(UUID(int=2),UUID(int=3),1,UUID(session.session_id),NOW,session.expires_at)
    async def open_it(create=False):
        return await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
            subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,fill_policy=POLICY,
            accounting_policy=ACCOUNTING,recovery_policy=RECOVERY,
            account_lock_root=path/'account-locks',create_binding=create,owner=owner if create else None,clock=lambda:NOW)
    store = await open_it(True)
    await pending_ack(store,binding,owner)
    await store.close()
    cold = await open_it()
    if sys.argv[2] == 'native':
        original = IntentWAL.acknowledge
        def crash(self,*args):
            original(self,*args)
            os._exit(81)
        IntentWAL.acknowledge = crash
    else:
        original = FileCheckpointStore._commit
        def crash(self,*args,**kwargs):
            result = original(self,*args,**kwargs)
            if args[2] == 'ack-done:ack1':
                os._exit(81)
            return result
        FileCheckpointStore._commit = crash
    await cold.resume_ack(ack(owner),facts=facts(binding),cut=(await cold.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 81, result.stderr

    async def readback():
        cold = await cold_open(files, binding, tmp_path)
        try:
            before = await cold.snapshot(binding)
            receipt = await cold.lookup_ack('ack1', binding)
            assert receipt.state == ('ACKED' if stage == 'receipt' else 'RECOVERY_REQUIRED')
            recovered = await cold.resume_ack(ack(owner), facts=facts(binding), cut=before.cut, owner=owner)
            after = await cold.snapshot(binding)
            assert recovered.replayed is (stage == 'receipt')
            assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
            assert (await cold.attempt('intent1', binding)).exchange_order_id == '123456789'
        finally:
            await cold.close()
    asyncio.run(readback())
