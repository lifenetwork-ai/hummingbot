"""An original net-flat inventory settlement composes with new external cash."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryPayment, RecoveryTrade
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory, SpotSettlementPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


async def pending_cycle(tmp_path, monkeypatch, *, stage, currency='USDT', amount=D('2'), activity=None):
    at = int(setup_shared(tmp_path / 'clock')[6].wall.timestamp() * 1000)
    repo, store, recovery, values, bundle, owner = await fixture(
        tmp_path, spot=True, approvals=(ApprovedSpotCashflow('101', currency, amount, at),), settlement_policy=SpotSettlementPolicy(1000))
    assert await repo.reconcile(store.binding, owner)
    swap_claim = await store._call(send_close, values) if activity == 'swap_fee' else None
    proofs, trades = [], []
    for n, side in enumerate(('BUY', 'SELL'), 1):
        prepared = order(owner, operation_id=f'operation{n}', intent_id=f'intent{n}', client_order_id=f'wire{n}', side=side,
                         subsidy_quote_usdt=D('.5'), fee_buffer_quote_usdt=D('.1'))
        claim = CapitalClaim(prepared.intent_id, prepared.client_order_id, store.binding.account_uid, prepared.session_id,
                             prepared.epoch, 1, 1, 'SPOT', side, 'OPEN', prepared.quantity_base, prepared.limit_price_usdt, 1)
        assert (await store._call(values[1].reserve, claim)).allowed
        await store.prepare(prepared, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.arm(prepared.operation_id, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        fill = SpotFillObservation(f'trade{n}', D('1'), D('1'), 'USDT', D('-.02'), at,
                                   D('.9') if side == 'BUY' else D('1.1'), at, at, 'independent_market')
        exchange = str(123456788 + n)
        await store.apply_fills(FillSpot(f'fill{n}', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                                         prepared.market, side, prepared.session_id, prepared.epoch, exchange, D('1'), (fill,), values[6].wall),
                                cut=(await store.snapshot(store.binding)).cut, owner=owner)
        trades.append(fill.trade_id)
        view = await store.snapshot(store.binding)
        facts = SpotTerminalFacts(store.binding, values[6].wall, 'CANCELED', D('1'), prepared.quantity_base, D('1'), (fill,),
                                  view.balance.total_base_life, view.balance.total_quote_usdt, D('0'), D('0'),
                                  tuple(trades), (), (), (), (), True, True, True)
        await store.confirm_terminal(TerminalSpot(f'terminal{n}', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                                                  prepared.market, side, prepared.session_id, prepared.epoch, exchange, facts),
                                     cut=view.cut, owner=owner)
        proofs.append(RecoveryOrder(claim, exchange, 'canceled',
                                    (RecoveryTrade(fill.trade_id, D('1'), D('1'), 'USDT', D('-.02'), D('0'), at),)))
    other = None
    if activity == 'fill':
        other = order(owner, operation_id='other-prepare', intent_id='other', client_order_id='otherwire',
                      quote_level=1, subsidy_quote_usdt=D('.5'), fee_buffer_quote_usdt=D('.1'))
        claim = CapitalClaim(other.intent_id, other.client_order_id, store.binding.account_uid, other.session_id,
                             other.epoch, 1, 1, 'SPOT', 'BUY', 'OPEN', other.quantity_base, other.limit_price_usdt, 1)
        assert (await store._call(values[1].reserve, claim)).allowed
        await store.prepare(other, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.arm(other.operation_id, cut=(await store.snapshot(store.binding)).cut, owner=owner)
    before = await store.snapshot(store.binding)
    facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, before.balance.total_base_life, before.balance.total_quote_usdt,
                                       D('0'), D('0'), tuple(trades), (),
                                       ('wire1', 'wire2') + (() if other is None else ('otherwire',)), (), True, True, True)
    command = SettleSpotInventory('original-cycle', 'cycle', ('intent1', 'intent2'))
    target, name = (store, '_publish_settlement') if stage == 'receipt' else (store._subsidy, '_settle_inventory_cycle')
    original = getattr(target, name)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('settle-done:'):
            return original(*args, **kwargs)
        if stage != 'before':
            original(*args, **kwargs)
        raise OSError('synthetic cycle interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, name, crash)
        with pytest.raises(CommitUnknown):
            await store.settle_inventory(command, facts=facts, cut=before.cut, owner=owner)
    base = facts.account_base_life + (amount if currency == 'LIFE' else 0)
    quote = facts.account_quote_usdt + (amount if currency == 'USDT' else 0) - D('.1')
    extra_fills = ()
    if other is not None:
        fill = SpotFillObservation('other-trade', D('1'), D('1'), 'USDT', D('-.02'), at,
                                   D('.9'), at, at, 'independent_market')
        proofs.append(RecoveryOrder(claim, '999', 'partially_filled',
                                    (RecoveryTrade(fill.trade_id, D('1'), D('1'), 'USDT', D('-.02'), D('0'), at),)))
        extra_fills = (FillSpot('other-fill', other.operation_id, other.intent_id, other.client_order_id, other.market,
                                other.side, other.session_id, other.epoch, '999', D('1'), (fill,), facts.observed_at),)
        trades.append(fill.trade_id)
        base += 1
        quote -= D('1.02')
    if swap_claim is not None:
        proofs.append(report(values, bundle, swap_claim))
        quote -= D('.001')
    values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, spot_life_base=base, spot_cash_quote=quote,
                                    pending=() if other is None else (claim,))
    values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
    bundle['value'] = replace(bundle['value'], snapshot_sequence=2, orders=tuple(proofs),
                              pending_wire_ids=() if other is None else ('otherwire',),
                              payments=(RecoveryPayment('202', D('-.1'), at),))
    flow = CashflowSpot('new-flow', '101', currency, amount, at, D('1') if currency == 'USDT' else D('1.2'), at, at,
                        'quote_unit' if currency == 'USDT' else 'independent_market', facts.observed_at)
    facts = replace(facts, account_base_life=base, account_quote_usdt=quote, account_cashflow_ids=('101',), account_trade_ids=tuple(trades))
    repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
    return repo, store, recovery, values, owner, facts, flow, command, before, extra_fills


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before', 'after', 'receipt'])
@pytest.mark.parametrize('currency,amount', [('USDT', D('2')), ('USDT', D('-2')), ('LIFE', D('2')), ('LIFE', D('-2'))])
async def test_pending_inventory_cycle_then_transfer_and_funding_preserve_spending_and_original_costs(tmp_path, monkeypatch, stage, currency, amount):
    repo, store, recovery, values, owner, facts, flow, command, before, _ = await pending_cycle(
        tmp_path, monkeypatch, stage=stage, currency=currency, amount=amount)
    try:
        assert await repo.reconcile(store.binding, owner, spot_facts=facts, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert after.balance.reserved_quote_usdt == after.balance.reserved_base_life == 0
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt == D('.24')
        assert after.budget.session_subsidy_committed_usdt == D('.24')
        row = thaw((await store.read(store.binding)).resources['settlement_operations'][command.operation_id])
        assert row['command'] == command.json() and row['state'] == 'SETTLED'
        assert values[1].claim_ids() == ()
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        replay = await store.snapshot(store.binding)
        assert replay.balance == after.balance and replay.budget == after.budget
        assert store._cold_unarmed and not recovery._armed and not values[3].sent and not values[4].sent
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet', 'cash_receipt', 'cycle_receipt'])
async def test_new_money_checkpoint_crash_preserves_original_pending_inventory_cycle(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, owner, facts, flow, command, _, _ = await pending_cycle(tmp_path, monkeypatch, stage='after')
    target, name = {'wallet': (store._ledger, 'apply_joint_snapshot'), 'cash_receipt': (store, '_publish_joint_cash'),
                    'cycle_receipt': (store, '_publish_settlement')}[stage]
    original = getattr(target, name)

    def crash(*args, **kwargs):
        if stage == 'cash_receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage == 'cycle_receipt' and not args[0].startswith('settle-done:'):
            return original(*args, **kwargs)
        if stage == 'wallet':
            original(*args, **kwargs)
        raise OSError('synthetic joint cash/cycle interruption')

    try:
        with monkeypatch.context() as patches:
            patches.setattr(target, name, crash)
            assert not await repo.reconcile(store.binding, owner, spot_facts=facts, spot_cashflows=(flow,))
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert (await store.snapshot(store.binding)).budget.session_subsidy_committed_usdt == D('1')
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert after.budget.session_subsidy_committed_usdt == after.budget.session_execution_loss_usdt == D('.24')
        assert (await store.lookup_inventory_settlement(command.operation_id, store.binding)).state == 'SETTLED'
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before', 'after', 'receipt'])
@pytest.mark.parametrize('activity', ['fill', 'swap_fee'])
async def test_cycle_completion_accounts_new_spot_or_swap_fill_without_rewriting_cycle_members(tmp_path, monkeypatch, stage, activity):
    repo, store, recovery, values, owner, facts, flow, command, _, fills = await pending_cycle(
        tmp_path, monkeypatch, stage=stage, activity=activity)
    try:
        assert await repo.reconcile(store.binding, owner, spot_facts=facts, spot_fills=fills, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert after.budget.session_execution_loss_usdt == (D('.36') if fills else D('.241'))
        assert after.budget.session_subsidy_committed_usdt == (D('.74') if fills else D('.24'))
        assert after.balance.reserved_quote_usdt == (D('1.08') if fills else 0)
        row = (await store.read(store.binding)).resources['settlement_operations'][command.operation_id]
        assert thaw(row['command']) == command.json() and row['state'] == 'SETTLED'
        assert values[1].claim_ids() == (('other',) if fills else ())
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        replay = await store.snapshot(store.binding)
        assert replay.balance == after.balance and replay.budget == after.budget
        assert store._cold_unarmed and not recovery._armed and values[3].sent == []
        assert len(values[4].sent) == (0 if fills else 1)
    finally:
        await store.close()
