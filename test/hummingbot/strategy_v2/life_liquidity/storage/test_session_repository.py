"""Actual native session decisions publish behind repository recovery barriers."""
from dataclasses import replace
from datetime import timedelta

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick

pytest_plugins = ['test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts']


@pytest.mark.asyncio
async def test_session_tick_pause_resume_and_expiry_preserve_financial_state(bundle):
    store, _, binding, owner, clock = bundle
    before = await store.snapshot(binding)
    command = SessionTick('pause', str(owner.session_id), owner.epoch, False, False, False, 1000)
    result = await store.tick_session(command, cut=before.cut, owner=owner)
    assert result.state == 'PAUSED' and result.reason_code == 'SESSION_GATE_UNAVAILABLE'
    after = await store.snapshot(binding)
    assert after.session.state == 'PAUSED' and not after.recovery_required
    assert (after.balance, after.budget) == (before.balance, before.budget)
    assert (await store.tick_session(command, cut=before.cut, owner=owner)).replayed
    resume = replace(command, operation_id='resume', reference_ready=True, all_gates_ready=True)
    assert (await store.tick_session(resume, cut=after.cut, owner=owner)).state == 'ACTIVE'
    clock[0] = before.session.expires_at
    expire = replace(command, operation_id='expire')
    result = await store.tick_session(expire, cut=(await store.snapshot(binding)).cut, owner=owner)
    assert result.state == 'EXPIRED'
    final = await store.snapshot(binding)
    assert (final.balance, final.budget) == (before.balance, before.budget)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_native', 'after_native', 'receipt'])
