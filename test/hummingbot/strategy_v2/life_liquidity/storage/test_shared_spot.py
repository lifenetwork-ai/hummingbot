"""Shared spot preparation owns original claims and native wallet holds."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@pytest.mark.asyncio
async def test_shared_spot_prepares_claim_and_wallet_then_joint_unsent_releases_both(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner)
        command = replace(order(owner), risk_epoch=1)
        await store.prepare(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        claim = (await store._call(values[1].claims))['intent1']
        assert claim.risk_epoch == 1 and claim.quantity_base == D('2')
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == D('2')
        assert view.budget.session_subsidy_committed_usdt == D('.1')
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2)
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, orders=(RecoveryOrder(claim, None, 'unsent', ()),))
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
        assert await store._call(values[1].claim_ids) == ()
        assert store._wal.get('intent1').state == 'ABORTED_BEFORE_SEND'
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['claim_before', 'claim_after', 'wal', 'principal', 'subsidy', 'receipt'])
async def test_shared_preparation_cold_recovery_recreates_only_original_hold_and_never_arms(tmp_path, monkeypatch, stage):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner)
        command = replace(order(owner), risk_epoch=1)
        target, name = {
            'claim_before': (values[1], 'spot'), 'claim_after': (values[1], 'spot'),
            'wal': (store._wal, 'begin'), 'principal': (store._ledger, 'reserve'),
            'subsidy': (store._subsidy, 'reserve'), 'receipt': (store, '_publish')}[stage]
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if stage == 'receipt' and not args[0].startswith('prepared:'):
                return original(*args, **kwargs)
            if stage not in ('claim_before', 'receipt'):
                original(*args, **kwargs)
            raise OSError('synthetic shared preparation cut')
        with monkeypatch.context() as patches:
            patches.setattr(target, name, interrupted)
            with pytest.raises(CommitUnknown):
                await store.prepare(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        await store.resume_prepare(command, facts=facts, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.resume_prepare(command, facts=facts, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == D('2') and view.balance.total_quote_usdt == D('10')
        assert view.budget.session_subsidy_committed_usdt == D('.1')
        assert await store._call(values[1].claim_ids) == ('intent1',)
        assert store._wal.get('intent1').state == 'PREPARED'
        assert store._cold_unarmed and not recovery._armed
        assert values[3].sent == values[4].sent == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_risk', 'boolean_risk', 'insufficient_shared', 'foreign_policy'])
async def test_invalid_shared_spot_is_denied_before_any_preparation(tmp_path, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner)
        if fault == 'insufficient_shared':
            values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, funding_liability_quote=D('11'))
            values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
            bundle['value'] = replace(bundle['value'], snapshot_sequence=2)
            assert await repo.reconcile(store.binding, owner), recovery.reason_code
        with pytest.raises((StorageError, ValueError)):
            command = replace(order(owner), risk_epoch=True if fault == 'boolean_risk' else None if fault == 'missing_risk' else 1)
            if fault == 'insufficient_shared':
                # A valid SPOT wallet alone is insufficient: the combined
                # gross/delta/margin evaluation remains independently binding.
                command = replace(command, quantity_base=D('2'))
            elif fault == 'foreign_policy':
                values[1].policy = replace(values[1].policy, account_uid='foreign')
            await store.prepare(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        assert store._wal.all_records() == ()
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == view.budget.session_subsidy_committed_usdt == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_configured_shared_spot_uses_actual_runner_and_rest_then_keeps_both_holds(tmp_path):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx import okx_constants
    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.core.data_type.common import TradeType
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
    service, _, _, connector, _ = await joint_service(tmp_path, hedge=False)
    sent = []

    class Response:
        status = 200
        content_type = 'application/json'

        async def json(self):
            return {'data': [{'sCode': '0', 'ordId': '101'}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=RESTAssistant(
        connection=RESTConnection(Session()), throttler=AsyncThrottler(okx_constants.RATE_LIMITS))))
    connector._api_request_url = AsyncMock(return_value='https://www.okx.com/api/v5/trade/order')
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    connector.get_price_by_type = lambda *args, **kwargs: D('1')
    connector._account_balances = connector._account_available_balances = {'USDT': D('10'), 'LIFE': D('5')}
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.1'),
                                                        min_price_increment=D('.001'), min_base_amount_increment=D('.1'))
    at = service.sources.clock().timestamp()
    connector._set_current_timestamp(at)
    service.runner._set_current_timestamp(at)
    try:
        await service.recover()
        service.controller.trading_permissions_ready = lambda: True  # Synthetic offline release.
        service.controller.allow_create_executor_actions = lambda: True
        config = OrderExecutorConfig(id='sharedquote', timestamp=at, controller_id='life', connector_name='okx',
                                     trading_pair='LIFE-USDT', side=TradeType.BUY, amount=D('2'), price=D('1'), level_id='0',
                                     execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        action = CreateExecutorAction(controller_id='life', executor_config=config)
        service.runner._async_action_dispatcher.submit([action])
        await service.runner._async_action_dispatcher.drain()

        async def acked():
            while 'sharedquote' not in service.spot._issued or service.spot._pending:
                failure = service.spot.failure_reason('sharedquote')
                assert failure is None, failure
                await asyncio.sleep(.001)
        await asyncio.wait_for(acked(), 3)
        await service.reporting.drain()
        view = await service.store.snapshot(service.store.binding)
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (D('5'), D('10'))
        assert view.balance.reserved_quote_usdt == D('2')
        assert view.budget.session_subsidy_committed_usdt == D('.1')
        assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == ('sharedquote',)
        assert (await service.store.attempt('sharedquote', service.store.binding)).state.value == 'ACKED'
        assert len(sent) == 1
        assert len(service.runner.executor_orchestrator.active_executors['life']) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wallet', 'risk_epoch', 'owner'])
async def test_cold_shared_preparation_rejects_changed_source_or_original_identity(tmp_path, monkeypatch, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True)
    try:
        await bind_shared_spot(repo)
        assert await repo.reconcile(store.binding, owner)
        command = replace(order(owner), risk_epoch=1)
        with monkeypatch.context() as patches:
            def failed(*args, **kwargs):
                raise OSError('synthetic cut before original shared claim')
            patches.setattr(values[1], 'spot', failed)
            with pytest.raises(CommitUnknown):
                await store.prepare(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        await bind_shared_spot(repo)
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        supplied, writer = command, owner
        if fault == 'wallet':
            values[8]['snapshot'] = replace(values[8]['snapshot'], spot_cash_quote=D('11'))
        elif fault == 'risk_epoch':
            supplied = replace(command, risk_epoch=2)
        else:
            from uuid import uuid4
            writer = replace(owner, generation=uuid4())
        with pytest.raises(StorageError):
            await store.resume_prepare(supplied, facts=facts, cut=(await store.snapshot(store.binding)).cut, owner=writer)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await store._call(values[1].claim_ids) == ()
        assert store._wal.all_records() == ()
        assert store._cold_unarmed and not recovery._armed
    finally:
        await store.close()
