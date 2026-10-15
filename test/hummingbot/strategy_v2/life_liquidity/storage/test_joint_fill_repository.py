"""Native joint source proof plus independent fill/account facts on one worker."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryTrade
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def scenario(tmp_path):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    assert await repo.reconcile(store.binding, owner)
    claim = CapitalClaim('intent1', 'wire1', store.binding.account_uid, str(owner.session_id), owner.epoch,
                         1, 1, 'SPOT', 'BUY', 'OPEN', D('2'), D('1'), 1)
    assert (await store._call(values[1].reserve, claim)).allowed
    await store.prepare(order(owner, fee_buffer_quote_usdt=D('.1')),
                        cut=(await store.snapshot(store.binding)).cut, owner=owner)
    await store.arm('operation1', cut=(await store.snapshot(store.binding)).cut, owner=owner)
    swap_claim = await store._call(send_close, values)
    swap_proof = report(values, bundle, swap_claim)
    at = recovery.clock_ms()
    proof = RecoveryOrder(claim, '123456789', 'partially_filled',
                          (RecoveryTrade('spot-trade', D('1'), D('1'), 'USDT', D('-.02'), D('0'), at),))
    values[8]['snapshot'] = replace(values[8]['snapshot'], spot_life_base=D('11'), spot_cash_quote=D('8.979'),
                                    pending=(claim,))
    bundle['value'] = replace(bundle['value'], orders=(swap_proof, proof), pending_wire_ids=(claim.wire_id,))
    fill = SpotFillObservation('spot-trade', D('1'), D('1'), 'USDT', D('-.02'), at,
                               D('.9'), at, at, 'independent_market')
    row = FillSpot('joint-spot-fill', 'operation1', claim.intent_id, claim.wire_id, 'LIFE-USDT', 'BUY',
                   claim.session_id, claim.epoch, '123456789', D('1'), (fill,), values[6].wall)
    facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('11'), D('8.979'), D('0'), D('0'),
                                       ('spot-trade',), (), (claim.wire_id,), (), True, True, True)
    return repo, store, recovery, values, bundle, owner, proof, row, facts


@pytest.mark.asyncio
async def test_partial_then_terminal_couples_actual_wallet_budgets_and_qualified_release(tmp_path):
    repo, store, recovery, values, bundle, owner, proof, row, facts = await scenario(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_base_life == D('11') and view.balance.total_quote_usdt == D('8.979')
        assert view.budget.session_execution_loss_usdt == D('.121')
        assert values[1].claim_ids() == ('intent1',)
        assert store._wal.get('intent1').state == 'SEND_UNKNOWN'
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, pending=())
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3,
                                  orders=(bundle['value'].orders[0], replace(proof, state='canceled')),
                                  pending_wire_ids=())
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert values[1].claim_ids() == ()
        assert store._wal.get('intent1').state == 'TERMINAL'
        assert (await store.snapshot(store.binding)).balance.total_quote_usdt == D('8.979')
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert not recovery._armed and store._cold_unarmed
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_reference', 'missing_account', 'debt', 'uid', 'scope', 'fee', 'runner'])
async def test_missing_or_conflicting_sources_preserve_wallet_budgets_and_claims(tmp_path, fault):
    repo, store, recovery, values, _, owner, _, row, facts = await scenario(tmp_path)
    try:
        before = await store.snapshot(store.binding)
        fills = (row,)
        if fault == 'missing_reference':
            fills = ()
        elif fault == 'missing_account':
            facts = None
        elif fault == 'debt':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'uid':
            facts = replace(facts, binding=replace(facts.binding, account_uid='99'))
        elif fault == 'scope':
            facts = replace(facts, bill_scope_complete=False)
        elif fault == 'fee':
            fills = (replace(row, fills=(replace(row.fills[0], signed_fee=D('-.03')),)),)
        else:
            facts = replace(facts, runner_wire_ids=('foreign',))
        claims = values[1].claim_ids()
        assert not await repo.reconcile(store.binding, owner, spot_fills=fills, spot_facts=facts)
        after = await store.snapshot(store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert values[1].claim_ids() == claims
        assert recovery.journal._state['phase'] == 'COMMITTED'
        assert not recovery._healthy
    finally:
        await store.close()


def terminal(values, bundle, proof):
    values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, pending=())
    values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3)
    bundle['value'] = replace(bundle['value'], snapshot_sequence=3,
                              orders=(bundle['value'].orders[0], replace(proof, state='canceled')),
                              pending_wire_ids=())


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet', 'loss', 'floor', 'cash_receipt', 'terminal_wallet',
                                   'terminal_receipt', 'capital', 'native_receipt'])
async def test_cold_native_batch_repairs_every_financial_release_cut_without_resend(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, bundle, owner, proof, row, facts = await scenario(tmp_path)
    terminal(values, bundle, proof)
    try:
        target, method = {
            'wallet': (store._ledger, 'apply_joint_snapshot'),
            'loss': (store._loss, 'record'),
            'floor': (store._subsidy, 'record_fill_floor'),
            'cash_receipt': (store, '_publish_joint_cash'),
            'terminal_wallet': (store._ledger, 'confirm_terminal'),
            'terminal_receipt': (store, '_publish_terminal'),
            'capital': (values[1], 'settle'),
            'native_receipt': (recovery.journal, 'commit'),
        }[stage]
        original = getattr(target, method)

        def interrupted(*args, **kwargs):
            if stage == 'cash_receipt' and not args[0].startswith('joint-done:'):
                return original(*args, **kwargs)
            if stage == 'terminal_receipt' and not args[0].startswith('term-done:'):
                return original(*args, **kwargs)
            if stage == 'native_receipt' and (args[0]['phase'] != 'COMMITTED' or not args[0]['bundle']['orders']):
                return original(*args, **kwargs)
            if stage not in ('cash_receipt', 'terminal_receipt', 'native_receipt'):
                original(*args, **kwargs)
            raise OSError('synthetic lost checkpoint')

        with monkeypatch.context() as patches:
            patches.setattr(target, method, interrupted)
            assert not await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('8.979') and view.balance.total_base_life == D('11')
        assert view.budget.session_execution_loss_usdt == D('.121')
        assert view.budget.session_subsidy_committed_usdt == D('.12')
        assert values[1].claim_ids() == ()
        assert len(values[4].sent) == 1 and values[3].sent == []
        assert store._cold_unarmed and not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_activity_after_unknown_batch_keeps_original_fill_value_and_repairs_then_extends(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, owner, proof, row, facts = await scenario(tmp_path)
    try:
        def interrupted(*args, **kwargs):
            raise OSError('synthetic pre-wallet failure')
        with monkeypatch.context() as patches:
            patches.setattr(store._ledger, 'apply_joint_snapshot', interrupted)
            assert not await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts)
        values[6].advance(.1)
        at = recovery.clock_ms()
        new_trade = RecoveryTrade('spot-next', D('1'), D('1'), 'USDT', D('-.02'), D('0'), at)
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, observed_at_ms=at, pending=(),
                                        spot_life_base=D('12'), spot_cash_quote=D('7.959'))
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3, observed_at_ms=at)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3, observed_at_ms=at,
                                  orders=(bundle['value'].orders[0], replace(proof, state='filled',
                                                                             trades=proof.trades + (new_trade,))),
                                  pending_wire_ids=())
        fill = SpotFillObservation('spot-next', D('1'), D('1'), 'USDT', D('-.02'), at,
                                   D('.9'), at, at, 'independent_market')
        row = replace(row, operation_id='joint-next', observed_at=values[6].wall,
                      cumulative_base_life=D('2'), fills=row.fills + (fill,))
        facts = replace(facts, observed_at=values[6].wall, account_base_life=D('12'), account_quote_usdt=D('7.959'),
                        account_trade_ids=('spot-trade', 'spot-next'))
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('7.959') and view.balance.total_base_life == D('12')
        assert view.budget.session_execution_loss_usdt == D('.241')
        assert view.budget.session_subsidy_committed_usdt == D('.24')
        assert values[1].claim_ids() == () and len(values[4].sent) == 1
        assert not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_spot_terminal_orders_share_one_qualified_account_cut(tmp_path):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        assert await repo.reconcile(store.binding, owner)
        claims = tuple(CapitalClaim(f'intent{i}', f'wire{i}', store.binding.account_uid, str(owner.session_id),
                                    owner.epoch, 1, 1, 'SPOT', 'BUY', 'OPEN', D('2'), D('1'), 1) for i in (1, 2))
        for i, claim in enumerate(claims, 1):
            assert (await store._call(values[1].reserve, claim)).allowed
            prepared = order(owner, operation_id=f'operation{i}', intent_id=claim.intent_id,
                             client_order_id=claim.wire_id, quote_level=i, fee_buffer_quote_usdt=D('.1'))
            await store.prepare(prepared, cut=(await store.snapshot(store.binding)).cut, owner=owner)
            await store.arm(prepared.operation_id, cut=(await store.snapshot(store.binding)).cut, owner=owner)
            if i == 1:
                ack = AckSpot('ack1', prepared.operation_id, claim.intent_id, claim.wire_id, 'LIFE-USDT',
                              claim.side, claim.session_id, claim.epoch, '101', values[6].wall)
                await store.acknowledge(ack, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        swap_claim = await store._call(send_close, values)
        swap_proof = report(values, bundle, swap_claim)
        at = recovery.clock_ms()
        orders, fills = [], []
        for i, claim in enumerate(claims, 1):
            trade = RecoveryTrade(f'spot{i}', D('1'), D('1'), 'USDT', D('-.02'), D('0'), at)
            orders.append(RecoveryOrder(claim, str(100 + i), 'canceled', (trade,)))
            fill = SpotFillObservation(trade.trade_id, D('1'), D('1'), 'USDT', D('-.02'), at,
                                       D('.9'), at, at, 'independent_market')
            fills.append(FillSpot(f'fill{i}', f'operation{i}', claim.intent_id, claim.wire_id, 'LIFE-USDT', 'BUY',
                                  claim.session_id, claim.epoch, str(100 + i), D('1'), (fill,), values[6].wall))
        values[8]['snapshot'] = replace(values[8]['snapshot'], spot_life_base=D('12'), spot_cash_quote=D('7.959'), pending=())
        bundle['value'] = replace(bundle['value'], orders=(swap_proof, *orders), pending_wire_ids=())
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('12'), D('7.959'), D('0'), D('0'),
                                           ('spot1', 'spot2'), (), ('wire1', 'wire2'), (), True, True, True)
        assert await repo.reconcile(store.binding, owner, spot_fills=tuple(fills), spot_facts=facts), recovery.reason_code
        assert values[1].claim_ids() == ()
        view = await store.snapshot(store.binding)
        assert view.balance.total_base_life == D('12') and view.balance.total_quote_usdt == D('7.959')
        assert view.budget.session_execution_loss_usdt == D('.241')
        assert view.budget.session_subsidy_committed_usdt == D('.24')
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_zero_fill_terminal_releases_only_proven_unused_spot_subsidy(tmp_path):
    repo, store, recovery, values, bundle, owner, proof, _, facts = await scenario(tmp_path)
    try:
        proof = replace(proof, state='canceled', trades=())
        values[8]['snapshot'] = replace(values[8]['snapshot'], spot_life_base=D('10'), spot_cash_quote=D('9.999'), pending=())
        bundle['value'] = replace(bundle['value'], orders=(bundle['value'].orders[0], proof), pending_wire_ids=())
        facts = replace(facts, account_base_life=D('10'), account_quote_usdt=D('9.999'), account_trade_ids=())
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.budget.session_subsidy_committed_usdt == 0
        assert view.budget.session_execution_loss_usdt == D('.001')
        assert view.balance.total_quote_usdt == D('9.999')
        assert values[1].claim_ids() == ()
        assert store._wal.get('intent1').state == 'TERMINAL'
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_qualified_unsent_spot_preparation_releases_only_original_zero_fill_hold(tmp_path):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = CapitalClaim('intent1', 'wire1', store.binding.account_uid, str(owner.session_id), owner.epoch,
                             1, 1, 'SPOT', 'BUY', 'OPEN', D('2'), D('1'), 1)
        assert (await store._call(values[1].reserve, claim)).allowed
        await store.prepare(order(owner), cut=(await store.snapshot(store.binding)).cut, owner=owner)
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, orders=(RecoveryOrder(claim, None, 'unsent', ()),))
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert store._wal.get('intent1').state == 'ABORTED_BEFORE_SEND'
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == 0 and view.budget.session_subsidy_committed_usdt == 0
        assert view.balance.total_base_life == view.balance.total_quote_usdt == D('10')
        assert values[1].claim_ids() == () and values[3].sent == values[4].sent == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'principal', 'subsidy', 'receipt'])
async def test_cold_joint_unsent_abort_repairs_original_hold_without_rearm(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = CapitalClaim('intent1', 'wire1', store.binding.account_uid, str(owner.session_id), owner.epoch,
                             1, 1, 'SPOT', 'BUY', 'OPEN', D('2'), D('1'), 1)
        assert (await store._call(values[1].reserve, claim)).allowed
        await store.prepare(order(owner), cut=(await store.snapshot(store.binding)).cut, owner=owner)
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, orders=(RecoveryOrder(claim, None, 'unsent', ()),))
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        target, method = {'wal': (store._wal, 'abort_before_send'), 'principal': (store._ledger, 'abort_unsent'),
                          'subsidy': (store._subsidy, 'release_unsent'), 'receipt': (store, '_publish_lifecycle')}[stage]
        original = getattr(target, method)

        def interrupted(*args, **kwargs):
            if stage == 'receipt' and not args[0].startswith('lifecycle-done:'):
                return original(*args, **kwargs)
            if stage != 'receipt':
                original(*args, **kwargs)
            raise OSError('synthetic unsent interruption')

        with monkeypatch.context() as patches:
            patches.setattr(target, method, interrupted)
            assert not await repo.reconcile(store.binding, owner, spot_facts=facts)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_base_life == view.balance.total_quote_usdt == D('10')
        assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
        assert store._wal.get('intent1').state == 'ABORTED_BEFORE_SEND'
        assert values[1].claim_ids() == () and store._cold_unarmed and not recovery._armed
        assert values[3].sent == values[4].sent == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet', 'loss', 'floor', 'receipt'])
async def test_pending_spot_fill_and_new_swap_settlement_finish_one_account_cut(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, bundle, owner, _, row, facts = await scenario(tmp_path)
    try:
        target, method = {'wallet': (store._ledger, 'apply_fills_snapshot'), 'loss': (store._loss, 'record'),
                          'floor': (store._subsidy, 'record_fill_floor'), 'receipt': (store, '_publish_fills')}[stage]
        original = getattr(target, method)

        def interrupted(*args, **kwargs):
            if stage == 'receipt' and not args[0].startswith('fill-done:'):
                return original(*args, **kwargs)
            if stage != 'receipt':
                original(*args, **kwargs)
            raise OSError('synthetic pending fill')
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
        with monkeypatch.context() as patches:
            patches.setattr(target, method, interrupted)
            with pytest.raises(CommitUnknown):
                await store.apply_fills(row, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_base_life == D('11') and view.balance.total_quote_usdt == D('8.979')
        assert view.budget.session_execution_loss_usdt == D('.121')
        assert (await store.lookup_fill(row.operation_id, store.binding)).state == 'APPLIED'
        assert store._wal.get(row.intent_id).state == 'SEND_UNKNOWN'
        assert values[1].claim_ids() == ('intent1',)
        assert store._cold_unarmed and len(values[4].sent) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('written', [False, True])
async def test_pending_ack_with_new_spot_fill_and_swap_cash_preserves_original_ack(tmp_path, monkeypatch, written):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    repo, store, recovery, values, bundle, owner, _, row, facts = await scenario(tmp_path)
    try:
        command = AckSpot('original-ack', row.prepare_operation_id, row.intent_id, row.client_order_id, row.market,
                          row.side, row.session_id, row.epoch, row.exchange_order_id, row.observed_at)
        original = store._wal.acknowledge

        def interrupted(*args, **kwargs):
            if written:
                original(*args, **kwargs)
            raise OSError('synthetic missing ACK receipt')
        with monkeypatch.context() as patches:
            patches.setattr(store._wal, 'acknowledge', interrupted)
            with pytest.raises(CommitUnknown):
                await store.acknowledge(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('8.979') and view.balance.total_base_life == D('11')
        assert view.budget.session_execution_loss_usdt == D('.121')
        assert (await store.lookup_ack(command.operation_id, store.binding)).state == 'ACKED'
        assert store._wal.get(row.intent_id).state == 'ACKED'
        assert values[1].claim_ids() == ('intent1',) and store._cold_unarmed
    finally:
        await store.close()