async def test_interrupted_tick_cold_resume_completes_original_target_without_rearm(tmp_path, monkeypatch, stage):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    files, binding, owner = create_fixture(tmp_path)
    settings = dict(binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET, loss_policy=BUDGET,
                    config_version=1, account_lock_root=tmp_path / 'locks', clock=lambda: NOW)
    store = await open_legacy_spot(files, **settings, create_binding=True, owner=owner)
    command = SessionTick('pause', str(owner.session_id), owner.epoch, False, False, False, 1000)
    before = await store.snapshot(binding)
    target, method = (store, '_publish_session') if stage == 'receipt' else (store._sessions, 'save')
    original = getattr(target, method)

    def fail(*args, **kwargs):
        if stage == 'receipt':
            if not args[0].startswith('session-done:'):
                return original(*args, **kwargs)
        elif stage == 'after_native':
            original(*args, **kwargs)
        raise OSError('synthetic session publication interruption')
    monkeypatch.setattr(target, method, fail)
    with pytest.raises(StorageError, match='SESSION_TICK_UNKNOWN'):
        await store.tick_session(command, cut=before.cut, owner=owner)
    await store.close()
    cold = await open_legacy_spot(files, **settings)
    try:
        pending = await cold.snapshot(binding)
        assert pending.recovery_required
        pending_commands = await cold.pending_session_ticks(binding)
        assert len(pending_commands) == 1 and pending_commands[0].command == command
        assert pending_commands[0].owner == owner and pending_commands[0].original_cut == before.cut
        result = await cold.tick_session(pending_commands[0].command, cut=pending_commands[0].original_cut,
                                         owner=pending_commands[0].owner)
        assert result.replayed and result.state == 'PAUSED'
        after = await cold.snapshot(binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert cold._cold_unarmed
        assert await cold.pending_session_ticks(binding) == ()
        with pytest.raises(StorageError):
            await cold.tick_session(replace(command, operation_id='new', reference_ready=True, all_gates_ready=True),
                                    cut=after.cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_session_tick_binds_writer_cut_scope_and_policy(bundle):
    store, _, binding, owner, clock = bundle
    command = SessionTick('pause', str(owner.session_id), owner.epoch, False, False, False, 1000)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.tick_session(replace(command, session_id='foreign'), cut=before.cut, owner=owner)
    await store.tick_session(command, cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.tick_session(replace(command, operation_id='stale'), cut=before.cut, owner=owner)
    with pytest.raises(StorageError):
        await store.tick_session(replace(command, operation_id='policy', max_reconciliation_age_ms=1),
                                 cut=(await store.snapshot(binding)).cut, owner=owner)
    clock[0] -= timedelta(milliseconds=1)
    with pytest.raises(StorageError):
        await store.tick_session(replace(command, operation_id='rollback'),
                                 cut=(await store.snapshot(binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_session_resume_cannot_adopt_unrelated_native_loss_mutation(tmp_path, monkeypatch):
    from decimal import Decimal
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    files, binding, owner = create_fixture(tmp_path)
    settings = dict(binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET, loss_policy=BUDGET,
                    config_version=1, account_lock_root=tmp_path / 'locks', clock=lambda: NOW)
    store = await open_legacy_spot(files, **settings, create_binding=True, owner=owner)
    command = SessionTick('pause', str(owner.session_id), owner.epoch, False, False, False, 1000)
    before = await store.snapshot(binding)
    monkeypatch.setattr(store._sessions, 'save', lambda _: (_ for _ in ()).throw(OSError('synthetic interruption')))
    with pytest.raises(StorageError):
        await store.tick_session(command, cut=before.cut, owner=owner)
    await store.close()
    LossBudgetLedger(files.loss, **BUDGET.kwargs()).record('unexpected-native-loss', Decimal('.1'),
                                                           session_id=str(owner.session_id), at_utc=NOW)
    cold = await open_legacy_spot(files, **settings)
    try:
        with pytest.raises(StorageError, match='SESSION_TICK_UNKNOWN'):
            await cold.tick_session(command, cut=before.cut, owner=owner)
        await cold.close()
        cold = await open_legacy_spot(files, **settings)
        view = await cold.snapshot(binding)
        assert view.recovery_required and view.session.state == 'ACTIVE'
        assert view.budget.session_execution_loss_usdt == Decimal('.1')
        assert len(await cold.pending_session_ticks(binding)) == 1
        assert cold._cold_unarmed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_market_successor_requires_complete_account_facts_and_separate_signed_activation(tmp_path):
    from decimal import Decimal
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import ACCOUNTING
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
        order,
    )
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionStore
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import (
        SpotFinancialRecoveryFacts,
        SpotFinancialRecoveryPolicy,
    )
    files, binding, owner = create_fixture(tmp_path)
    sessions = SessionStore(files.session)
    journal = sessions.load()
    sessions.save(replace(journal, on_expiry='switch_to_market_reference', successor_duration_seconds=Decimal('3600')))
    clock = [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET,
                                   loss_policy=BUDGET, config_version=1, account_lock_root=tmp_path / 'locks',
                                   clock=lambda: clock[0], create_binding=True, owner=owner,
                                   accounting_policy=ACCOUNTING, fill_policy=SpotFillPolicy(1000),
                                   recovery_policy=SpotFinancialRecoveryPolicy(1000),
                                   operator_keys=KEYS)
    try:
        clock[0] = journal.primary.expires_at
        tick = SessionTick('expire', str(owner.session_id), owner.epoch, False, False, False, 1000)
        assert (await store.tick_session(tick, cut=(await store.snapshot(binding)).cut, owner=owner)).state == 'TRANSITIONING'
        result = OrderReconciliation(str(owner.session_id), owner.epoch, clock[0], True, (), (), (), True)
        account = SpotFinancialRecoveryFacts(binding, clock[0], Decimal('5'), Decimal('10'), Decimal('0'), Decimal('0'),
                                             (), (), (), (), True, True, True)
        switch = replace(tick, operation_id='successor', reference_ready=True, all_gates_ready=True,
                         market_reference_ready=True, market_anchor_usdt=Decimal('1.1'),
                         reconciliation=result, account=account)
        with pytest.raises(StorageError, match='SESSION_RECONCILIATION_UNQUALIFIED'):
            await store.tick_session(replace(switch, account=replace(account, order_scope_complete=False)),
                                     cut=(await store.snapshot(binding)).cut, owner=owner)
        assert (await store.tick_session(switch, cut=(await store.snapshot(binding)).cut, owner=owner)).state == 'ACTIVE'
        view = await store.snapshot(binding)
        assert view.session.epoch == owner.epoch + 1 and view.session.anchors_usdt['LIFE-USDT'] == Decimal('1.1')
        with pytest.raises(StorageError):
            await store.prepare(order(owner), cut=view.cut, owner=owner)
        new_owner = replace(owner, generation=UUID(int=99), session_id=UUID(view.session.session_id),
                            epoch=view.session.epoch, valid_from=clock[0], expires_at=view.session.expires_at)
        activation = OwnerTransition('activate-successor', OwnerMode.ACTIVE, new_owner)
        approval = KEYS.sign('offline-operator', binding, 'ACTIVATE', activation.json(), view.cut, account,
                             issued_at=clock[0], expires_at=clock[0] + timedelta(seconds=1))
        await store.transition_owner(activation, approval=approval, facts=account, cut=view.cut)
        receipt = await store.prepare(order(new_owner), cut=(await store.snapshot(binding)).cut, owner=new_owner)
        assert receipt.state == 'PREPARED'
    finally:
        await store.close()
