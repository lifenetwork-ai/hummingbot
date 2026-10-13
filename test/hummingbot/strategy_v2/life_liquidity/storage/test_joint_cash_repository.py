"""Native SWAP reconciliation publishes into the actual spot store wallet."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    create_fixture,
)
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import (
    cold_restore,
    report,
    send_close,
    setup_recovery,
)
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.storage.capital import ObserveSpotNAV, SpotAccountingPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits, open_file_joint
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles, open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalPolicy


async def fixture(tmp_path, *, spot=False):
    recovery, values, bundle, _ = setup_recovery(tmp_path / 'native')
    c, _, _, _, _, _, clock, _, _, _, _, _ = values
    _, binding, owner = create_fixture(tmp_path / 'seed')
    session = c._order_safety_manager.current_session
    binding = replace(binding, config_hash=fingerprint(c.config.strategy.model_dump(mode='json')))
    owner = replace(owner, session_id=UUID(session.session_id), epoch=session.epoch,
                    valid_from=clock.wall, expires_at=session.expires_at)
    files = LegacySpotFiles(tmp_path / 'binding.json', c._order_safety_manager.store.path,
                            c._order_safety_wal.path, c._order_safety_reservations.path,
                            tmp_path / 'subsidy.json', tmp_path / 'loss.json')
    SubsidyBudgetLedger(files.subsidy, **BUDGET.kwargs()).initialize_empty()
    LossBudgetLedger(files.loss, **BUDGET.kwargs()).record('opening', D('0'),
                                                           session_id=session.session_id, at_utc=clock.wall)
    accounting = SpotAccountingPolicy(D('10'), D('10'), D('1'), clock.wall, '100', ())
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY,
                                   risk_limits=c._order_safety_reservations.limits,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', owner=owner,
                                   create_binding=True, clock=lambda: clock.wall,
                                   fill_policy=SpotFillPolicy(1000), accounting_policy=accounting,
                                   recovery_policy=SpotFinancialRecoveryPolicy(1000),
                                   terminal_policy=SpotTerminalPolicy(1000) if spot else None)
    # Explicit isolated test assembly: the restored store objects are the sole
    # wallet/WAL; production controller installation is a separate PG1 gate.
    bind(store, recovery)
    repo = await open_file_joint(store, recovery=recovery, owner=owner,
                                 limits=JointLimits(16, 64, 64 * 1024), couple_cash=True, couple_spot=spot)
    return repo, store, recovery, values, bundle, owner


def bind(store, recovery):
    c = recovery.controller
    c._order_safety_wal = recovery.spot_wal = store._wal
    c._order_safety_reservations = recovery.reservations = store._ledger
    c._execution_loss_budget = store._loss
    c._order_safety_gateway.wal = store._wal
    if c._protected_spot_sender is not None:
        c._protected_spot_sender.reservations = store._ledger
        c._protected_spot_sender.slots.wal = store._wal
        c._protected_spot_sender.slots.reservations = store._ledger


async def reopen(tmp_path, store, recovery, values, owner):
    await store.close()
    joint, values = cold_restore(recovery, values)
    # Restore the exact controller configuration used by the persisted policy.
    restored = await open_legacy_spot(store._files, binding=store.binding, limits=CAPACITY,
                                      risk_limits=store._risk_limits, subsidy_policy=BUDGET, loss_policy=BUDGET,
                                      config_version=1, account_lock_root=tmp_path / 'account-locks',
                                      clock=lambda: values[6].wall, fill_policy=store._fill_policy,
                                      accounting_policy=store._accounting_policy, recovery_policy=store._recovery_policy,
                                      terminal_policy=store._terminal_policy)
    try:
        bind(restored, joint)
        repo = await open_file_joint(restored, recovery=joint, owner=owner,
                                     limits=JointLimits(16, 64, 64 * 1024), couple_cash=True,
                                     couple_spot=recovery._repository_application._couple_spot)
    except BaseException:
        await restored.close()
        raise
    return repo, restored, joint, values


@pytest.mark.asyncio
async def test_swap_fee_partial_terminal_and_refreshed_proof_keep_spot_wallet_and_nav_consistent(tmp_path):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        proof = report(values, bundle, claim, terminal=False, fee='-0.00100')
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('9.999')
        assert view.budget.session_execution_loss_usdt == D('.001')
        assert values[1].claim_ids() == (claim.intent_id,)
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, pending=())
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3,
                                  orders=(replace(proof, state='canceled'),), pending_wire_ids=())
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        now = values[6].wall
        at = int(now.timestamp() * 1000)
        measured = await store.observe_nav(ObserveSpotNAV('nav1', D('1'), at, at, 'independent_market', now),
                                           cut=(await store.snapshot(store.binding)).cut, owner=owner)
        assert measured.capital.nav_usdt == D('19.999')
        assert measured.capital.net_cashflows_usdt == 0
        assert measured.capital.execution_loss_usdt == D('.001')
        assert (await repo.state(store.binding)).measurement.adjusted_nav_quote == D('19.999')
        assert values[1].claim_ids() == ()
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_after', 'loss_after', 'receipt', 'wal_after'])
async def test_cold_coupled_joint_repair_never_resends_or_books_fee_twice(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        report(values, bundle, claim)
        target, name = {
            'wallet_after': (store._ledger, 'apply_joint_snapshot'),
            'loss_after': (store._loss, 'record'),
            'receipt': (store, '_publish_joint_cash'),
            'wal_after': (values[5], 'mark_terminal'),
        }[stage]
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if stage == 'receipt':
                if not args[0].startswith('joint-done:'):
                    return original(*args, **kwargs)
            else:
                original(*args, **kwargs)
            raise OSError('synthetic lost checkpoint')

        with monkeypatch.context() as patches:
            patches.setattr(target, name, interrupted)
            assert not await repo.reconcile(store.binding, owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('9.999')
        assert view.budget.session_execution_loss_usdt == D('.001')
        assert values[1].claim_ids() == ()
        assert len(values[4].sent) == 1
        assert store._cold_unarmed and store._observation_generation is None
        assert not recovery._armed and not values[2]._runtime_enabled
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_signed_funding_then_cold_refresh_updates_same_spot_wallet_once(tmp_path):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        at = recovery.clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, spot_cash_quote=D('9.94'))
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2,
                                  payments=(RecoveryPayment('101', D('-.1'), at),
                                            RecoveryPayment('102', D('.04'), at)))
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        values[6].advance(.1)
        now = recovery.clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, observed_at_ms=now)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3, observed_at_ms=now)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3, observed_at_ms=now)
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('9.94')
        assert view.budget.session_execution_loss_usdt == 0
        assert (await repo.state(store.binding)).measurement.adjusted_nav_quote == D('19.94')
        assert store._cold_unarmed and not recovery._armed
    finally:
        await store.close()
