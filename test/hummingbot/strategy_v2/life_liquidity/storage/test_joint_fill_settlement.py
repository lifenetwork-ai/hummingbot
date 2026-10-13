"""One accounting barrier covers spot fills and signed SWAP settled cash."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_settlement import command as cash_command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import (
    capital_bundle as spot_bundle,
    cold_open,
    nav,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    AT,
    command as fill_command,
    prepare_arm,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent

capital_bundle = spot_bundle


@pytest.mark.asyncio
async def test_joint_batch_books_fill_fee_loss_subsidy_and_swap_cash_once(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner)
    before = (await store.snapshot(binding)).cut
    cmd = replace(cash_command(binding, owner), spot_fills=(fill_command(owner),),
                  account_base_life=D('6'), account_quote_usdt=D('8.944'))
    await store.apply_joint_cash(cmd, cut=before, owner=owner)
    after = await store.snapshot(binding)
    assert after.balance.total_base_life == D('6')
    assert after.balance.total_quote_usdt == D('8.944')
    assert after.budget.session_execution_loss_usdt == D('.121')
    assert after.budget.session_subsidy_committed_usdt == D('.12')
    assert (await store.apply_joint_cash(cmd, cut=before, owner=owner)).replayed
    assert (await store.snapshot(binding)).cut == after.cut
    assert (await store.lookup_fill('fill1', binding)).state == 'APPLIED'
    assert store._wal.get('intent1').state == 'ACKED'
    assert store._ledger.reservation_state('intent1').state != 'TERMINAL'
    measured = await store.observe_nav(nav(), cut=after.cut, owner=owner)
    assert measured.capital.nav_usdt == D('14.944')
    assert measured.capital.execution_loss_usdt == D('.121')
    assert measured.capital.net_cashflows_usdt == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'floor_after', 'receipt'])
async def test_interrupted_joint_fill_batch_repairs_original_cut_without_releasing_holds(capital_bundle, tmp_path,
                                                                                         monkeypatch, stage):
    store, files, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner, acknowledge=False)
    cut = (await store.snapshot(binding)).cut
    cmd = replace(cash_command(binding, owner), spot_fills=(fill_command(owner),),
                  account_base_life=D('6'), account_quote_usdt=D('8.944'))
    target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet') else
                      (store._loss, 'record') if stage == 'loss_after' else
                      (store._subsidy, 'record_fill_floor') if stage == 'floor_after' else
                      (store, '_publish_joint_cash'))
    original = getattr(target, method)

    def interrupted(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage in ('wallet_after', 'loss_after', 'floor_after'):
            original(*args, **kwargs)
        raise OSError('synthetic interrupted publication')

    with monkeypatch.context() as patches:
        patches.setattr(target, method, interrupted)
        with pytest.raises(CommitUnknown):
            await store.apply_joint_cash(cmd, cut=cut, owner=owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        with pytest.raises(StorageError):
            await cold.apply_joint_cash(cmd, cut=cut, owner=owner)
        assert (await cold.resume_joint_cash(cmd, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await cold.resume_joint_cash(cmd, cut=cut, owner=owner)).replayed
        after = await cold.snapshot(binding)
        assert after.balance.total_base_life == D('6') and after.balance.total_quote_usdt == D('8.944')
        assert after.budget.session_execution_loss_usdt == D('.121')
        assert after.budget.session_subsidy_committed_usdt == D('.12')
        assert cold._wal.get('intent1').state == 'SEND_UNKNOWN'
        assert cold._ledger.reservation_state('intent1').remaining_base == D('1')
        assert cold._cold_unarmed and cold._observation_generation is None
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['reference', 'exchange', 'wire', 'price', 'cumulative', 'account'])
async def test_invalid_mixed_batch_preserves_all_journals(capital_bundle, fault):
    store, _, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner)
    before = await store.snapshot(binding)
    row = fill_command(owner)
    changes = {}
    if fault == 'reference':
        row = replace(row, fills=(replace(row.fills[0], independent_value_at_ms=AT - 1001),))
    elif fault == 'exchange':
        row = replace(row, exchange_order_id='999')
    elif fault == 'wire':
        row = replace(row, client_order_id='other')
    elif fault == 'price':
        row = replace(row, fills=(replace(row.fills[0], price_usdt=D('2')),))
    elif fault == 'cumulative':
        row = replace(row, cumulative_base_life=D('3'), fills=(replace(row.fills[0], quantity_base_life=D('3')),))
    else:
        changes['account_quote_usdt'] = D('9')
    cmd = replace(cash_command(binding, owner), spot_fills=(row,),
                  account_base_life=D('6'), account_quote_usdt=D('8.944'))
    cmd = replace(cmd, **changes)
    with pytest.raises(StorageError):
        await store.apply_joint_cash(cmd, cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget


@pytest.mark.asyncio
@pytest.mark.parametrize('offset,allowed', [(-1, True), (0, False)])
async def test_funding_can_finance_a_later_fee_but_not_an_equal_time_spot_fill(capital_bundle, offset, allowed):
    store, _, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner, quantity_base=D('2'), limit_price_usdt=D('4'))
    before = await store.snapshot(binding)
    row = fill_command(owner)
    row = replace(row, cumulative_base_life=D('2'),
                  fills=(replace(row.fills[0], quantity_base_life=D('2'), price_usdt=D('4'),
                                 signed_fee=D('-3'), independent_price_usdt=D('4')),))
    cmd = replace(cash_command(binding, owner), spot_fills=(row,),
                  events=(JointCashEvent(f'JOINT:{AT + offset}:FUNDING:101', D('3')),),
                  account_base_life=D('7'), account_quote_usdt=D('2'))
    if allowed:
        await store.apply_joint_cash(cmd, cut=before.cut, owner=owner)
        after = await store.snapshot(binding)
        assert after.balance.total_quote_usdt == D('2') and after.balance.total_base_life == D('7')
        assert after.budget.session_execution_loss_usdt == D('3')
    else:
        with pytest.raises(StorageError):
            await store.apply_joint_cash(cmd, cut=before.cut, owner=owner)
        assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
@pytest.mark.parametrize('currency,fee,base,quote,loss', [
    ('LIFE', '-.02', '5.98', '8.964', '.119'),
    ('LIFE', '.02', '6.02', '8.964', '.083'),
    ('USDT', '.02', '6', '8.984', '.081'),
])
async def test_mixed_batch_preserves_life_fees_and_signed_rebates(capital_bundle, currency, fee, base, quote, loss):
    store, _, binding, owner, _ = capital_bundle
    await prepare_arm(store, binding, owner)
    row = fill_command(owner)
    row = replace(row, fills=(replace(row.fills[0], fee_currency=currency, signed_fee=D(fee)),))
    cmd = replace(cash_command(binding, owner), spot_fills=(row,),
                  account_base_life=D(base), account_quote_usdt=D(quote))
    await store.apply_joint_cash(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
    view = await store.snapshot(binding)
    assert view.balance.total_base_life == D(base) and view.balance.total_quote_usdt == D(quote)
    assert view.budget.session_execution_loss_usdt == D(loss)
