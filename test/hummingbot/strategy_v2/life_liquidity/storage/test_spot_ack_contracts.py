"""ACK storage tests use actual journals and synthetic exchange observations."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    bundle as _bundle_fixture,
    order,
)
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, AttemptState
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot

bundle = _bundle_fixture


def ack(owner, **changes):
    return replace(AckSpot('ack1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                           str(owner.session_id), owner.epoch, '123456789', NOW), **changes)


async def armed(store, binding, owner, **changes):
    await store.prepare(order(owner, **changes), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)


async def reopen(files, binding, tmp_path, now=NOW):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: now)


@pytest.mark.asyncio
async def test_ack_persists_exchange_identity_and_retains_principal_fees_and_budget(bundle):
    store, files, binding, owner, _ = bundle
    await armed(store, binding, owner, fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.01'))
    before = await store.snapshot(binding)
    receipt = await store.acknowledge(ack(owner), cut=before.cut, owner=owner)
    row = await store.attempt('intent1', binding)
    assert row.state is AttemptState.ACKED and row.exchange_order_id == '123456789'
    assert IntentWAL(files.wal).get('intent1').state == 'ACKED'
    after = await store.snapshot(binding)
    assert after.balance == before.balance and after.budget == before.budget
    assert not after.recovery_required and not receipt.replayed
    assert not hasattr(receipt, 'send_authorized')


@pytest.mark.asyncio
async def test_ack_replay_with_original_or_fresh_cut_does_not_write_or_double_account(bundle):
    store, _, binding, owner, _ = bundle
    await armed(store, binding, owner)
    original = (await store.snapshot(binding)).cut
    command = ack(owner)
    await store.acknowledge(command, cut=original, owner=owner)
    completed = await store.snapshot(binding)
    for cut in (original, completed.cut):
        assert (await store.acknowledge(command, cut=cut, owner=owner)).replayed
        assert (await store.snapshot(binding)).cut == completed.cut
    with pytest.raises(StorageError):
        await store.acknowledge(replace(command, exchange_order_id='987'), cut=completed.cut, owner=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'prepare_operation_id': 'unknown'}, {'intent_id': 'foreign'}, {'client_order_id': 'foreign'},
    {'session_id': 'foreign'}, {'epoch': 2}, {'side': 'SELL'},
    {'observed_at': NOW + timedelta(seconds=1)},
])
async def test_foreign_or_future_ack_is_rejected_without_modifying_attempt(bundle, changes):
    store, _, binding, owner, _ = bundle
    await armed(store, binding, owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.acknowledge(ack(owner, **changes), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut
    assert (await store.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN


@pytest.mark.asyncio
async def test_ack_before_arm_and_stale_cut_and_foreign_owner_are_rejected(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    prepared = (await store.snapshot(binding)).cut
    with pytest.raises(StorageError):
        await store.acknowledge(ack(owner), cut=prepared, owner=owner)
    await store.arm('operation1', cut=prepared, owner=owner)
    cut = (await store.snapshot(binding)).cut
    for bad_cut, bad_owner in ((prepared, owner), (cut, replace(owner, epoch=2)),
                               (replace(cut, checkpoint=replace(cut.checkpoint,
                                                                binding=replace(binding, account_uid='999'))), owner)):
        with pytest.raises(StorageError):
            await store.acknowledge(ack(owner), cut=bad_cut, owner=bad_owner)
    assert (await store.snapshot(binding)).cut == cut


@pytest.mark.asyncio
async def test_late_ack_is_recorded_after_expiry_without_rearming_session(bundle):
    store, _, binding, owner, time = bundle
    await armed(store, binding, owner)
    time[0] = owner.expires_at + timedelta(seconds=1)
    view = await store.snapshot(binding)
    await store.acknowledge(ack(owner, observed_at=time[0]), cut=view.cut, owner=owner)
    assert (await store.attempt('intent1', binding)).state is AttemptState.ACKED
    assert (await store.snapshot(binding)).balance == view.balance
    with pytest.raises(StorageError):
        await store.prepare(order(owner, operation_id='operation2', intent_id='intent2', client_order_id='wire2'),
                            cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_exchange_order_id_cannot_be_assigned_to_a_second_intent(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.prepare(order(owner, operation_id='operation2', intent_id='intent2', client_order_id='wire2', quote_level=1),
                        cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation2', cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageError):
        await store.acknowledge(ack(owner, operation_id='ack2', prepare_operation_id='operation2', intent_id='intent2',
                                    client_order_id='wire2'), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.snapshot(binding)).recovery_required
    assert (await store.attempt('intent2', binding)).state is AttemptState.SEND_UNKNOWN


@pytest.mark.asyncio
async def test_acked_duplicate_with_new_operation_identity_is_safe(bundle):
    store, _, binding, owner, _ = bundle
    await armed(store, binding, owner)
    await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    result = await store.acknowledge(ack(owner, operation_id='ack2'),
                                     cut=(await store.snapshot(binding)).cut, owner=owner)
    assert result.state == 'ACKED'
    assert (await store.snapshot(binding)).balance.reserved_quote_usdt == D('2')


@pytest.mark.asyncio
async def test_crash_after_ack_wal_keeps_all_holds_and_exposes_incomplete_readback(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await armed(store, binding, owner, fee_buffer_quote_usdt=D('.1'))
    original = type(store)._publish_ack

    def interrupted(self, key, *args):
        if key == 'ack-done:ack1':
            raise OSError('synthetic publication interruption')
        return original(self, key, *args)

    with patch.object(type(store), '_publish_ack', new=interrupted):
        with pytest.raises(CommitUnknown):
            await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageUnavailable):
        await store.snapshot(binding)
    await store.close()
    cold = await reopen(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.reserved_quote_usdt == D('2.1')
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
        receipt = await cold.lookup_ack('ack1', binding)
        assert receipt.state == 'RECOVERY_REQUIRED' and receipt.exchange_order_id == '123456789'
        with pytest.raises(StorageError):
            await cold.acknowledge(ack(owner), cut=view.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_completed_ack_survives_cold_restart_but_does_not_grant_rearm(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await armed(store, binding, owner)
    await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await reopen(files, binding, tmp_path)
    try:
        receipt = await cold.lookup_ack('ack1', binding)
        assert receipt.state == 'ACKED' and receipt.replayed
        assert (await cold.snapshot(binding)).recovery_required
        assert (await cold.attempt('intent1', binding)).exchange_order_id == '123456789'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_missing_native_ack_postcondition_cannot_publish_success(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await armed(store, binding, owner)
    with patch.object(IntentWAL, 'acknowledge', return_value=False):
        with pytest.raises(CommitUnknown):
            await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await reopen(files, binding, tmp_path)
    try:
        assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
        assert (await cold.lookup_ack('ack1', binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == D('2')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_competing_ack_commands_cannot_assign_two_exchange_ids(bundle):
    store, _, binding, owner, _ = bundle
    await armed(store, binding, owner)
    cut = (await store.snapshot(binding)).cut
    results = await asyncio.gather(
        store.acknowledge(ack(owner), cut=cut, owner=owner),
        store.acknowledge(ack(owner, operation_id='ack2', exchange_order_id='987'), cut=cut, owner=owner),
        return_exceptions=True)
    assert sum(isinstance(value, StorageError) for value in results) == 1
    assert (await store.attempt('intent1', binding)).exchange_order_id == '123456789'


@pytest.mark.parametrize('changes', [
    {'exchange_order_id': ''}, {'exchange_order_id': 'foreign'}, {'exchange_order_id': '1' * 129},
    {'client_order_id': 'invalid-wire'}, {'epoch': True}, {'market': 'BTC-USDT'},
    {'observed_at': NOW.replace(tzinfo=None)},
])
def test_ack_model_rejects_invalid_exchange_identity_and_timestamp(changes):
    from types import SimpleNamespace
    with pytest.raises(StorageError):
        ack(SimpleNamespace(session_id='session1', epoch=1), **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['DRAINING', 'RECOVERY_ONLY', 'UNARMED'])
async def test_observation_keeps_owner_mode_and_never_reopens_normal_admission(bundle, mode):
    store, _, binding, owner, _ = bundle
    await armed(store, binding, owner)

    def fixture_mode():
        # Test-only control-plane fixture; this is not a runtime grant API.
        with store._state.locked() as state:
            state['ownership']['mode'] = mode
            store._state.commit(state)

    await store._call(fixture_mode)
    before = await store.snapshot(binding)
    if mode == 'UNARMED':
        with pytest.raises(StorageError):
            await store.acknowledge(ack(owner), cut=before.cut, owner=owner)
        assert (await store.snapshot(binding)).cut == before.cut
    else:
        await store.acknowledge(ack(owner), cut=before.cut, owner=owner)
        assert (await store.current_owner(binding)).mode.value == mode
    with pytest.raises(StorageError):
        await store.prepare(order(owner, operation_id='operation2', intent_id='intent2', client_order_id='wire2'),
                            cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_cancelled_ack_retains_account_lock_until_worker_finishes(bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = bundle
    await armed(store, binding, owner, fee_buffer_quote_usdt=D('.1'))
    cut = (await store.snapshot(binding)).cut
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = IntentWAL.acknowledge

    def paused(self, *args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(self, *args)

    with patch.object(IntentWAL, 'acknowledge', new=paused):
        task = asyncio.create_task(store.acknowledge(ack(owner), cut=cut, owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            close = asyncio.create_task(store.close())
            await asyncio.sleep(0)
            with pytest.raises(AccountLockUnavailable):
                await reopen(files, binding, tmp_path)
        finally:
            release.set()
            await store.close()
        await close
    cold = await reopen(files, binding, tmp_path)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.reserved_quote_usdt == D('2.1')
        assert (await cold.lookup_ack('ack1', binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.attempt('intent1', binding)).state is AttemptState.ACKED
    finally:
        await cold.close()


@pytest.mark.parametrize('stage', ['before_wal', 'after_wal'])
def test_killed_ack_worker_leaves_durable_barrier_and_holds(tmp_path, stage):
    import subprocess
    import sys
    from pathlib import Path
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import create_fixture

    files, binding, owner = create_fixture(tmp_path)
    child = r'''
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack, armed
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    LegacySpotFiles, BUDGET, CAPACITY, NOW, RISK)
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
async def main():
    path = Path(sys.argv[1])
    files = LegacySpotFiles(*(path/name for name in (
        'binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record = SessionStore(files.session).load().primary
    binding = StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner = OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    store = await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
        subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,
        account_lock_root=path/'account-locks',create_binding=True,owner=owner,clock=lambda:NOW)
    await armed(store,binding,owner)
    original = IntentWAL.acknowledge
    def crash(self,*args):
        if sys.argv[2] == 'after_wal':
            original(self,*args)
        os._exit(41)
    IntentWAL.acknowledge = crash
    await store.acknowledge(ack(owner),cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 41, result.stderr

    async def readback():
        cold = await reopen(files, binding, tmp_path)
        try:
            view = await cold.snapshot(binding)
            assert view.recovery_required and view.balance.reserved_quote_usdt == D('2')
            assert view.budget.session_subsidy_committed_usdt == D('.1')
            row = await cold.attempt('intent1', binding)
            assert row.state is (AttemptState.ACKED if stage == 'after_wal' else AttemptState.SEND_UNKNOWN)
            assert (await cold.lookup_ack('ack1', binding)).state == 'RECOVERY_REQUIRED'
            with pytest.raises(StorageError):
                await cold.acknowledge(ack(owner), cut=view.cut, owner=owner)
        finally:
            await cold.close()
    asyncio.run(readback())
