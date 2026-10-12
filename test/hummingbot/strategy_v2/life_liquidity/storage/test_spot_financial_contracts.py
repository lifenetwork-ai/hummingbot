"""Spot contracts use actual legacy files and no exchange transport."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.config import SessionConfig
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits
from hummingbot.strategy_v2.life_liquidity.session import SessionManager, SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileLimits
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState, BudgetPolicy, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles, open_legacy_spot

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
RISK = RiskLimits(D('0'), D('100'), D('1000'), D('100'))
BUDGET = BudgetPolicy('synthetic', D('10'), D('5'), D('2'))
CAPACITY = FileLimits(2, 1024 * 1024, 128)  # Explicit offline fixture values.


def create_fixture(path):
    files = LegacySpotFiles(path / 'binding.json', path / 'session.json', path / 'wal.json',
                            path / 'reservations.json', path / 'subsidy.json', path / 'loss.json')
    manager = SessionManager(SessionStore(files.session), wall_clock=lambda: NOW,
                             monotonic_clock=lambda: 1, max_reconciliation_age_ms=1000)
    session = manager.begin(SessionConfig(duration='1h'), anchors={'LIFE-USDT': D('1')},
                            model_version='test', config_version=1, reference_mode='market', ready=True)
    wal = IntentWAL(files.wal)
    wal.initialize_empty()
    ReservationLedger(life_balance=D('5'), usdt_balance=D('10'), limits=RISK, path=files.reservations)
    SubsidyBudgetLedger(files.subsidy, **BUDGET.kwargs()).initialize_empty()
    LossBudgetLedger(files.loss, **BUDGET.kwargs()).record('opening', D('0'), session_id=session.session_id, at_utc=NOW)
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', UUID(int=1), 1, 'a' * 64, 'b' * 64)
    owner = OwnerToken(UUID(int=2), UUID(int=3), session.epoch, UUID(session.session_id), NOW, session.expires_at)
    return files, binding, owner


@pytest_asyncio.fixture
async def bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    time = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', owner=owner,
                                   create_binding=True, clock=lambda: time[0])
    try:
        yield store, files, binding, owner, time
    finally:
        await store.close()


def order(owner, **changes):
    return replace(PrepareSpot('operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY', D('2'), D('1'),
                               D('1'), D('.1'), str(owner.session_id), owner.epoch, 1, 0, D('0'), D('0')), **changes)


@pytest.mark.asyncio
async def test_prepare_publishes_real_intent_capital_and_subsidy_holds(bundle):
    store, files, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    result = await store.prepare(order(owner), cut=cut, owner=owner)
    assert result.state == 'PREPARED' and not result.replayed
    view = await store.snapshot(binding)
    assert view.balance.reserved_quote_usdt == D('2')
    assert view.balance.available_quote_usdt == D('8')
    assert view.budget.session_subsidy_committed_usdt == D('.1')
    attempt = await store.attempt('intent1', binding)
    assert attempt.state is AttemptState.PREPARED
    assert IntentWAL(files.wal).get('intent1').state == 'PREPARED'
    assert ReservationLedger.restore(files.reservations, limits=RISK).reserved_usdt == D('2')
    assert not hasattr(result, 'send_authorized')


@pytest.mark.asyncio
async def test_replay_has_no_duplicate_holds_and_changed_id_conflicts(bundle):
    store, _, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    command = order(owner)
    await store.prepare(command, cut=cut, owner=owner)
    assert (await store.prepare(command, cut=cut, owner=owner)).replayed
    for changed in [replace(command, quantity_base=D('3')), replace(command, client_order_id='wire2')]:
        with pytest.raises(StorageConflict):
            await store.prepare(changed, cut=cut, owner=owner)
    assert (await store.snapshot(binding)).balance.reserved_quote_usdt == D('2')


@pytest.mark.asyncio
async def test_insufficient_cash_or_budget_rejected_before_any_intent(bundle):
    store, files, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    for changes in [{'quantity_base': D('11')}, {'subsidy_quote_usdt': D('3')}]:
        with pytest.raises(StorageError):
            await store.prepare(order(owner, **changes), cut=cut, owner=owner)
    assert not IntentWAL(files.wal).all_records()
    view = await store.snapshot(binding)
    assert view.balance.reserved_quote_usdt == 0 and view.budget.session_subsidy_committed_usdt == 0


@pytest.mark.asyncio
async def test_arm_is_durable_unknown_and_second_arm_is_rejected(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
    with pytest.raises(StorageError):
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.reserved_quote_usdt == D('2')
    assert view.budget.session_subsidy_committed_usdt == D('.1')


@pytest.mark.asyncio
async def test_expired_session_or_foreign_owner_cannot_prepare_or_arm(bundle):
    store, _, binding, owner, time = bundle
    cut = (await store.snapshot(binding)).cut
    with pytest.raises(StorageError):
        await store.prepare(order(owner), cut=cut, owner=replace(owner, epoch=2))
    await store.prepare(order(owner), cut=cut, owner=owner)
    time[0] = owner.expires_at
    with pytest.raises(StorageError):
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.attempt('intent1', binding)).state is AttemptState.PREPARED
    assert (await store.snapshot(binding)).balance.reserved_quote_usdt == D('2')


@pytest.mark.asyncio
async def test_failure_after_reservation_retains_hold_and_cold_blocks_admission(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    with patch.object(SubsidyBudgetLedger, 'reserve', side_effect=OSError('synthetic crash')):
        with pytest.raises(CommitUnknown):
            await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    with pytest.raises(StorageUnavailable):
        await store.snapshot(binding)
    await store.close()
    cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.reserved_quote_usdt == D('2')
        assert (await cold.lookup_prepare('operation1', binding)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError):
            await cold.prepare(order(owner), cut=view.cut, owner=owner)
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=view.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cold_restart_preserves_complete_receipt_and_unknown_attempt(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    await store.prepare(order(owner), cut=cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    try:
        assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
        assert (await cold.lookup_prepare('operation1', binding)).state == 'SEND_UNKNOWN'
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == D('2')
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_missing_journal_never_bootstraps_or_resets_budget(bundle, tmp_path):
    store, files, binding, _, _ = bundle
    await store.close()
    files.subsidy.unlink()
    with pytest.raises(StorageUnavailable):
        await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                               subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                               account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    assert not files.subsidy.exists()


@pytest.mark.asyncio
async def test_foreign_binding_and_stale_cut_denied(bundle):
    store, _, binding, owner, _ = bundle
    with pytest.raises(StorageError):
        await store.snapshot(replace(binding, account_uid='9'))
    stale = (await store.snapshot(binding)).cut
    await store.prepare(order(owner), cut=stale, owner=owner)
    with pytest.raises(StorageConflict):
        await store.prepare(order(owner, operation_id='two', intent_id='two', client_order_id='wire2'),
                            cut=stale, owner=owner)


@pytest.mark.parametrize('field,value', [
    ('quantity_base', 1.0), ('quantity_base', D('NaN')), ('limit_price_usdt', D('Infinity')),
    ('quantity_base', D('1e999999')), ('quote_level', True),
    ('subsidy_quote_usdt', D('-1')), ('epoch', True), ('market', 'BTC-USDT'), ('client_order_id', 'bad-wire'),
])
def test_typed_order_rejects_invalid_units_values_and_identity(field, value):
    owner = OwnerToken(UUID(int=2), UUID(int=3), 1, UUID(int=4), NOW, NOW + timedelta(hours=1))
    with pytest.raises(StorageError):
        order(owner, **{field: value})


@pytest.mark.asyncio
async def test_quote_slot_and_unknown_attempt_revoke_new_preparation(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    other = order(owner, operation_id='two', intent_id='two', client_order_id='wire2')
    with pytest.raises(StorageConflict, match='SLOT'):
        await store.prepare(other, cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    other = replace(other, quote_level=1)
    with pytest.raises(StorageError):
        await store.prepare(other, cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.balance(binding)).reserved_quote_usdt == D('2')


@pytest.mark.asyncio
async def test_concurrent_preparations_cannot_allocate_from_stale_cut(bundle):
    store, _, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    results = await asyncio.gather(
        store.prepare(order(owner), cut=cut, owner=owner),
        store.prepare(order(owner, operation_id='two', intent_id='two', client_order_id='wire2', quote_level=1),
                      cut=cut, owner=owner), return_exceptions=True)
    assert sum(isinstance(v, StorageConflict) for v in results) == 1
    assert (await store.balance(binding)).reserved_quote_usdt == D('2')


@pytest.mark.asyncio
async def test_admission_lock_excludes_second_owner_and_generic_uow_is_unsupported(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import UnsupportedBackend
    with pytest.raises(AccountLockUnavailable):
        await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                               subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                               account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    with pytest.raises(UnsupportedBackend):
        store.unit_of_work((await store.snapshot(binding)).cut.checkpoint, owner)
    assert (await store.balance(binding)).total_quote_usdt == D('10')


@pytest.mark.asyncio
async def test_external_pause_or_journal_tamper_cannot_arm_and_holds_remain(bundle):
    store, files, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    journal = SessionStore(files.session).load()
    SessionStore(files.session).save(replace(journal, state='PAUSED', reason_code='operator-stop'))
    with pytest.raises(StorageError):
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    assert ReservationLedger.restore(files.reservations, limits=RISK).reserved_usdt == D('2')


@pytest.mark.asyncio
async def test_filled_subsidy_and_execution_loss_survive_cold_readback(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    SubsidyBudgetLedger(files.subsidy, **BUDGET.kwargs()).record_fill_floor('intent1', D('.4'))
    LossBudgetLedger(files.loss, **BUDGET.kwargs()).record('adverse', D('2.5'),
                                                           session_id=str(owner.session_id), at_utc=NOW)
    cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    try:
        budget = await cold.budget(binding)
        assert budget.session_subsidy_committed_usdt == D('.4')
        assert budget.session_execution_loss_usdt == D('2.5') and budget.loss_exhausted
        assert (await cold.balance(binding)).reserved_quote_usdt == D('2')
    finally:
        await cold.close()


@pytest.mark.parametrize('stage', ['reservation', 'arm'])
def test_killed_process_preserves_interrupted_saga_and_unresolved_holds(tmp_path, stage):
    import subprocess
    import sys

    files, binding, owner = create_fixture(tmp_path)
    child = r'''
import asyncio
import os
import sys
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    LegacySpotFiles, RISK, BUDGET, CAPACITY, NOW, order)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from uuid import UUID

async def main():
    path=Path(sys.argv[1])
    files=LegacySpotFiles(*(path/name for name in ('binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    record=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(record.session_id),NOW,record.expires_at)
    store=await open_legacy_spot(files,binding=binding,limits=CAPACITY,risk_limits=RISK,
        subsidy_policy=BUDGET,loss_policy=BUDGET,config_version=1,
        account_lock_root=path/'account-locks',create_binding=True,owner=owner,clock=lambda:NOW)
    if sys.argv[2]=='reservation':
        original=ReservationLedger.reserve
        def crash(self,*args,**kwargs):
            original(self,*args,**kwargs)
            os._exit(31)
        ReservationLedger.reserve=crash
    await store.prepare(order(owner),cut=(await store.snapshot(binding)).cut,owner=owner)
    original=IntentWAL.arm_send
    def crash(self,*args,**kwargs):
        original(self,*args,**kwargs)
        os._exit(32)
    IntentWAL.arm_send=crash
    await store.arm('operation1',cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    process = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                             cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert process.returncode == (31 if stage == 'reservation' else 32), process.stderr

    async def verify():
        cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                      subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                      account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
        try:
            view = await cold.snapshot(binding)
            assert view.recovery_required and view.balance.reserved_quote_usdt == D('2')
            assert (await cold.lookup_prepare('operation1', binding)).state == 'RECOVERY_REQUIRED'
            if stage == 'arm':
                assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
                assert view.budget.session_subsidy_committed_usdt == D('.1')
            with pytest.raises(StorageError):
                await cold.arm('operation1', cut=view.cut, owner=owner)
        finally:
            await cold.close()
    asyncio.run(verify())


@pytest.mark.asyncio
async def test_cancelled_preparation_does_not_refund_or_release_owner_early(bundle, tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable

    store, files, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ReservationLedger.reserve

    def paused(self, *args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(self, *args, **kwargs)

    with patch.object(ReservationLedger, 'reserve', paused):
        prepare = asyncio.create_task(store.prepare(order(owner), cut=cut, owner=owner))
        await entered.wait()
        prepare.cancel()
        with pytest.raises(asyncio.CancelledError):
            await prepare
        close = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        try:
            with pytest.raises(AccountLockUnavailable):
                await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                       subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                       account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
        finally:
            release.set()
            await close
    cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    try:
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.balance.reserved_quote_usdt == D('2')
        assert (await cold.lookup_prepare('operation1', binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cold_complete_prepare_requires_new_rearm_even_with_old_owner_token(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)
    try:
        assert (await cold.lookup_prepare('operation1', binding)).state == 'PREPARED'
        view = await cold.snapshot(binding)
        assert view.recovery_required
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=view.cut, owner=owner)
        assert view.balance.reserved_quote_usdt == D('2')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_budget_policy_change_and_foreign_config_restore_fail(bundle, tmp_path):
    store, files, binding, _, _ = bundle
    await store.close()
    for policy, version in [(BudgetPolicy('synthetic', D('20'), D('10'), D('4')), 1), (BUDGET, 2)]:
        with pytest.raises(StorageError):
            await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=policy, loss_policy=BUDGET, config_version=version,
                                   account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW)


@pytest.mark.asyncio
async def test_exact_decimal_canonicalization_preserves_replay_identity(bundle):
    store, _, binding, owner, _ = bundle
    command = order(owner, quantity_base=D('2.000'), subsidy_quote_usdt=D('0.100'))
    cut = (await store.snapshot(binding)).cut
    await store.prepare(command, cut=cut, owner=owner)
    assert (await store.prepare(order(owner), cut=cut, owner=owner)).replayed
    snapshot = await store.snapshot(binding)
    assert snapshot.session.anchors_usdt['LIFE-USDT'] == D('1')
    with pytest.raises(TypeError):
        snapshot.session.anchors_usdt['LIFE-USDT'] = D('2')


@pytest.mark.asyncio
async def test_sell_holds_base_and_duplicate_wire_id_is_permanent(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner, side='SELL'), cut=(await store.snapshot(binding)).cut, owner=owner)
    reservation = await store.reservation('intent1', binding)
    assert reservation.remaining_base_life == D('2') and reservation.side == 'SELL'
    snapshot = await store.snapshot(binding)
    assert snapshot.balance.reserved_base_life == D('2') and snapshot.balance.available_base_life == D('3')
    with pytest.raises(StorageConflict, match='WIRE'):
        await store.prepare(order(owner, operation_id='two', intent_id='two', side='BUY', quote_level=1),
                            cut=snapshot.cut, owner=owner)


@pytest.mark.asyncio
async def test_capital_includes_quote_and_base_fee_buffers_without_double_application(bundle):
    store, _, binding, owner, _ = bundle
    cut = (await store.snapshot(binding)).cut
    with pytest.raises(StorageError, match='INCLUDING_FEES'):
        await store.prepare(order(owner, quantity_base=D('10'), fee_buffer_quote_usdt=D('.1')), cut=cut, owner=owner)
    with pytest.raises(StorageError, match='INCLUDING_FEES'):
        await store.prepare(order(owner, side='SELL', quantity_base=D('5'), fee_buffer_base_life=D('.1')), cut=cut, owner=owner)
    command = order(owner, fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.01'))
    await store.prepare(command, cut=cut, owner=owner)
    assert (await store.prepare(command, cut=cut, owner=owner)).replayed
    balance = await store.balance(binding)
    assert balance.reserved_quote_usdt == D('2.1') and balance.available_quote_usdt == D('7.9')
    assert balance.fee_reserved_quote_usdt == D('.1') and balance.fee_reserved_base_life == D('.01')
    assert balance.available_base_life == D('4.99')
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    assert (await store.balance(binding)).reserved_quote_usdt == D('2.1')
