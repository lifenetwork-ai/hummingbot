"""Exact cold preparation/arm completion never authorizes a send."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    bundle as _bundle_fixture,
    cold_open,
    facts as account_facts,
)
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState

bundle = _bundle_fixture


def facts(binding, **changes):
    return account_facts(binding, kind='cash', account_quote_usdt=D('10'), account_cashflow_ids=(), **changes)


async def pending(store, binding, owner, stage, side='BUY'):
    cmd = order(owner, side=side, fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.1'))
    if stage in ('arm', 'armed'):
        await store.prepare(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
        if stage == 'arm':
            with patch.object(IntentWAL, 'arm_send', side_effect=OSError('synthetic arm interruption')):
                with pytest.raises(CommitUnknown):
                    await store.arm(cmd.operation_id, cut=(await store.snapshot(binding)).cut, owner=owner)
        else:
            original = type(store)._publish

            def fail(self, key, *args):
                if key.startswith('armed:'):
                    raise OSError('synthetic receipt interruption')
                return original(self, key, *args)
            with patch.object(type(store), '_publish', new=fail):
                with pytest.raises(CommitUnknown):
                    await store.arm(cmd.operation_id, cut=(await store.snapshot(binding)).cut, owner=owner)
    elif stage == 'prepared':
        original = type(store)._publish

        def fail(self, key, *args):
            if key.startswith('prepared:'):
                raise OSError('synthetic preparation receipt interruption')
            return original(self, key, *args)
        with patch.object(type(store), '_publish', new=fail):
            with pytest.raises(CommitUnknown):
                await store.prepare(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    else:
        cls, method = {'wal': (IntentWAL, 'begin'), 'capital': (ReservationLedger, 'reserve'),
                       'subsidy': (SubsidyBudgetLedger, 'reserve')}[stage]
        with patch.object(cls, method, side_effect=OSError('synthetic missing checkpoint')):
            with pytest.raises(CommitUnknown):
                await store.prepare(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    return cmd


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wal', 'capital', 'subsidy', 'prepared', 'arm', 'armed'])
async def test_cold_completion_fills_missing_holds_once_and_preserves_unknown_unarmed(bundle, tmp_path, stage, side):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, stage, side)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        if stage in ('arm', 'armed'):
            result = await cold.resume_arm(cmd.operation_id, facts=facts(binding), cut=before.cut, owner=owner)
            expected = AttemptState.SEND_UNKNOWN
            again = await cold.resume_arm(cmd.operation_id, facts=facts(binding),
                                          cut=(await cold.snapshot(binding)).cut, owner=owner)
        else:
            result = await cold.resume_prepare(cmd, facts=facts(binding), cut=before.cut, owner=owner)
            expected = AttemptState.PREPARED
            again = await cold.resume_prepare(cmd, facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
        after = await cold.snapshot(binding)
        assert result.state == expected.value and again.replayed
        assert (await cold.attempt(cmd.intent_id, binding)).state is expected
        assert after.balance.total_base_life == 5 and after.balance.total_quote_usdt == 10
        assert after.balance.reserved_quote_usdt == (D('2.1') if side == 'BUY' else D('.1'))
        assert after.balance.reserved_base_life == (D('.1') if side == 'BUY' else D('2.1'))
        assert after.budget.session_subsidy_committed_usdt == D('.1') and after.recovery_required
        with pytest.raises(StorageError):
            await cold.arm(cmd.operation_id, cut=after.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == after.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'armed'])
async def test_recovery_rejects_facts_identity_owner_and_cut_before_writes(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    stale = (await store.snapshot(binding)).cut
    cmd = await pending(store, binding, owner, stage)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        for evidence, cut, token in (
            (facts(binding, order_scope_complete=False), before.cut, owner),
            (facts(binding, account_base_life=D('6')), before.cut, owner),
            (facts(binding, account_trade_ids=('foreign',)), before.cut, owner),
            (facts(binding, accepted_wire_ids=('foreign',)), before.cut, owner),
            (facts(binding), before.cut, replace(owner, epoch=2)),
            (facts(binding), stale, owner),
        ):
            with pytest.raises(StorageError):
                if stage == 'wal':
                    await cold.resume_prepare(cmd, facts=evidence, cut=cut, owner=token)
                else:
                    await cold.resume_arm(cmd.operation_id, facts=evidence, cut=cut, owner=token)
        assert (await cold.snapshot(binding)).cut == before.cut
        with pytest.raises(StorageError):
            await cold.resume_prepare(replace(cmd, quantity_base=D('3')), facts=facts(binding), cut=before.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'arm'])
async def test_expired_completion_retains_holds_and_original_budget_day_without_rearm(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, stage)
    await store.close()
    later = owner.expires_at + timedelta(days=1)
    cold = await cold_open(files, binding, tmp_path, now=later)
    try:
        cut = (await cold.snapshot(binding)).cut
        evidence = replace(facts(binding), observed_at=later)
        if stage == 'wal':
            await cold.resume_prepare(cmd, facts=evidence, cut=cut, owner=owner)
        else:
            await cold.resume_arm(cmd.operation_id, facts=evidence, cut=cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.budget.campaign_subsidy_committed_usdt == D('.1')
        assert after.budget.day_subsidy_committed_usdt == 0 and after.recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_prepare_noop_cannot_publish_complete_receipt(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, 'wal')
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with patch.object(IntentWAL, 'begin', return_value=None):
            with pytest.raises(CommitUnknown):
                await cold.resume_prepare(cmd, facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        assert (await cold.lookup_prepare(cmd.operation_id, binding)).state == 'RECOVERY_REQUIRED'
        await cold.resume_prepare(cmd, facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_observed_accepted_wire_during_arm_recovery_keeps_unknown_not_acknowledged(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, 'armed')
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        await cold.resume_arm(cmd.operation_id, facts=facts(binding, accepted_wire_ids=('wire1',)),
                              cut=(await cold.snapshot(binding)).cut, owner=owner)
        attempt = await cold.attempt(cmd.intent_id, binding)
        assert attempt.state is AttemptState.SEND_UNKNOWN and attempt.exchange_order_id is None
        assert (await cold.snapshot(binding)).recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'arm'])
async def test_older_partial_without_original_writer_cut_is_not_guessed(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, stage)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        def old_format():
            with cold._state.locked() as state:
                state['resources']['commands'][cmd.operation_id].pop('original_cut')
                cold._state.commit(state)
        await cold._call(old_format)
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            if stage == 'wal':
                await cold.resume_prepare(cmd, facts=facts(binding), cut=before.cut, owner=owner)
            else:
                await cold.resume_arm(cmd.operation_id, facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_foreign_native_subsidy_cannot_be_adopted_during_preparation_recovery(bundle, tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import BUDGET, NOW

    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, 'wal')
    await store.close()
    native = SubsidyBudgetLedger(files.subsidy, **BUDGET.kwargs())
    native.reserve('foreign', D('.1'), session_id=cmd.session_id, at_utc=NOW)
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        with pytest.raises(StorageError):
            await cold.resume_prepare(cmd, facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).cut == before.cut
        assert (await cold.attempt(cmd.intent_id, binding)) is None
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,cls,method', [
    ('wal', ReservationLedger, 'reserve'), ('wal', SubsidyBudgetLedger, 'reserve'), ('arm', IntentWAL, 'arm_send')])
async def test_native_noop_never_publishes_success_and_cold_resume_finishes_once(bundle, tmp_path, stage, cls, method):
    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, stage)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with patch.object(cls, method, return_value=False):
            with pytest.raises(CommitUnknown):
                if stage == 'wal':
                    await cold.resume_prepare(cmd, facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
                else:
                    await cold.resume_arm(cmd.operation_id, facts=facts(binding), cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        assert (await cold.lookup_prepare(cmd.operation_id, binding)).state == 'RECOVERY_REQUIRED'
        if stage == 'wal':
            await cold.resume_prepare(cmd, facts=facts(binding), cut=before.cut, owner=owner)
        else:
            await cold.resume_arm(cmd.operation_id, facts=facts(binding), cut=before.cut, owner=owner)
        assert (await cold.snapshot(binding)).budget.session_subsidy_committed_usdt == D('.1')
    finally:
        await cold.close()


@pytest.mark.parametrize('kind,stage', [('prepare', 'native'), ('prepare', 'receipt'), ('arm', 'native'), ('arm', 'receipt')])
def test_killed_preparation_recovery_keeps_holds_and_unknown_without_send(tmp_path, kind, stage):
    import asyncio
    import subprocess
    import sys
    from pathlib import Path
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import create_fixture

    files, binding, owner = create_fixture(tmp_path)
    child = '''
import asyncio, os, sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_prepare_recovery import pending, facts
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    BUDGET, CAPACITY, NOW, POLICY, RISK, ACCOUNTING, RECOVERY)
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles, open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
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
    cmd = await pending(store,binding,owner,'wal' if sys.argv[2] == 'prepare' else 'arm')
    await store.close()
    cold = await open_it()
    if sys.argv[3] == 'native':
        cls, method = (ReservationLedger,'reserve') if sys.argv[2] == 'prepare' else (IntentWAL,'arm_send')
        original = getattr(cls,method)
        def crash(self,*args,**kwargs):
            original(self,*args,**kwargs)
            os._exit(81)
        setattr(cls,method,crash)
    else:
        original = FileCheckpointStore._commit
        def crash(self,*args,**kwargs):
            result = original(self,*args,**kwargs)
            if args[2].startswith(('prepared:', 'armed:')):
                os._exit(81)
            return result
        FileCheckpointStore._commit = crash
    if sys.argv[2] == 'prepare':
        await cold.resume_prepare(cmd,facts=facts(binding),cut=(await cold.snapshot(binding)).cut,owner=owner)
    else:
        await cold.resume_arm(cmd.operation_id,facts=facts(binding),cut=(await cold.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), kind, stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 81, result.stderr

    async def readback():
        cold = await cold_open(files, binding, tmp_path)
        try:
            before = await cold.snapshot(binding)
            record = await cold.lookup_prepare('operation1', binding)
            expected = 'PREPARED' if kind == 'prepare' else 'SEND_UNKNOWN'
            assert record.state == (expected if stage == 'receipt' else 'RECOVERY_REQUIRED')
            if kind == 'prepare':
                result = await cold.resume_prepare(order(owner, fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.1')),
                                                   facts=facts(binding), cut=before.cut, owner=owner)
            else:
                result = await cold.resume_arm('operation1', facts=facts(binding), cut=before.cut, owner=owner)
            assert result.replayed is (stage == 'receipt')
            after = await cold.snapshot(binding)
            assert after.balance.reserved_quote_usdt == D('2.1')
            assert after.budget.session_subsidy_committed_usdt == D('.1') and after.recovery_required
            assert (await cold.attempt('intent1', binding)).state.value == expected
        finally:
            await cold.close()
    asyncio.run(readback())


@pytest.mark.asyncio
async def test_cancelled_preparation_resume_retains_lock_until_physical_completion(bundle, tmp_path):
    import asyncio
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = bundle
    cmd = await pending(store, binding, owner, 'wal')
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = IntentWAL.begin

    def paused(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    with patch.object(IntentWAL, 'begin', new=paused):
        task = asyncio.create_task(cold.resume_prepare(cmd, facts=facts(binding),
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
        after = await cold.snapshot(binding)
        await cold.resume_prepare(cmd, facts=facts(binding), cut=after.cut, owner=owner)
        assert (await cold.snapshot(binding)).budget.session_subsidy_committed_usdt == D('.1')
        assert (await cold.snapshot(binding)).recovery_required
    finally:
        await cold.close()
