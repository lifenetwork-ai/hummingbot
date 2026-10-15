"""An original interrupted terminal operation composes with later account cash."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_fill_repository import scenario
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


async def pending_terminal(tmp_path, monkeypatch, *, stage, currency='USDT', amount=D('2'), activity=None, aborted_history=False):
    at = int(setup_shared(tmp_path / 'clock-reference')[6].wall.timestamp() * 1000)
    approval = ApprovedSpotCashflow('101', currency, amount, at)
    repo, store, recovery, values, bundle, owner, proof, fill, facts = await scenario(
        tmp_path, approvals=(approval,), swap_terminal=activity != 'swap_fee')
    assert await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts), recovery.reason_code
    aborted_source = None
    if aborted_history:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

        from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder
        from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
        from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
        from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
        await store.acknowledge(AckSpot('history-original-ack', fill.prepare_operation_id, fill.intent_id, fill.client_order_id,
                                        fill.market, fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, facts.observed_at),
                                cut=(await store.snapshot(store.binding)).cut, owner=owner)
        aborted = replace(order(owner), operation_id='aborted-prepare', intent_id='aborted', client_order_id='abortedwire', quote_level=1)
        aborted_claim = CapitalClaim(aborted.intent_id, aborted.client_order_id, store.binding.account_uid, aborted.session_id,
                                     aborted.epoch, 1, 1, 'SPOT', 'BUY', 'OPEN', aborted.quantity_base, aborted.limit_price_usdt, 1)
        assert (await store._call(values[1].reserve, aborted_claim)).allowed
        await store.prepare(aborted, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.apply_lifecycle(SpotLifecycleCommand('original-abort', aborted.operation_id, aborted.intent_id,
                                                         aborted.client_order_id, 'ABORT_PREPARED'),
                                    cut=(await store.snapshot(store.binding)).cut, owner=owner)
        aborted_source = RecoveryOrder(aborted_claim, None, 'unsent', ())
    other = None
    if activity == 'fill':
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

        from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
        from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
        await store.acknowledge(AckSpot('original-ack', fill.prepare_operation_id, fill.intent_id, fill.client_order_id, fill.market,
                                        fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, facts.observed_at),
                                cut=(await store.snapshot(store.binding)).cut, owner=owner)
        other = replace(order(owner), operation_id='other-prepare', intent_id='other', client_order_id='otherwire', quote_level=1,
                        fee_buffer_quote_usdt=D('.1'))
        claim = CapitalClaim(other.intent_id, other.client_order_id, store.binding.account_uid, other.session_id, other.epoch,
                             1, 1, 'SPOT', 'BUY', 'OPEN', other.quantity_base, other.limit_price_usdt, 1)
        assert (await store._call(values[1].reserve, claim)).allowed
        await store.prepare(other, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.arm(other.operation_id, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.acknowledge(AckSpot('other-ack', other.operation_id, other.intent_id, other.client_order_id, other.market,
                                        other.side, other.session_id, other.epoch, '999', facts.observed_at),
                                cut=(await store.snapshot(store.binding)).cut, owner=owner)
    evidence = SpotTerminalFacts(store.binding, facts.observed_at, 'CANCELED', D('1'), D('2'), D('1'), fill.fills,
                                 facts.account_base_life, facts.account_quote_usdt, D('0'), D('0'),
                                 facts.account_trade_ids, (), () if other is None else ('otherwire',),
                                 () if other is None else ('999',), (), True, True, True)
    terminal = TerminalSpot('original-terminal', fill.prepare_operation_id, fill.intent_id, fill.client_order_id,
                            fill.market, fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, evidence)
    target, name = {
        'observation': (store._wal, 'mark_exchange_terminal_observed'),
        'principal': (store._ledger, 'confirm_terminal'),
        'wal': (store._wal, 'mark_terminal'),
        'receipt': (store, '_publish_terminal'),
    }[stage]
    original = getattr(target, name)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('term-done:'):
            return original(*args, **kwargs)
        if stage != 'receipt':
            original(*args, **kwargs)
        raise OSError('synthetic original terminal interruption')

    before = await store.snapshot(store.binding)
    with monkeypatch.context() as patches:
        patches.setattr(target, name, crash)
        with pytest.raises(CommitUnknown):
            await store.confirm_terminal(terminal, cut=(await store.snapshot(store.binding)).cut, owner=owner)
    base = facts.account_base_life + (amount if currency == 'LIFE' else 0)
    quote = facts.account_quote_usdt + (amount if currency == 'USDT' else 0)
    extra = ()
    orders = (bundle['value'].orders[0], replace(proof, state='canceled'))
    if aborted_source is not None:
        orders += (aborted_source,)
    payments = bundle['value'].payments
    if activity == 'fill':
        from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryTrade
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
        trade = RecoveryTrade('other-trade', D('1'), D('1'), 'USDT', D('-.02'), D('0'), at)
        orders += (RecoveryOrder(claim, '999', 'partially_filled', (trade,)),)
        observed = SpotFillObservation('other-trade', D('1'), D('1'), 'USDT', D('-.02'), at,
                                       D('.9'), at, at, 'independent_market')
        extra = (FillSpot('other-fill', other.operation_id, other.intent_id, other.client_order_id, other.market, other.side,
                          other.session_id, other.epoch, '999', D('1'), (observed,), facts.observed_at),)
        base += 1
        quote -= D('1.02')
    elif activity == 'funding':
        from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
        payments += (RecoveryPayment('202', D('-.1'), at),)
        quote -= D('.1')
    elif activity == 'swap_fee':
        from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryTrade
        values[6].advance(.001)
        swap = orders[0]
        trade = RecoveryTrade('swap-next', D('.25'), D('1'), 'USDT', D('-.002'), D('0'), at + 1)
        orders = (replace(swap, state='filled', trades=swap.trades + (trade,)), orders[1])
        values[8]['snapshot'] = replace(values[8]['snapshot'], long_base=D('.5'))
        values[7]['value'] = replace(values[7]['value'], net_contracts=D('2'))
        quote -= D('.002')
    now = recovery.clock_ms()
    values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, observed_at_ms=now,
                                    pending=() if other is None else (claim,), spot_life_base=base, spot_cash_quote=quote)
    values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3, observed_at_ms=now)
    bundle['value'] = replace(bundle['value'], snapshot_sequence=3, observed_at_ms=now,
                              pending_wire_ids=() if other is None else ('otherwire',),
                              orders=orders, payments=payments)
    flow = CashflowSpot('later-flow', '101', currency, amount, at, D('1') if currency == 'USDT' else D('1.2'), at, at,
                        'quote_unit' if currency == 'USDT' else 'independent_market', facts.observed_at)
    facts = replace(facts, observed_at=values[6].wall, account_base_life=base, account_quote_usdt=quote, account_cashflow_ids=('101',),
                    account_trade_ids=facts.account_trade_ids + (() if other is None else ('other-trade',)),
                    accepted_wire_ids=facts.accepted_wire_ids + (() if other is None else ('otherwire',)))
    repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
    assert (await store.snapshot(store.binding)).balance.reserved_quote_usdt == (D('3.18') if other is not None else D('1.08'))
    return repo, store, recovery, values, bundle, owner, fill, facts, terminal, flow, before, extra


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['observation', 'principal', 'wal', 'receipt'])
@pytest.mark.parametrize('currency,amount', [('USDT', D('2')), ('USDT', D('-2')), ('LIFE', D('2')), ('LIFE', D('-2'))])
async def test_original_terminal_and_new_transfer_preserve_identity_cost_and_cold_replay(tmp_path, monkeypatch, stage, currency, amount):
    repo, store, recovery, values, _, owner, _, facts, original, flow, before, _ = await pending_terminal(
        tmp_path, monkeypatch, stage=stage, currency=currency, amount=amount)
    try:
        assert await repo.reconcile(store.binding, owner, spot_facts=facts, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert after.balance.reserved_quote_usdt == 0
        assert after.budget == before.budget  # Terminal of a filled quote retains its economic floor/hold.
        row = thaw((await store.read(store.binding)).resources['terminal_operations'][original.operation_id])
        assert row['command'] == original.json() and row['state'] == 'TERMINAL'
        assert row['fingerprint'] == fingerprint({'command': original.json(), 'owner': owner.json()})
        assert values[1].claim_ids() == ()
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        replay = await store.snapshot(store.binding)
        assert replay.balance == after.balance and replay.budget == after.budget
        assert store._cold_unarmed and not recovery._armed and not values[2]._runtime_enabled
        assert values[3].sent == [] and len(values[4].sent) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['observation', 'principal', 'wal', 'receipt'])
@pytest.mark.parametrize('activity', ['fill', 'funding', 'swap_fee'])
async def test_terminal_receipt_and_new_fill_or_funding_account_before_original_release(tmp_path, monkeypatch, stage, activity):
    repo, store, recovery, values, _, owner, _, facts, original, flow, before, fills = await pending_terminal(
        tmp_path, monkeypatch, stage=stage, activity=activity)
    try:
        assert await repo.reconcile(store.binding, owner, spot_fills=fills, spot_facts=facts, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        extra_loss = {'fill': D('.12'), 'funding': D('0'), 'swap_fee': D('.002')}[activity]
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt + extra_loss
        assert after.balance.reserved_quote_usdt == (D('1.08') if fills else 0)
        assert values[1].claim_ids() == (('other',) if fills else ())
        assert (await store.lookup_terminal(original.operation_id, store.binding)).state == 'TERMINAL'
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert (await store.snapshot(store.binding)).balance == after.balance
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['live', 'changed_fill', 'debt', 'bill_scope', 'missing_transfer', 'wrong_exchange', 'wrong_owner',
                                   'unrelated_loss', 'unrelated_subsidy'])
async def test_unqualified_terminal_composition_never_books_money_or_releases_original_claim(tmp_path, monkeypatch, fault):
    repo, store, recovery, values, bundle, owner, _, facts, original, flow, _, fills = await pending_terminal(
        tmp_path, monkeypatch, stage='principal', activity='fill' if fault == 'unrelated_subsidy' else None)
    try:
        if fault == 'unrelated_loss':
            await store._call(lambda: store._loss.record('unapproved-debit', D('1'),
                                                         session_id=original.session_id, at_utc=facts.observed_at))
        elif fault == 'unrelated_subsidy':
            await store._call(lambda: store._subsidy._reconcile('other', actual_cost_quote=D('0'), release_proven=True))
        before = await store.snapshot(store.binding)
        current = bundle['value'].orders[-1]
        if fault == 'live':
            bundle['value'] = replace(bundle['value'], orders=(*bundle['value'].orders[:-1], replace(current, state='partially_filled')),
                                      pending_wire_ids=(current.claim.wire_id,))
            values[8]['snapshot'] = replace(values[8]['snapshot'], pending=(current.claim,))
        elif fault == 'changed_fill':
            bundle['value'] = replace(bundle['value'], orders=(*bundle['value'].orders[:-1],
                                                               replace(current, trades=(replace(current.trades[0], signed_fee=D('-.03')),))))
        elif fault == 'debt':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'bill_scope':
            facts = replace(facts, bill_scope_complete=False)
        elif fault == 'wrong_exchange':
            bundle['value'] = replace(bundle['value'], orders=(*bundle['value'].orders[:-1], replace(current, exchange_order_id='999')))
        elif fault == 'wrong_owner':
            with pytest.raises(StorageError, match='JOINT_OWNER_MISMATCH'):
                await repo.reconcile(store.binding, replace(owner, epoch=owner.epoch + 1), spot_facts=facts, spot_cashflows=(flow,))
            return
        assert not await repo.reconcile(store.binding, owner, spot_facts=facts, spot_fills=fills,
                                        spot_cashflows=() if fault == 'missing_transfer' else (flow,))
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert values[1].claim_ids() == (('intent1', 'other') if fills else ('intent1',))
        assert thaw((await store.read(store.binding)).resources['terminal_operations'][original.operation_id])['state'] == 'TERMINATING'
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'financial_receipt', 'terminal_receipt'])
@pytest.mark.parametrize('new_activity', [False, True])
async def test_joint_money_and_terminal_crash_replays_original_values_with_optional_newer_funding(tmp_path, monkeypatch, stage, new_activity):
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
    repo, store, recovery, values, bundle, owner, _, facts, original, flow, _, fills = await pending_terminal(
        tmp_path, monkeypatch, stage='principal', activity='fill')
    target, name = (store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet') else (
        (store, '_publish_joint_cash') if stage == 'financial_receipt' else (store, '_publish_terminal'))
    native = getattr(target, name)

    def crash(*args, **kwargs):
        if stage == 'financial_receipt' and not args[0].startswith('joint-done:'):
            return native(*args, **kwargs)
        if stage == 'terminal_receipt' and not args[0].startswith('term-done:'):
            return native(*args, **kwargs)
        if stage == 'wallet_after':
            native(*args, **kwargs)
        raise OSError('synthetic joint money/terminal lost reply')

    try:
        with monkeypatch.context() as patches:
            patches.setattr(target, name, crash)
            assert not await repo.reconcile(store.binding, owner, spot_fills=fills, spot_facts=facts, spot_cashflows=(flow,))
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        held = await store.snapshot(store.binding)
        assert held.balance.reserved_quote_usdt >= D('2.16')  # Original terminal overlay plus the other working order.
        if new_activity:
            at = recovery.clock_ms()
            facts = replace(facts, account_quote_usdt=facts.account_quote_usdt - D('.1'))
            values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=4, spot_cash_quote=facts.account_quote_usdt)
            values[7]['value'] = replace(values[7]['value'], snapshot_sequence=4)
            bundle['value'] = replace(bundle['value'], snapshot_sequence=4,
                                      payments=bundle['value'].payments + (RecoveryPayment('202', D('-.1'), at),))
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert after.balance.reserved_quote_usdt == D('1.08')
        assert after.budget.session_execution_loss_usdt == D('.241')
        assert (await store.lookup_terminal(original.operation_id, store.binding)).state == 'TERMINAL'
        assert values[1].claim_ids() == ('other',) and not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['spot_terminal_operations', 'spot_settlement_operations'])
async def test_pending_operation_ids_are_not_authority_or_permission_to_admit_new_work(tmp_path, monkeypatch, field):
    from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
    _, store, recovery, values, _, owner, _, facts, original, _, _, _ = await pending_terminal(
        tmp_path, monkeypatch, stage='principal')
    try:
        command = JointCashSpot('forged-proof', store.binding, 'a' * 64, facts.observed_at, (),
                                facts.account_base_life, facts.account_quote_usdt, True, True, 'okx_joint_reconciled',
                                **{field: (original.operation_id if field == 'spot_terminal_operations' else 'new-cycle',)})
        assert JointCashSpot.from_json(command.json()) == command
        legacy = replace(command, **{field: ()}).json()
        assert field not in legacy and JointCashSpot.from_json(legacy).json() == legacy
        before = await store.snapshot(store.binding)
        with pytest.raises(StorageError, match='JOINT_PENDING_RECOVERY_AUTHORITY_REQUIRED'):
            await store.apply_joint_cash(command, cut=before.cut, owner=owner)
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert values[1].claim_ids() == ('intent1',) and not recovery._armed
        assert (await store.lookup_terminal(original.operation_id, store.binding)).state == 'RECOVERY_REQUIRED'
        assert (await store.read(store.binding)).resources['terminal_operations'][original.operation_id]['state'] == 'TERMINATING'
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['observation', 'principal', 'wal', 'receipt'])
async def test_terminal_recovery_preserves_a_previously_aborted_unsent_order(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, _, owner, _, facts, original, flow, before, _ = await pending_terminal(
        tmp_path, monkeypatch, stage=stage, aborted_history=True)
    try:
        assert await repo.reconcile(store.binding, owner, spot_facts=facts, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert after.budget == before.budget and after.balance.reserved_quote_usdt == 0
        assert (await store.attempt('aborted', store.binding)).state.value == 'ABORTED_BEFORE_SEND'
        operations = (await store.read(store.binding)).resources['lifecycle_operations']
        assert tuple(operations) == ('original-abort',)
        assert operations['original-abort']['state'] == 'APPLIED'
        assert (await store.lookup_terminal(original.operation_id, store.binding)).state == 'TERMINAL'
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert (await store.snapshot(store.binding)).balance == after.balance
        assert store._cold_unarmed and not recovery._armed and not values[3].sent
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['prepare', 'cancel'])
async def test_original_pending_terminal_cannot_admit_an_unrelated_second_operation(tmp_path, monkeypatch, action):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

    from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
    _, store, _, _, _, owner, fill, _, _, _, _, _ = await pending_terminal(tmp_path, monkeypatch, stage='principal')
    try:
        def remove_only_cold_gate():
            store._cold_unarmed = False
            store._observation_generation = owner.generation
        await store._call(remove_only_cold_gate)
        before = await store.snapshot(store.binding)
        assert before.recovery_required
        if action == 'prepare':
            command = replace(order(owner), operation_id='new-prepare', intent_id='new', client_order_id='newwire', quote_level=1)
            with pytest.raises(StorageError, match='SPOT_OWNER_OR_RECOVERY_GATE'):
                await store.prepare(command, cut=before.cut, owner=owner)
        else:
            command = SpotLifecycleCommand('new-cancel', fill.prepare_operation_id, fill.intent_id, fill.client_order_id, 'REQUEST_CANCEL')
            with pytest.raises(StorageError, match='SPOT_ACK_OWNER_OR_RECOVERY_GATE'):
                await store.apply_lifecycle(command, cut=before.cut, owner=owner)
        after = await store.snapshot(store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
    finally:
        await store.close()
