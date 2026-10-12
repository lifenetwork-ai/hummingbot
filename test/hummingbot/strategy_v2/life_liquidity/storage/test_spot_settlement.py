"""Closed inventory cycles settle subsidy, never replenish execution loss."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import facts as account_facts
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
    ACCOUNTING,
    BUDGET,
    CAPACITY,
    NOW,
    POLICY,
    RISK,
    TERMINAL_POLICY,
    create_fixture,
    facts as terminal_facts,
    fill,
    fill_command,
    terminal,
)
from unittest.mock import patch

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory, SpotSettlementPolicy

SETTLEMENT = SpotSettlementPolicy(1000)  # Explicit synthetic freshness.


async def open_it(files, binding, path, *, owner=None, create=False):
    return await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                  subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                  account_lock_root=path / 'account-locks', owner=owner, create_binding=create,
                                  clock=lambda: NOW, fill_policy=POLICY, accounting_policy=ACCOUNTING,
                                  terminal_policy=TERMINAL_POLICY, settlement_policy=SETTLEMENT)


@pytest_asyncio.fixture
async def terminal_bundle(tmp_path):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner=owner, create=True)
    try:
        yield store, files, binding, owner, [NOW]
    finally:
        await store.close()


async def closed_cycle(store, binding, owner, *, sell_price=D('1'), fee_currency='USDT', sell_quantity=D('1'),
                       sell_reference=D('.9')):
    rows = []
    for i, side in enumerate(('BUY', 'SELL'), 1):
        suffix = str(i)
        price = D('1') if i == 1 else sell_price
        cmd = order(owner, operation_id='operation' + suffix, intent_id='intent' + suffix,
                    client_order_id='wire' + suffix, side=side, limit_price_usdt=price,
                    subsidy_quote_usdt=D('.5'), fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.1'))
        await store.prepare(cmd, cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.arm(cmd.operation_id, cut=(await store.snapshot(binding)).cut, owner=owner)
        row = fill(trade_id='trade' + suffix, quantity_base_life=D('1') if i == 1 else sell_quantity,
                   price_usdt=price, fee_currency=fee_currency,
                   independent_price_usdt=D('.9') if i == 1 else sell_reference,
                   signed_fee=D('-.02') if fee_currency == 'USDT' else D('-.01') if i == 1 else D('0'))
        rows.append(row)
        await store.apply_fills(fill_command(owner, operation_id='fill' + suffix,
                                             prepare_operation_id=cmd.operation_id, intent_id=cmd.intent_id,
                                             client_order_id=cmd.client_order_id, side=side,
                                             exchange_order_id=str(123456788 + i), cumulative_base_life=row.quantity_base_life,
                                             fills=(row,)), cut=(await store.snapshot(binding)).cut, owner=owner)
        view = await store.snapshot(binding)
        evidence = terminal_facts(binding, limit_price_usdt=price, cumulative_base_life=row.quantity_base_life,
                                  fills=(row,), account_trade_ids=tuple(r.trade_id for r in rows),
                                  account_base_life=view.balance.total_base_life,
                                  account_quote_usdt=view.balance.total_quote_usdt, runner_wire_ids=())
        await store.confirm_terminal(terminal(binding, owner, evidence, operation_id='terminal' + suffix,
                                              prepare_operation_id=cmd.operation_id, intent_id=cmd.intent_id,
                                              client_order_id=cmd.client_order_id, side=side,
                                              exchange_order_id=str(123456788 + i)), cut=view.cut, owner=owner)


async def evidence(store, binding):
    view = await store.snapshot(binding)
    return account_facts(binding, account_base_life=view.balance.total_base_life,
                         account_quote_usdt=view.balance.total_quote_usdt,
                         account_trade_ids=('trade1', 'trade2'), accepted_wire_ids=(), runner_wire_ids=())


def command(**changes):
    return replace(SettleSpotInventory('settle1', 'cycle1', ('intent1', 'intent2')), **changes)


@pytest.mark.asyncio
async def test_flat_cycle_settlement_releases_only_unused_subsidy_and_replays_without_loss_credit(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    before = await store.snapshot(binding)
    result = await store.settle_inventory(command(), facts=await evidence(store, binding), cut=before.cut, owner=owner)
    after = await store.snapshot(binding)
    assert result.state == 'SETTLED' and not result.replayed
    assert dict(result.actuals_usdt) == {'intent1': D('.12'), 'intent2': D('0')}
    assert after.budget.session_subsidy_committed_usdt == D('.12')
    assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt == D('.12')
    assert after.balance == before.balance
    assert (await store.settle_inventory(command(), facts=await evidence(store, binding), cut=after.cut, owner=owner)).replayed
    assert (await store.snapshot(binding)).cut == after.cut


@pytest.mark.asyncio
@pytest.mark.parametrize('fee_currency,sell_quantity,allowed', [
    ('USDT', D('.9'), False), ('LIFE', D('1'), False), ('LIFE', D('.99'), True)])
async def test_cycle_requires_physically_flat_base_including_life_fees(terminal_bundle, fee_currency, sell_quantity, allowed):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner, fee_currency=fee_currency, sell_quantity=sell_quantity)
    before = await store.snapshot(binding)
    if allowed:
        await store.settle_inventory(command(), facts=await evidence(store, binding), cut=before.cut, owner=owner)
    else:
        with pytest.raises(StorageError):
            await store.settle_inventory(command(), facts=await evidence(store, binding), cut=before.cut, owner=owner)
        assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_incomplete_account_or_changed_member_operation_cannot_settle(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    before = await store.snapshot(binding)
    qualified = await evidence(store, binding)
    for cmd, facts in ((command(intent_ids=('intent1',)), qualified),
                       (command(), replace(qualified, account_trade_ids=())),
                       (command(), replace(qualified, order_scope_complete=False))):
        with pytest.raises(StorageError):
            await store.settle_inventory(cmd, facts=facts, cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_native_noop_keeps_settlement_pending_and_loss_spending(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    before = await store.snapshot(binding)
    with patch.object(SubsidyBudgetLedger, '_settle_inventory_cycle', return_value=False):
        with pytest.raises(CommitUnknown):
            await store.settle_inventory(command(), facts=await evidence(store, binding), cut=before.cut, owner=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['native', 'receipt'])
async def test_cold_resume_retains_pending_budget_overlay_and_never_rearms(terminal_bundle, tmp_path, stage):
    store, files, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    before = await store.snapshot(binding)
    qualified = await evidence(store, binding)
    if stage == 'native':
        context = patch.object(SubsidyBudgetLedger, '_settle_inventory_cycle', side_effect=OSError('synthetic checkpoint'))
    else:
        original = type(store)._publish_settlement

        def fail(self, key, *args):
            if key == 'settle-done:settle1':
                raise OSError('synthetic missing receipt')
            return original(self, key, *args)
        context = patch.object(type(store), '_publish_settlement', new=fail)
    with context:
        with pytest.raises(CommitUnknown):
            await store.settle_inventory(command(), facts=qualified, cut=before.cut, owner=owner)
    await store.close()
    cold = await open_it(files, binding, tmp_path)
    try:
        pending = await cold.snapshot(binding)
        assert pending.budget == before.budget and pending.recovery_required
        assert (await cold.lookup_inventory_settlement('settle1', binding)).state == 'RECOVERY_REQUIRED'
        await cold.resume_inventory_settlement(command(), facts=qualified, cut=pending.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.budget.session_subsidy_committed_usdt == D('.12') and after.recovery_required
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt
        assert (await cold.resume_inventory_settlement(command(), facts=qualified, cut=after.cut, owner=owner)).replayed
        assert (await cold.snapshot(binding)).cut == after.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'account_base_life': D('6')}, {'account_quote_usdt': D('12')}, {'account_cashflow_ids': ('999',)},
    {'liability_quote_usdt': D('.1')}, {'runner_wire_ids': ('foreign',)}, {'bill_scope_complete': False},
    {'observed_at': NOW - timedelta(milliseconds=1001)}, {'observed_at': NOW + timedelta(milliseconds=1)},
])
async def test_bad_account_scope_freshness_or_liability_cannot_settle(terminal_bundle, changes):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    before = await store.snapshot(binding)
    with pytest.raises(StorageError):
        await store.settle_inventory(command(), facts=replace(await evidence(store, binding), **changes), cut=before.cut, owner=owner)
    assert (await store.snapshot(binding)).cut == before.cut


@pytest.mark.asyncio
async def test_settlement_conflicts_permanent_members_and_requires_explicit_bound_policy(terminal_bundle, tmp_path):
    store, files, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner)
    qualified = await evidence(store, binding)
    await store.settle_inventory(command(), facts=qualified, cut=(await store.snapshot(binding)).cut, owner=owner)
    before = await store.snapshot(binding)
    for cmd, token in ((command(cycle_id='changed'), owner), (command(), replace(owner, epoch=2)),
                       (command(operation_id='new', cycle_id='new'), owner)):
        with pytest.raises(StorageError):
            await store.settle_inventory(cmd, facts=qualified, cut=before.cut, owner=token)
    assert (await store.snapshot(binding)).cut == before.cut
    await store.close()
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable
    with pytest.raises(StorageUnavailable):
        await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                               subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                               account_lock_root=tmp_path / 'account-locks', clock=lambda: NOW,
                               fill_policy=POLICY, accounting_policy=ACCOUNTING, terminal_policy=TERMINAL_POLICY)


@pytest.mark.parametrize('members', [(), ('intent1', 'intent1'), ['intent1']])
def test_invalid_settlement_members(members):
    with pytest.raises(StorageError):
        command(intent_ids=members)


@pytest.mark.asyncio
async def test_realized_inventory_extra_charges_closing_fill_without_loss_budget_reset(terminal_bundle):
    store, _, binding, owner, _ = terminal_bundle
    await closed_cycle(store, binding, owner, sell_price=D('.9'), sell_reference=D('.8'))
    before = await store.snapshot(binding)
    result = await store.settle_inventory(command(), facts=await evidence(store, binding), cut=before.cut, owner=owner)
    assert dict(result.actuals_usdt) == {'intent1': D('.12'), 'intent2': D('.02')}
    after = await store.snapshot(binding)
    assert after.budget.session_subsidy_committed_usdt == D('.14')
    assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt == D('.12')


@pytest.mark.parametrize('stage', ['native', 'receipt'])
def test_killed_settlement_reopens_pending_overlay_or_historical_receipt(tmp_path, stage):
    import asyncio
    import subprocess
    import sys
    from pathlib import Path

    files, binding, owner = create_fixture(tmp_path)

    child = '''
import asyncio,os,sys
from pathlib import Path
from uuid import UUID
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_settlement import open_it,command,evidence,closed_cycle,NOW
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
async def main():
    p=Path(sys.argv[1])
    files=LegacySpotFiles(*(p/name for name in ('binding.json','session.json','wal.json','reservations.json','subsidy.json','loss.json')))
    session=SessionStore(files.session).load().primary
    binding=StateBinding('OFFLINE_SIMULATION','okx','12345','usdt',UUID(int=1),1,'a'*64,'b'*64)
    owner=OwnerToken(UUID(int=2),UUID(int=3),1,UUID(session.session_id),NOW,session.expires_at)
    store=await open_it(files,binding,p,owner=owner,create=True)
    await closed_cycle(store,binding,owner)
    if sys.argv[2]=='native':
        original=SubsidyBudgetLedger._settle_inventory_cycle
        def crash(self,*args,**kwargs):
            original(self,*args,**kwargs)
            os._exit(81)
        SubsidyBudgetLedger._settle_inventory_cycle=crash
    else:
        original=FileCheckpointStore._commit
        def crash(self,*args,**kwargs):
            result=original(self,*args,**kwargs)
            if args[2]=='settle-done:settle1':
                os._exit(81)
            return result
        FileCheckpointStore._commit=crash
    await store.settle_inventory(command(),facts=await evidence(store,binding),cut=(await store.snapshot(binding)).cut,owner=owner)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), stage],
                            cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=20)
    assert result.returncode == 81, result.stderr

    async def readback():
        store = await open_it(files, binding, tmp_path)
        try:
            before = await store.snapshot(binding)
            assert before.budget.session_subsidy_committed_usdt == (D('1') if stage == 'native' else D('.12'))
            result = await store.resume_inventory_settlement(command(), facts=await evidence(store, binding),
                                                             cut=before.cut, owner=owner)
            assert result.replayed is (stage == 'receipt')
            assert (await store.snapshot(binding)).budget.session_subsidy_committed_usdt == D('.12')
            assert (await store.snapshot(binding)).recovery_required
        finally:
            await store.close()
    asyncio.run(readback())
