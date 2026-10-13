"""Lifecycle commands remain file sagas and never infer an exchange cancellation."""
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand

pytest_plugins = ['test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts']


@pytest.mark.asyncio
async def test_cancel_attempt_is_persisted_once_and_enforces_bound_retry_interval(bundle):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageConflict
    store, _, binding, owner, clock = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    request = SpotLifecycleCommand('cancel-attempt-1', 'operation1', 'intent1', 'wire1',
                                   'RECORD_CANCEL_ATTEMPT', clock[0], 0, 100)
    await store.apply_lifecycle(request, cut=before.cut, owner=owner)
    row = await store.attempt('intent1', binding)
    assert row.cancel_attempts == 1 and row.last_cancel_attempt_at == clock[0]
    assert (await store.apply_lifecycle(request, cut=before.cut, owner=owner)).replayed
    assert (await store.attempt('intent1', binding)).cancel_attempts == 1
    with pytest.raises(StorageConflict):
        await store.apply_lifecycle(replace(request, expected_cancel_attempts=1), cut=before.cut, owner=owner)
    retry = replace(request, operation_id='cancel-attempt-2', expected_cancel_attempts=1)
    with pytest.raises(StorageError, match='SPOT_CANCEL_RETRY_NOT_DUE'):
        await store.apply_lifecycle(retry, cut=(await store.snapshot(binding)).cut, owner=owner)
    clock[0] += timedelta(milliseconds=100)
    retry = replace(retry, attempted_at=clock[0])
    with pytest.raises(StorageError, match='SPOT_CANCEL_RETRY_POLICY_MISMATCH'):
        await store.apply_lifecycle(replace(retry, retry_interval_ms=1),
                                    cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_lifecycle(retry, cut=(await store.snapshot(binding)).cut, owner=owner)
    row = await store.attempt('intent1', binding)
    assert row.cancel_attempts == 2 and row.last_cancel_attempt_at == clock[0]
    after = await store.snapshot(binding)
    assert after.balance == before.balance and after.budget == before.budget


@pytest.mark.asyncio
async def test_interrupted_native_cancel_attempt_resumes_cold_without_repeating_attempt(tmp_path, monkeypatch):
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
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    request = SpotLifecycleCommand('cancel-attempt', 'operation1', 'intent1', 'wire1',
                                   'RECORD_CANCEL_ATTEMPT', NOW, 0, 100)
    original = store._wal.mark_cancel_attempt

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('synthetic interruption after native publication')
    monkeypatch.setattr(store._wal, 'mark_cancel_attempt', fail)
    with pytest.raises(StorageError, match='SPOT_LIFECYCLE_UNKNOWN'):
        await store.apply_lifecycle(request, cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    cold = await open_legacy_spot(files, **settings)
    try:
        cut = (await cold.snapshot(binding)).cut
        receipt = await cold.apply_lifecycle(request, cut=cut, owner=owner)
        assert receipt.replayed
        assert (await cold.attempt('intent1', binding)).cancel_attempts == 1
        assert (await cold.snapshot(binding)).balance.reserved_quote_usdt == 2
        assert cold._cold_unarmed
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_abort_prepared_releases_only_proven_unsent_holds_and_deduplicates(bundle):
    store, _, binding, owner, _ = bundle
    command = order(owner)
    await store.prepare(command, cut=(await store.snapshot(binding)).cut, owner=owner)
    request = SpotLifecycleCommand('abort-1', 'operation1', 'intent1', 'wire1', 'ABORT_PREPARED')
    cut = (await store.snapshot(binding)).cut
    receipt = await store.apply_lifecycle(request, cut=cut, owner=owner)
    assert receipt.state == 'ABORTED_BEFORE_SEND'
    assert (await store.apply_lifecycle(request, cut=cut, owner=owner)).replayed
    view = await store.snapshot(binding)
    assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
    assert view.balance.total_quote_usdt == 10 and not view.recovery_required
    with pytest.raises(StorageError):
        await store.arm('operation1', cut=view.cut, owner=owner)


@pytest.mark.asyncio
async def test_unknown_send_cannot_abort_and_cancel_preserves_principal_and_budgets(bundle):
    store, _, binding, owner, _ = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    request = SpotLifecycleCommand('control-1', 'operation1', 'intent1', 'wire1', 'ABORT_PREPARED')
    view = await store.snapshot(binding)
    with pytest.raises(StorageError, match='SPOT_ABORT_NOT_PROVEN_UNSENT'):
        await store.apply_lifecycle(request, cut=view.cut, owner=owner)
    cancel = replace(request, action='REQUEST_CANCEL')
    receipt = await store.apply_lifecycle(cancel, cut=view.cut, owner=owner)
    assert receipt.state == 'SEND_UNKNOWN'
    after = await store.snapshot(binding)
    assert after.balance == view.balance and after.budget == view.budget
    assert (await store.attempt('intent1', binding)).cancel_requested
    assert (await store.apply_lifecycle(cancel, cut=view.cut, owner=owner)).replayed
    assert not (await store.check_spot_send(order(owner), owner=owner)).qualified


@pytest.mark.asyncio
async def test_send_check_requires_exact_command_fresh_owner_and_complete_unknown_hold(bundle):
    store, _, binding, owner, clock = bundle
    await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    assert not (await store.check_spot_send(order(owner), owner=owner)).qualified
    await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
    result = await store.check_spot_send(order(owner), owner=owner)
    assert result.qualified and result.binding == binding
    assert not hasattr(result, 'send_authorized')
    assert not (await store.check_spot_send(replace(order(owner), quantity_base=Decimal('3')), owner=owner)).qualified
    clock[0] = owner.expires_at
    assert not (await store.check_spot_send(order(owner), owner=owner)).qualified


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'capital', 'subsidy', 'receipt'])
async def test_interrupted_abort_keeps_overlays_and_cold_resume_does_not_activate(tmp_path, stage, monkeypatch):
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
    prepared = replace(order(owner), fee_buffer_quote_usdt=Decimal('.03'))
    await store.prepare(prepared, cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    request = SpotLifecycleCommand('abort', 'operation1', 'intent1', 'wire1', 'ABORT_PREPARED')
    target, method = {'wal': (store._wal, 'abort_before_send'), 'capital': (store._ledger, 'abort_unsent'),
                      'subsidy': (store._subsidy, 'release_unsent'), 'receipt': (store, '_publish_lifecycle')}[stage]
    original = getattr(target, method)

    def fail(*args, **kwargs):
        if stage == 'receipt':
            if not args[0].startswith('lifecycle-done:'):
                return original(*args, **kwargs)
        else:
            original(*args, **kwargs)
        raise OSError('synthetic interruption')
    monkeypatch.setattr(target, method, fail)
    with pytest.raises(StorageError, match='SPOT_LIFECYCLE_UNKNOWN'):
        await store.apply_lifecycle(request, cut=before.cut, owner=owner)
    await store.close()
    cold = await open_legacy_spot(files, **settings)
    try:
        retained = await cold.snapshot(binding)
        assert retained.balance.reserved_quote_usdt == before.balance.reserved_quote_usdt
        assert retained.budget.session_subsidy_committed_usdt == before.budget.session_subsidy_committed_usdt
        receipt = await cold.apply_lifecycle(request, cut=retained.cut, owner=owner)
        assert receipt.replayed and receipt.state == 'ABORTED_BEFORE_SEND'
        result = await cold.snapshot(binding)
        assert result.balance.reserved_quote_usdt == result.budget.session_subsidy_committed_usdt == 0
        assert cold._cold_unarmed
        assert not (await cold.check_spot_send(prepared, owner=owner)).qualified
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_signed_drain_revokes_send_but_accepts_late_ack_from_same_writer(tmp_path):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
    files, binding, owner = create_fixture(tmp_path)
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET,
                                   loss_policy=BUDGET, config_version=1, account_lock_root=tmp_path / 'locks',
                                   create_binding=True, owner=owner, clock=lambda: NOW, operator_keys=KEYS)
    try:
        await store.prepare(order(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=owner)
        command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        facts = SpotFinancialRecoveryFacts(binding, NOW, Decimal('5'), Decimal('10'), Decimal('0'), Decimal('0'),
                                           (), (), ('wire1',), ('wire1',), True, True, True)
        cut = (await store.snapshot(binding)).cut
        approval = KEYS.sign('offline-operator', binding, 'DRAIN', command.json(), cut, facts,
                             issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        await store.transition_owner(command, approval=approval, facts=facts, cut=cut)
        assert not (await store.check_spot_send(order(owner), owner=owner)).qualified
        result = await store.acknowledge(ack(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
        assert result.state == 'ACKED'
        assert (await store.snapshot(binding)).balance.reserved_quote_usdt == Decimal('2')
    finally:
        await store.close()
