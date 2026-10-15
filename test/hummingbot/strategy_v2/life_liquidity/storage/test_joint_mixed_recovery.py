"""Original ACK uncertainty plus newly observed approved transfers and fills."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_fill_repository import scenario
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot


async def mixed(tmp_path, monkeypatch, *, written=True, currency='USDT', amount=D('2'), kind='ACK'):
    at = int(setup_shared(tmp_path / 'clock-reference')[6].wall.timestamp() * 1000)
    approval = ApprovedSpotCashflow('101', currency, amount, at)
    repo, store, recovery, values, bundle, owner, proof, fill, facts = await scenario(tmp_path, approvals=(approval,))
    ack = AckSpot('original-ack', fill.prepare_operation_id, fill.intent_id, fill.client_order_id, fill.market,
                  fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, fill.observed_at)
    target, method = (store._wal, 'acknowledge') if kind == 'ACK' else (store._ledger, 'apply_fills_snapshot')
    original = getattr(target, method)

    def interrupted(*args, **kwargs):
        if written:
            original(*args, **kwargs)
        raise OSError('synthetic lost original ACK reply')
    with monkeypatch.context() as patches:
        patches.setattr(target, method, interrupted)
        with pytest.raises(CommitUnknown):
            cut = (await store.snapshot(store.binding)).cut
            if kind == 'ACK':
                await store.acknowledge(ack, cut=cut, owner=owner)
            else:
                await store.apply_fills(fill, cut=cut, owner=owner)
    price = D('1') if currency == 'USDT' else D('1.2')
    flow = CashflowSpot('new-flow', '101', currency, amount, at, price, at, at,
                        'quote_unit' if currency == 'USDT' else 'independent_market', fill.observed_at)
    quote = D('8.979') + (amount if currency == 'USDT' else 0)
    base = D('11') + (amount if currency == 'LIFE' else 0)
    values[8]['snapshot'] = replace(values[8]['snapshot'], spot_cash_quote=quote, spot_life_base=base)
    facts = replace(facts, account_quote_usdt=quote, account_base_life=base, account_cashflow_ids=('101',))
    repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
    return repo, store, recovery, values, bundle, owner, fill, facts, ack, flow


@pytest.mark.asyncio
@pytest.mark.parametrize('written', [False, True])
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
@pytest.mark.parametrize('currency,amount', [('USDT', D('2')), ('USDT', D('-2')), ('LIFE', D('2')), ('LIFE', D('-2'))])
async def test_original_ack_and_new_approved_cashflow_fill_and_swap_cash_reconcile_once(tmp_path, monkeypatch, written, currency, amount, kind):
    repo, store, recovery, values, bundle, owner, fill, facts, ack, flow = await mixed(
        tmp_path, monkeypatch, written=written, currency=currency, amount=amount, kind=kind)
    try:
        assert 'new-flow' not in (await store.read(store.binding)).resources.get('cashflow_operations', {})
        assert await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts,
                                    spot_cashflows=(flow,)), recovery.reason_code
        before = await store.snapshot(store.binding)
        assert (before.balance.total_base_life, before.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert before.budget.session_execution_loss_usdt == D('.121')
        # One LIFE remains at 1 USDT, plus the unused 0.08 USDT fee buffer.
        assert before.balance.reserved_quote_usdt == D('1.08')
        if kind == 'ACK':
            assert (await store.lookup_ack(ack.operation_id, store.binding)).state == 'ACKED'
        else:
            assert (await store.lookup_fill(fill.operation_id, store.binding)).state == 'APPLIED'
        assert (await store.lookup_cashflow(flow.operation_id, store.binding)).state == 'APPLIED'
        raw = (await store.read(store.binding)).resources
        if kind == 'ACK':
            assert thaw(raw['acknowledgements'][ack.operation_id]['command']) == ack.json()
        else:
            assert thaw(raw['fill_operations'][fill.operation_id]['command']) == fill.json()
        assert thaw(raw['cashflow_operations'][flow.operation_id]['command']) == flow.json()
        assert values[1].claim_ids() == ('intent1',)
        measured = (await repo.state(store.binding)).measurement
        assert measured.adjusted_nav_quote == facts.account_quote_usdt + facts.account_base_life - amount * flow.independent_price_usdt
        # The permanent joint command is now the original cashflow source;
        # a caller need not reconstruct/reprice it on replay or cold restart.
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert store._cold_unarmed and not recovery._armed
        assert values[3].sent == [] and len(values[4].sent) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unapproved', 'amount', 'bill_scope', 'bill_ids', 'debt', 'uid', 'reference',
                                   'future_receipt', 'missing_flow', 'fill_value', 'duplicate_bill', 'no_facts'])
async def test_unqualified_mixed_inputs_write_no_money_or_new_transfer_origin(tmp_path, monkeypatch, fault):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, store, recovery, values, _, owner, fill, facts, _, flow = await mixed(tmp_path, monkeypatch)
    try:
        flows = (flow,)
        if fault == 'unapproved':
            flows = (replace(flow, bill_id='102'),)
        elif fault == 'amount':
            flows = (replace(flow, signed_amount=D('3')),)
        elif fault == 'bill_scope':
            facts = replace(facts, bill_scope_complete=False)
        elif fault == 'bill_ids':
            facts = replace(facts, account_cashflow_ids=())
        elif fault == 'debt':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'uid':
            facts = replace(facts, binding=replace(facts.binding, account_uid='999'))
        elif fault == 'reference':
            flows = (replace(flow, value_at_ms=flow.value_at_ms - 1001),)
        elif fault == 'future_receipt':
            values[6].advance(.1)
            flows = (replace(flow, observed_at=flow.observed_at + timedelta(milliseconds=10)),)
        elif fault == 'missing_flow':
            flows = ()
        elif fault == 'fill_value':
            fill = replace(fill, fills=(replace(fill.fills[0], signed_fee=D('-.03')),))
        elif fault == 'duplicate_bill':
            flows = (flow, replace(flow, operation_id='duplicate'))
        else:
            facts = None
        before = await store.snapshot(store.binding)
        claims = await store._call(values[1].claim_ids)
        if fault in ('duplicate_bill', 'no_facts'):
            with pytest.raises(StorageError, match='JOINT_SPOT_CAPTURE_INVALID_OR_CAPACITY'):
                await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts, spot_cashflows=flows)
        else:
            assert not await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts, spot_cashflows=flows)
        after = await store.snapshot(store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert 'new-flow' not in (await store.read(store.binding)).resources.get('cashflow_operations', {})
        assert not store._uncertain and await store._call(values[1].claim_ids) == claims
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'floor_after', 'receipt', 'ack_after', 'native_receipt'])
@pytest.mark.parametrize('currency', ['USDT', 'LIFE'])
async def test_mixed_admission_and_money_crash_recover_original_transfers_without_caller_reconstruction(tmp_path, monkeypatch, stage, currency):
    repo, store, recovery, values, bundle, owner, fill, facts, ack, flow = await mixed(tmp_path, monkeypatch, currency=currency)
    try:
        target, method = {
            'wallet_before': (store._ledger, 'apply_joint_snapshot'), 'wallet_after': (store._ledger, 'apply_joint_snapshot'),
            'loss_after': (store._loss, 'record'), 'floor_after': (store._subsidy, 'record_fill_floor'),
            'receipt': (store, '_publish_joint_cash'), 'ack_after': (store._wal, 'acknowledge'),
            'native_receipt': (recovery.journal, 'commit'),
        }[stage]
        original = getattr(target, method)
        hit = False

        def interrupt(*args, **kwargs):
            nonlocal hit
            if stage == 'receipt' and not args[0].startswith('joint-done:'):
                return original(*args, **kwargs)
            if stage == 'native_receipt' and (args[0]['phase'] != 'COMMITTED' or not args[0]['bundle']['orders']):
                return original(*args, **kwargs)
            hit = True
            if stage not in ('wallet_before', 'receipt', 'native_receipt'):
                original(*args, **kwargs)
            raise OSError('synthetic mixed accounting interruption')
        with monkeypatch.context() as patches:
            patches.setattr(target, method, interrupt)
            assert not await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts, spot_cashflows=(flow,))
        assert hit
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert view.budget.session_execution_loss_usdt == D('.121')
        assert view.balance.reserved_quote_usdt == D('1.08') and values[1].claim_ids() == ('intent1',)
        assert (await store.lookup_ack(ack.operation_id, store.binding)).state == 'ACKED'
        assert (await store.lookup_cashflow(flow.operation_id, store.binding)).state == 'APPLIED'
        assert store._cold_unarmed and not recovery._armed and not values[2]._runtime_enabled
        assert values[3].sent == [] and len(values[4].sent) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_joint_cash_dto_without_native_recovery_authority_cannot_admit_a_new_transfer(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
    repo, store, recovery, values, _, owner, fill, facts, _, flow = await mixed(tmp_path, monkeypatch)
    try:
        command = JointCashSpot('unqualified-direct', store.binding, 'a' * 64, facts.observed_at, (),
                                facts.account_base_life, facts.account_quote_usdt, True, True,
                                'okx_joint_reconciled', (fill,), (flow,))
        before = await store.snapshot(store.binding)
        with pytest.raises(StorageError):
            await store.apply_joint_cash(command, cut=before.cut, owner=owner)
        assert (await store.snapshot(store.binding)).cut == before.cut
        assert not (await store.read(store.binding)).resources.get('joint_cash_operations')
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('before_financial_start', [False, True])
async def test_configured_service_uses_typed_new_transfer_inputs_and_cold_replay(tmp_path, monkeypatch, before_financial_start):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW

    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
    from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotAccountingPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.joint import JointSpotRecoveryInputs
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    at = int(NOW.timestamp() * 1000)
    policy = SpotAccountingPolicy(D('5'), D('10'), D('1'), NOW, '1', (ApprovedSpotCashflow('101', 'USDT', D('2'), at),))
    state = {}

    def callbacks(config, source):
        state['capital'], state['account'] = source.capital_snapshot(), source.swap_account()
        state['facts'] = None
        state['flows'] = ()

        async def collect():
            return state.get('bundle') or await source.joint_collect()
        return dict(capital_snapshot=lambda: state['capital'], swap_account=lambda: state['account'], joint_collect=collect,
                    joint_spot_recovery=lambda: JointSpotRecoveryInputs((), state['facts'], state['flows']))
    service, config, sources, spot, swap = await joint_service(tmp_path, hedge=False,
                                                               config_extra={'accounting_policy': policy}, source_extra=callbacks)
    provider = service.controller.market_data_provider
    try:
        await service.recover()
        bundle = await sources.joint_collect()
        state['bundle'] = replace(bundle, snapshot_sequence=2, payments=(RecoveryPayment('201', D('-.1'), at),))
        state['capital'] = replace(state['capital'], sequence=2, spot_cash_quote=D('11.9'))
        state['account'] = replace(state['account'], snapshot_sequence=2)
        state['flows'] = (CashflowSpot('service-flow', '101', 'USDT', D('2'), at, D('1'), at, at, 'quote_unit', NOW),)
        state['facts'] = SpotFinancialRecoveryFacts(service.store.binding, NOW, D('5'), D('11.9'), D('0'), D('0'),
                                                    (), ('101',), (), (), True, True, True)
        if before_financial_start:
            from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
            original = service.store._publish_joint_cash

            def fail(key, *args, **kwargs):
                if key.startswith('joint-start:'):
                    raise OSError('synthetic failure between native intent and financial start')
                return original(key, *args, **kwargs)
            with monkeypatch.context() as patches:
                patches.setattr(service.store, '_publish_joint_cash', fail)
                with pytest.raises(StorageError):
                    await service.recover()
        else:
            await service.recover()
            await service.recover()
            assert (await service.store.snapshot(service.store.binding)).balance.total_quote_usdt == D('11.9')
        assert not service.store._wal.all_records() and not service.runtime._local_ready()
    finally:
        await service.close()
    cold = await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap}, sources=sources,
                                           market_data_provider=provider)
    try:
        state['flows'] = ()  # The original admitted value comes from the repository.
        await cold.recover()
        assert (await cold.store.snapshot(cold.store.binding)).balance.total_quote_usdt == D('11.9')
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert (await cold.store.lookup_cashflow('service-flow', cold.store.binding)).state == 'APPLIED'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_later_reobservation_cannot_replace_admitted_life_transfer_valuation(tmp_path, monkeypatch):
    repo, store, recovery, values, _, owner, fill, facts, _, flow = await mixed(tmp_path, monkeypatch, currency='LIFE')
    try:
        assert await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts, spot_cashflows=(flow,))
        before = await store.snapshot(store.binding)
        changed = replace(flow, operation_id='replacement', independent_price_usdt=D('1.3'))
        assert not await repo.reconcile(store.binding, owner, spot_facts=facts, spot_cashflows=(changed,))
        after = await store.snapshot(store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        raw = (await store.read(store.binding)).resources
        assert thaw(raw['cashflow_operations'][flow.operation_id]['command']) == flow.json()
    finally:
        await store.close()
