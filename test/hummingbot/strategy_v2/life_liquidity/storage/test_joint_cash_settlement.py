"""Joint cash uses the existing wallet, accounting and execution-loss journals."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import (
    ACCOUNTING,
    AT,
    capital_bundle as spot_capital_bundle,
    cold_open,
    nav,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot

capital_bundle = spot_capital_bundle


def command(binding, owner, **changes):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW
    return replace(JointCashSpot('joint1', binding, 'c' * 64, NOW,
                                 (JointCashEvent(f'JOINT:{AT}:FUNDING:101', D('-.1')),
                                  JointCashEvent(f'JOINT:{AT}:FUNDING:102', D('.04')),
                                  JointCashEvent(f'JOINT:{AT}:TRADE:swap1', D('.024'), D('.025'), D('-.001'),
                                                 str(owner.session_id))),
                                 D('5'), D('9.964'), True, True, 'okx_joint_reconciled'), **changes)


@pytest.mark.asyncio
async def test_signed_funding_and_swap_pnl_fee_use_one_wallet_and_are_not_external_transfers(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    before = await store.snapshot(binding)
    await store.observe_nav(nav(), cut=before.cut, owner=owner)
    cmd = command(binding, owner)
    original = (await store.snapshot(binding)).cut
    receipt = await store.apply_joint_cash(cmd, cut=original, owner=owner)
    after = await store.snapshot(binding)
    assert receipt.state == 'APPLIED' and not hasattr(receipt, 'send_authorized')
    assert after.balance.total_quote_usdt == D('9.964')
    assert after.budget.session_execution_loss_usdt == D('.001')
    assert after.budget.session_subsidy_committed_usdt == 0
    assert not (await store.capital(binding)).current
    measured = await store.observe_nav(nav(operation_id='nav2'), cut=after.cut, owner=owner)
    assert measured.capital.nav_usdt == D('14.964')
    assert measured.capital.adjusted_nav_usdt == D('14.964')
    assert measured.capital.net_cashflows_usdt == 0
    assert measured.capital.highwater_usdt == D('15')
    assert (await store.apply_joint_cash(cmd, cut=original, owner=owner)).replayed
    assert (await store.snapshot(binding)).balance == after.balance


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['account', 'incomplete', 'uid', 'future', 'owner', 'underfunded'])
async def test_unqualified_joint_cash_does_not_change_money_or_budget(capital_bundle, fault):
    store, _, binding, owner, _ = capital_bundle
    before = await store.snapshot(binding)
    cmd = command(binding, owner)
    if fault == 'account':
        cmd = replace(cmd, account_quote_usdt=D('99'))
    elif fault == 'incomplete':
        cmd = replace(cmd, order_scope_complete=False)
    elif fault == 'uid':
        cmd = replace(cmd, binding=replace(binding, account_uid='999'))
    elif fault == 'future':
        from datetime import timedelta
        cmd = replace(cmd, observed_at=cmd.observed_at + timedelta(seconds=1))
    elif fault == 'owner':
        owner = replace(owner, epoch=owner.epoch + 1)
    else:
        cmd = replace(cmd, events=(JointCashEvent(f'JOINT:{AT}:FUNDING:101', D('-11')),), account_quote_usdt=D('0'))
    with pytest.raises(StorageError):
        await store.apply_joint_cash(cmd, cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'receipt'])
async def test_interrupted_cash_and_loss_resume_original_effect_without_new_admission(capital_bundle, tmp_path,
                                                                                      monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    store, files, binding, owner, _ = capital_bundle
    cmd = command(binding, owner)
    cut = (await store.snapshot(binding)).cut
    target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet')
                      else (store._loss, 'record') if stage == 'loss_after' else (store, '_publish_joint_cash'))
    original = getattr(target, method)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage in ('wallet_after', 'loss_after'):
            original(*args, **kwargs)
        raise OSError('synthetic interrupted checkpoint')

    with monkeypatch.context() as patches:
        patches.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_joint_cash(cmd, cut=cut, owner=owner)
    await store.close()
    restored = await cold_open(files, binding, tmp_path, ACCOUNTING)
    try:
        assert (await restored.snapshot(binding)).recovery_required
        with pytest.raises(StorageError):
            await restored.apply_joint_cash(cmd, cut=cut, owner=owner)
        assert (await restored.resume_joint_cash(cmd, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await restored.resume_joint_cash(cmd, cut=cut, owner=owner)).replayed
        after = await restored.snapshot(binding)
        assert after.balance.total_quote_usdt == D('9.964')
        assert after.budget.session_execution_loss_usdt == D('.001')
        assert restored._cold_unarmed and restored._observation_generation is None
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_history', 'changed_amount', 'changed_timestamp'])
async def test_new_proof_cannot_hide_or_rewrite_published_cash(capital_bundle, fault):
    store, _, binding, owner, _ = capital_bundle
    cmd = command(binding, owner)
    await store.apply_joint_cash(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    events = list(cmd.events)
    if fault == 'missing_history':
        events.pop()
    elif fault == 'changed_amount':
        events[0] = replace(events[0], signed_amount_usdt=D('-.2'))
    else:
        event = events[0]
        events.append(replace(event, event_id=event.event_id.replace(str(AT), str(AT - 1))))
    with pytest.raises(StorageError):
        update = replace(cmd, operation_id='joint2', proof_id='d' * 64, events=tuple(events))
        await store.apply_joint_cash(update, cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
