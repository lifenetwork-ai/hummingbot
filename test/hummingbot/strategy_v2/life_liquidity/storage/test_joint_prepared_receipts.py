"""A lost prepared receipt and newer joint cash recover without another send."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryPayment
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot


async def pending_receipt(tmp_path, monkeypatch, *, kind='funding', before_prepare=None):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner)
        if before_prepare is not None:
            await before_prepare(store, values, owner)
        swap = await store._call(send_close, values) if kind == 'fee' else None
        prepared = replace(order(owner), risk_epoch=1)
        publish = store._publish

        def lost_receipt(key, *args, **kwargs):
            if key.startswith('prepared:'):
                raise OSError('synthetic crash before prepared receipt')
            return publish(key, *args, **kwargs)
        with monkeypatch.context() as patches:
            patches.setattr(store, '_publish', lost_receipt)
            with pytest.raises(CommitUnknown):
                await store.prepare(prepared, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        claim = (await store._call(values[1].claims))[prepared.intent_id]
        unsent = RecoveryOrder(claim, None, 'unsent', ())
        if kind == 'fee':
            proof = report(values, bundle, swap, fee='-.001')
            bundle['value'] = replace(bundle['value'], orders=(proof, unsent))
            cash = D('9.999')
        else:
            at = recovery.clock_ms()
            cash = D('9.9')
            values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, spot_cash_quote=cash)
            values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
            bundle['value'] = replace(bundle['value'], snapshot_sequence=2, orders=(unsent,),
                                      payments=(RecoveryPayment('101', D('-.1'), at),))
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), cash, D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        return repo, store, recovery, values, bundle, owner, prepared, facts
    except BaseException:
        await store.close()
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['funding', 'fee'])
async def test_prepared_receipt_plus_new_joint_cash_reconciles_once_and_releases_only_proven_unsent(tmp_path, monkeypatch, kind):
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch, kind=kind)
    try:
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == facts.account_quote_usdt
        assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
        assert view.budget.session_execution_loss_usdt == (D('.001') if kind == 'fee' else 0)
        assert store._wal.get(prepared.intent_id).state == 'ABORTED_BEFORE_SEND'
        assert await store._call(values[1].claim_ids) == ()
        assert values[3].sent == [] and len(values[4].sent) == (1 if kind == 'fee' else 0)
        meta = await store.read(store.binding)
        batches = [r for r in meta.resources['joint_cash_operations'].values() if r['command'].get('spot_prepared_receipts')]
        assert len(batches) == 1 and batches[0]['state'] == 'APPLIED'
        assert dict(batches[0]['command']['spot_prepared_receipts'][0]) == prepared.json()
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert (await store.snapshot(store.binding)).balance.total_quote_usdt == facts.account_quote_usdt
        assert store._cold_unarmed and not recovery._armed and not values[2]._runtime_enabled
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cut', ['wallet', 'receipt', 'abort_wallet', 'abort_subsidy', 'shared_release'])
async def test_prepared_receipt_joint_batch_crash_repairs_original_terms_without_repeating_cash(tmp_path, monkeypatch, cut):
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch)
    try:
        target, name = {
            'wallet': (store._ledger, 'apply_joint_snapshot'), 'receipt': (store, '_publish_joint_cash'),
            'abort_wallet': (store._ledger, 'abort_unsent'), 'abort_subsidy': (store._subsidy, 'release_unsent'),
            'shared_release': (values[1], 'settle')}[cut]
        original = getattr(target, name)
        triggered = False

        def interrupted(*args, **kwargs):
            nonlocal triggered
            if cut == 'receipt' and not args[0].startswith('joint-done:'):
                return original(*args, **kwargs)
            if triggered:
                return original(*args, **kwargs)
            triggered = True
            if cut != 'receipt':
                original(*args, **kwargs)
            raise OSError('synthetic joint prepared-receipt cut')
        with monkeypatch.context() as patches:
            patches.setattr(target, name, interrupted)
            assert not await repo.reconcile(store.binding, owner, spot_facts=facts)
        assert triggered
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.total_quote_usdt == D('9.9')
        assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
        assert store._wal.get(prepared.intent_id).state == 'ABORTED_BEFORE_SEND'
        assert await store._call(values[1].claim_ids) == ()
        assert values[3].sent == values[4].sent == []
        assert store._cold_unarmed and not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source_state', 'debt', 'coverage', 'writer', 'risk', 'cut_binding', 'time_before_owner', 'missing_subsidy', 'extra_subsidy', 'loss'])
async def test_prepared_receipt_batch_refuses_unproven_origin_scope_or_holds_without_cash_or_release(tmp_path, monkeypatch, fault):
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode, thaw
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch)
    try:
        if fault == 'source_state':
            original = bundle['value'].orders[0]
            bundle['value'] = replace(bundle['value'], orders=(replace(original, state='live', exchange_order_id='10'),), pending_wire_ids=(prepared.client_order_id,))
        elif fault == 'debt':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'coverage':
            facts = replace(facts, runner_scope_complete=False)
        elif fault in ('writer', 'risk', 'cut_binding', 'time_before_owner'):
            def changed_origin():
                meta = store._read()
                commands = thaw(meta.resources['commands'])
                row = commands[prepared.operation_id]
                if fault == 'writer':
                    row['original_owner'] = replace(owner, process_id=uuid4()).json()
                elif fault == 'risk':
                    row['command'] = replace(prepared, risk_epoch=2).json()
                elif fault == 'cut_binding':
                    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
                    row['original_cut']['checkpoint']['binding']['account_uid'] = '99'
                    row['fingerprint'] = fingerprint({'command': row['command'], 'cut': row['original_cut'], 'owner': row['original_owner']})
                else:
                    from datetime import timedelta
                    row['prepared_at'] = (owner.valid_from - timedelta(seconds=1)).isoformat()
                store._commit(meta.cut, owner, 'synthetic-invalid-original', (('commands', encode(commands)),), observation=True)
            await store._call(changed_origin)
        elif fault == 'missing_subsidy':
            await store._call(lambda: store._subsidy.reconcile(prepared.intent_id, actual_cost_quote=prepared.subsidy_quote_usdt))
        elif fault == 'extra_subsidy':
            await store._call(lambda: store._subsidy.reserve('foreign', D('.01'), session_id=prepared.session_id, at_utc=values[6].wall))
        else:
            await store._call(lambda: store._loss.record('foreign', D('.01'), session_id=prepared.session_id, at_utc=values[6].wall))
        before = await store.snapshot(store.binding)
        claims = await store._call(values[1].claim_ids)
        assert not await repo.reconcile(store.binding, owner, spot_facts=facts)
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await store._call(values[1].claim_ids) == claims
        assert values[3].sent == values[4].sent == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('complete_holds,report_fault', [(True, None), (False, None), (True, 'initial'), (True, 'sql'), (True, 'origin'), (True, 'late_projection')])
async def test_configured_service_routes_prepared_receipt_to_joint_proof_and_retains_incomplete_holds(tmp_path, monkeypatch, complete_holds, report_fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

    from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, config, sources, spot, swap = await joint_service(tmp_path, hedge=False)
    owner = config.owner
    prepared = None
    try:
        await service.recover()
        import asyncio

        from hummingbot.connector.trading_rule import TradingRule
        from hummingbot.core.data_type.common import TradeType
        from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
        from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
        spot.get_price_by_type = lambda *args, **kwargs: D('1')
        spot._account_balances = spot._account_available_balances = {'LIFE': D('5'), 'USDT': D('10')}
        spot._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.1'),
                                                       min_price_increment=D('.001'), min_base_amount_increment=D('.1'))
        spot._set_current_timestamp(sources.clock().timestamp())
        service.runner._set_current_timestamp(sources.clock().timestamp())
        service.controller.trading_permissions_ready = lambda: True  # Synthetic offline release only.
        service.controller.allow_create_executor_actions = lambda: True
        target, name = (service.store, '_publish') if complete_holds else (service.store._subsidy, 'reserve')
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if complete_holds and not args[0].startswith('prepared:'):
                return original(*args, **kwargs)
            raise OSError('synthetic configured preparation interruption')
        with monkeypatch.context() as patches:
            patches.setattr(target, name, interrupted)
            cfg = OrderExecutorConfig(id='pending-shared', timestamp=sources.clock().timestamp(), controller_id='life',
                                      connector_name='okx', trading_pair='LIFE-USDT', side=TradeType.BUY, amount=D('2'), price=D('1'),
                                      level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
            service.runner._async_action_dispatcher.submit([CreateExecutorAction(controller_id='life', executor_config=cfg)])
            await service.runner._async_action_dispatcher.drain()

            async def failed():
                while service.spot.failure_reason(cfg.id) is None:
                    await asyncio.sleep(.001)
            await asyncio.wait_for(failed(), 3)
            prepared = service.spot._issued[cfg.id]
            assert service.store._uncertain
    finally:
        try:
            await service.close()
        except StorageError:
            assert service.store._closed and service.store._uncertain
    permit = SendPermit(prepared.intent_id, prepared.client_order_id, prepared.intent_id, prepared.session_id,
                        prepared.epoch, prepared.config_version, prepared.risk_epoch, prepared.limit_price_usdt, prepared.quantity_base)
    claim = CapitalClaim.spot(permit, prepared.side, config.controller.storage_binding.account_uid)
    capital = replace(sources.capital_snapshot(), sequence=2, spot_cash_quote=D('9.9'))
    account = replace(sources.swap_account(), snapshot_sequence=2)
    bundle = replace(await sources.joint_collect(), snapshot_sequence=2,
                     orders=(RecoveryOrder(claim, None, 'unsent', ()),),
                     payments=(RecoveryPayment('101', D('-.1'), int(sources.clock().timestamp() * 1000)),))
    facts = SpotFinancialRecoveryFacts(config.controller.storage_binding, sources.clock(), D('5'), D('9.9'),
                                       D('0'), D('0'), (), (), (), (), True, True, True)

    async def collect():
        return bundle
    sources = replace(sources, capital_snapshot=lambda: capital, swap_account=lambda: account,
                      joint_collect=collect, joint_spot_recovery=lambda: ((), facts))
    cold = await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap}, sources=sources,
                                           market_data_provider=service.controller.market_data_provider, create_binding=False)
    try:
        if report_fault is not None:
            def corrupt_reporting():
                from hummingbot.model.executors import Executors
                from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode, fingerprint, thaw
                if report_fault == 'sql':
                    with cold.reporting.repository._recorder.sql_manager.get_new_session() as session:
                        native = session.query(Executors).filter(Executors.id == prepared.intent_id).one()
                        native.config = {**native.config, 'price': '2'}
                        session.commit()
                else:
                    meta = cold.store._read()
                    rows = thaw(meta.resources['recorder_outbox'])
                    if report_fault == 'initial':
                        rows = {}
                    elif report_fault == 'late_projection':
                        row = next(iter(rows.values()))
                        row['cut'] = meta.cut.json()
                        row['identity'] = fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']})
                    else:
                        next(iter(rows.values()))['identity'] = '0' * 64
                    cold.store._commit(meta.cut, owner, 'synthetic-report-corruption',
                                       (('recorder_outbox', encode(rows)),), observation=True)
            await cold.store._call(corrupt_reporting)
        if complete_holds and report_fault is None:
            await cold.recover()
            await cold.recover()
            assert (await cold.store.snapshot(cold.store.binding)).balance.total_quote_usdt == D('9.9')
            assert (await cold.store.attempt(prepared.intent_id, cold.store.binding)).state.value == 'ABORTED_BEFORE_SEND'
            assert await cold.store._call(cold.controller._shared_capital_authority.claim_ids) == ()
        else:
            with pytest.raises(StorageError):
                await cold.recover()
            view = await cold.store.snapshot(cold.store.binding)
            assert view.balance.total_quote_usdt == D('10') and view.balance.reserved_quote_usdt == D('2')
            assert await cold.store._call(cold.controller._shared_capital_authority.claim_ids) == (prepared.intent_id,)
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert cold.runner.executor_orchestrator.active_executors['life'] == []
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_prepared_receipt_payload_is_not_a_joint_authority_or_send_grant(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch)
    try:
        at = recovery.clock_ms()
        command = JointCashSpot('forged-proof', store.binding, 'a' * 64, facts.observed_at,
                                (JointCashEvent(f'JOINT:{at}:FUNDING:101', D('-.1')),), D('10'), D('9.9'), True, True,
                                'okx_joint_reconciled', spot_prepared_receipts=(prepared,))
        assert JointCashSpot.from_json(command.json()) == command
        legacy = replace(command, spot_prepared_receipts=()).json()
        assert 'spot_prepared_receipts' not in legacy
        assert JointCashSpot.from_json(legacy).json() == legacy
        before = await store.snapshot(store.binding)
        with pytest.raises(StorageError, match='JOINT_PREPARED_RECEIPT_AUTHORITY_REQUIRED'):
            await store.apply_joint_cash(command, cut=before.cut, owner=owner)
        after = await store.snapshot(store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await store._call(values[1].claim_ids) == (prepared.intent_id,)
        assert store._cold_unarmed and not recovery._armed
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expired_writer_can_finish_original_prepared_receipt_without_extending_session(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch)
    try:
        values[6].advance((owner.expires_at - values[6].wall).total_seconds() + 1)
        now = recovery.clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=3, observed_at_ms=now)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=3, observed_at_ms=now)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=3, observed_at_ms=now)
        facts = replace(facts, observed_at=values[6].wall)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.session.expires_at == owner.expires_at
        assert view.balance.total_quote_usdt == D('9.9') and view.balance.reserved_quote_usdt == 0
        assert store._cold_unarmed and not recovery._armed and not values[2]._runtime_enabled
        assert values[3].sent == values[4].sent == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_prepared_receipt_plus_unrelated_spot_fill_and_funding_keep_original_fill_values_and_loss(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryTrade
    from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, SpotFillObservation

    async def other_order(store, values, owner):
        other = replace(order(owner), operation_id='other-prepare', intent_id='other', client_order_id='otherwire',
                        side='SELL', quantity_base=D('1'), fee_buffer_quote_usdt=D('.1'), risk_epoch=1)
        await store.prepare_current(other, owner=owner)
        await store.arm_current(other.operation_id, owner=owner)
        ack = AckSpot('other-ack', other.operation_id, other.intent_id, other.client_order_id, other.market,
                      other.side, other.session_id, other.epoch, '999', values[6].wall)
        await store.acknowledge(ack, cut=(await store.snapshot(store.binding)).cut, owner=owner)
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch,
                                                                                          before_prepare=other_order)
    try:
        claim = (await store._call(values[1].claims))['other']
        at = recovery.clock_ms()
        trade = RecoveryTrade('other-trade', D('1'), D('1'), 'USDT', D('-.02'), D('0'), at)
        proof = RecoveryOrder(claim, '999', 'filled', (trade,))
        values[8]['snapshot'] = replace(values[8]['snapshot'], spot_life_base=D('9'), spot_cash_quote=D('10.88'))
        bundle['value'] = replace(bundle['value'], orders=(proof, *bundle['value'].orders))
        fill = SpotFillObservation('other-trade', D('1'), D('1'), 'USDT', D('-.02'), at,
                                   D('1.1'), at, at, 'independent_market')
        row = FillSpot('other-fill', 'other-prepare', claim.intent_id, claim.wire_id, 'LIFE-USDT', 'SELL',
                       claim.session_id, claim.epoch, '999', D('1'), (fill,), facts.observed_at)
        facts = replace(facts, account_base_life=D('9'), account_quote_usdt=D('10.88'),
                        account_trade_ids=('other-trade',), accepted_wire_ids=(claim.wire_id,))
        assert await repo.reconcile(store.binding, owner, spot_fills=(row,), spot_facts=facts), recovery.reason_code
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (D('9'), D('10.88'))
        assert view.balance.reserved_base_life == view.balance.reserved_quote_usdt == 0
        assert view.budget.session_execution_loss_usdt == D('.12')
        assert view.budget.session_subsidy_committed_usdt == D('.12')
        assert store._wal.get('other').state == 'TERMINAL'
        assert store._wal.get(prepared.intent_id).state == 'ABORTED_BEFORE_SEND'
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert after.balance == view.balance and after.budget == view.budget
        assert values[3].sent == values[4].sent == [] and not recovery._armed
    finally:
        await store.close()
