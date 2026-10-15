"""Actual controller/executor and OKX transport use one financial repository."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    create_fixture,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.connector.exchange.okx import okx_constants
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.spot_application import FileSpotApplication, SpotAdmission


async def setup(tmp_path, *, delay=None, observation_transport=None, operator_keys=None):
    config = LifeLiquidityConfig(id='life', recovery_account_uid='12345', recovery_reconciliation_max_age_ms=1000,
                                 cancel_retry_interval_ms=100, cancel_max_requests_per_cycle=1)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
    files, binding, owner = create_fixture(tmp_path / 'state')
    binding = replace(binding, config_hash=fingerprint(config.strategy.model_dump(mode='json')))
    controller.config = config.model_copy(update={'storage_binding': binding})
    clock = {'now': NOW}
    from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotAccountingPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalPolicy
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'locks', create_binding=True, owner=owner,
                                   clock=lambda: clock['now'], fill_policy=SpotFillPolicy(1000),
                                   accounting_policy=SpotAccountingPolicy(Decimal('5'), Decimal('10'), Decimal('1'),
                                                                          NOW, '1', ()),
                                   terminal_policy=SpotTerminalPolicy(1000), recovery_policy=SpotFinancialRecoveryPolicy(1000),
                                   operator_keys=operator_keys)
    sent = []

    class Response:
        status = 200
        content_type = 'application/json'

        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    class Session:
        async def request(self, **kwargs):
            if kwargs['method'] == 'GET':
                if observation_transport is not None and not kwargs['url'].endswith(okx_constants.OKX_ACCOUNT_CONFIG_PATH):
                    return Response(observation_transport(kwargs, sent, clock))
                return Response({'code': '0', 'data': [{'uid': '12345'}]})
            sent.append(kwargs)
            if delay is not None:
                await delay.wait()
            return Response({'data': [{'sCode': '0', 'ordId': '123456789'}]})

    connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-passphrase',
                            trading_pairs=[], trading_required=False)
    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=AsyncThrottler(okx_constants.RATE_LIMITS))
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(side_effect=lambda path_url, **kwargs: 'https://www.okx.com' + path_url)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    # The real protected API schedules the fake _create_order driver; its order
    # placement/auth/REST/throttler and async ACK paths remain actual connector code.

    async def create_order(**kwargs):
        await connector._place_order(**{key: value for key, value in kwargs.items()
                                        if key != 'position_action'})
    connector._create_order = create_order
    application = await FileSpotApplication.install(store, controller, connector, owner,
                                                    admission=lambda config: SpotAdmission(Decimal('1'), Decimal('.1'),
                                                                                           Decimal('0'), Decimal('0')),
                                                    authorize_permit=lambda permit: True, risk_epoch=lambda: 1, max_pending=1,
                                                    shutdown_timeout_s=.1, ack_timeout_s=2)
    # Explicit isolated offline release. Production's actual release stays false.
    controller.trading_permissions_ready = lambda: True
    controller.allow_create_executor_actions = lambda: True
    strategy = SimpleNamespace(connectors={'okx': connector}, controllers={'life': controller})
    order = OrderExecutorConfig(id='repository-quote', controller_id='life', timestamp=1,
                                connector_name='okx', trading_pair='LIFE-USDT', side=TradeType.BUY,
                                amount=Decimal('2'), price=Decimal('1'), level_id='0',
                                execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    executor = OrderExecutor(strategy, order)
    executor.get_order_price = lambda: order.price
    controller._runner_orchestrator = SimpleNamespace(active_executors={'life': [executor]},
                                                      get_stored_executors_by_controller=lambda _: ())
    return application, store, controller, executor, sent, clock


@pytest.mark.asyncio
async def test_real_executor_prepare_arm_transport_and_ack_share_exact_repository_state(tmp_path):
    app, store, controller, executor, sent, _ = await setup(tmp_path)
    try:
        executor.place_open_order()
        await app.drain()
        assert len(sent) == 1
        row = await store.attempt('repository-quote', store.binding)
        assert row.state.value == 'ACKED' and row.exchange_order_id == '123456789'
        view = await store.snapshot(store.binding)
        assert view.balance.reserved_quote_usdt == Decimal('2')
        assert view.budget.session_subsidy_committed_usdt == Decimal('.1')
        assert controller._order_safety_wal is store._wal
        assert controller._order_safety_reservations is store._ledger
        with pytest.raises(ValueError, match='RECONCILE_BEFORE_RETRY'):
            app.submit(executor.config, amount=executor.config.amount, price=executor.config.price,
                       order_type=OrderType.LIMIT_MAKER)
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_watchdog_uses_repository_session_tick_without_native_side_writer(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    app, store, controller, _, _, _ = await setup(tmp_path, observation_transport=empty_transport)
    controller._runner_orchestrator.active_executors['life'] = []
    app.install_observer(OkxSpotRepositoryObserver(app, independent_fill_value=lambda key, at: None,
                                                   independent_cashflow_value=lambda key, at: None, max_pages=2))
    try:
        before = await store.snapshot(store.binding)
        controller._order_safety_manager.tick = MagicMock(side_effect=AssertionError('native writer forbidden'))
        controller.on_safety_tick(1)
        first_task = controller.order_safety_task
        controller.on_safety_tick(2)
        assert controller.order_safety_task is first_task
        await first_task
        controller._order_safety_manager.tick.assert_not_called()
        assert controller.order_safety_reason_code == 'OLD_ORDERS_RECONCILED'
        assert not app.local_ready()
        after = await store.snapshot(store.binding)
        assert after.session.state == 'PAUSED' and not after.recovery_required
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await app.close()
        await store.close()


def empty_transport(request, sent, clock):
    path = request['url']
    if path.endswith(okx_constants.OKX_ACCOUNT_BILLS_ARCHIVE_PATH):
        return {'code': '0', 'data': [{'billId': '1'}]}
    if path.endswith(okx_constants.OKX_BALANCE_PATH):
        return {'code': '0', 'data': [{'details': [{'ccy': 'LIFE', 'cashBal': '5', 'liab': '0'},
                                                   {'ccy': 'USDT', 'cashBal': '10', 'liab': '0'}]}]}
    return {'code': '0', 'data': []}


@pytest.mark.asyncio
async def test_expiry_watchdog_cancels_and_accounts_fills_without_resetting_deadline(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    app, store, controller, executor, _, clock = await setup(tmp_path, observation_transport=filled_transport)
    app.install_observer(OkxSpotRepositoryObserver(
        app, independent_fill_value=lambda key, at: IndependentFillObservation(Decimal('.9'), at, at, 'independent_market'),
        independent_cashflow_value=lambda key, at: None, max_pages=2))
    app.connector.cancel_by_exchange_order_id = AsyncMock(return_value=True)
    try:
        executor.place_open_order()
        await app.drain()
        before = await store.snapshot(store.binding)
        clock['now'] = before.session.expires_at
        controller.on_safety_tick(1)
        await controller.order_safety_task
        after = await store.snapshot(store.binding)
        assert after.session.state == 'EXPIRED' and after.session.expires_at == before.session.expires_at
        assert controller._order_safety_manager.state == 'EXPIRED'
        assert after.balance.reserved_quote_usdt == 0
        assert after.budget.session_execution_loss_usdt == Decimal('.22')
        assert (await store.attempt(executor.config.id, store.binding)).cancel_attempts == 1
        assert controller.order_safety_reason_code == 'OLD_ORDERS_RECONCILED'
        assert not app.local_ready()
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_stop_while_watchdog_worker_waits_revokes_without_session_or_money_write(tmp_path, monkeypatch):
    import threading
    app, store, controller, _, sent, _ = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = app._session_inputs

    def delayed():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(app, '_session_inputs', delayed)
    try:
        before = await store.snapshot(store.binding)
        controller.on_safety_tick(1)
        assert await asyncio.to_thread(entered.wait, 2)
        controller.stop()
        assert not app.local_ready()
        await asyncio.wait_for(asyncio.sleep(0), .1)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await controller.order_safety_task
        after = await store.snapshot(store.binding)
        assert (after.balance, after.budget, after.session) == (before.balance, before.budget, before.session)
        assert sent == []
    finally:
        release.set()
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_cancel_uses_durable_commands_and_bounds_retries_without_releasing_holds(tmp_path):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.order_gateway import CancelRequestError
    app, store, controller, executor, _, clock = await setup(tmp_path)
    calls = []

    async def cancel(pair, exchange_id):
        attempt = await store.attempt(executor.config.id, store.binding)
        calls.append((pair, exchange_id, attempt.cancel_attempts))
        assert attempt.cancel_requested
        return False if len(calls) == 1 else True
    app.connector.cancel_by_exchange_order_id = cancel
    try:
        executor.place_open_order()
        await app.drain()
        before = await store.snapshot(store.binding)
        app._issued.clear()  # Cancellation cannot rely on a current-process send cache.
        scope = ((str(app.owner.session_id), app.owner.epoch),)
        with pytest.raises(CancelRequestError) as failure:
            await controller._order_safety_gateway.request_cancel_scopes(scope)
        assert failure.value.requests_used == 1
        assert calls == [('LIFE-USDT', '123456789', 1)]
        assert await app.cancel_scopes(scope) == 0
        clock['now'] += timedelta(milliseconds=100)
        results = await asyncio.gather(app.cancel_scopes(scope), app.cancel_scopes(scope))
        assert results == [1, 0]
        assert calls[-1] == ('LIFE-USDT', '123456789', 2)
        assert await controller._order_safety_gateway.request_cancel(*scope[0], max_requests=0) == 0
        after = await store.snapshot(store.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert (await store.attempt(executor.config.id, store.binding)).state.value == 'ACKED'
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'expiry', 'config', 'admission'])
async def test_repository_spot_rechecks_authority_after_real_throttler_wait(tmp_path, change):
    from contextlib import asynccontextmanager
    app, store, controller, executor, sent, clock = await setup(tmp_path)
    assistant = await app.connector._web_assistants_factory.get_rest_assistant()
    original_throttler = assistant._throttler
    entered, release = asyncio.Event(), asyncio.Event()
    completed = asyncio.get_running_loop().create_future()
    create = app.connector._create_order

    class DelayedThrottler:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            async with original_throttler.execute_task(limit_id=limit_id):
                if limit_id == okx_constants.OKX_PLACE_ORDER_PATH:
                    entered.set()
                    await release.wait()
                yield

    async def capture(**kwargs):
        try:
            await create(**kwargs)
            completed.set_result(None)
        except Exception as exc:
            completed.set_result(exc)
    assistant._throttler = DelayedThrottler()
    app.connector._create_order = capture
    try:
        executor.place_open_order()
        await asyncio.wait_for(entered.wait(), 2)
        assert (await store.attempt(executor.config.id, store.binding)).state.value == 'SEND_UNKNOWN'
        if change == 'stop':
            app.cancel_local(executor.config.id)
        elif change == 'expiry':
            clock['now'] = app.owner.expires_at
        elif change == 'admission':
            app.admission = lambda _: SpotAdmission(Decimal('2'), Decimal('.1'), Decimal('0'), Decimal('0'))
        else:
            controller.config = controller.config.model_copy(update={'cancel_retry_interval_ms': 101})
        release.set()
        assert isinstance(await asyncio.wait_for(completed, 2), PermissionError)
        assert sent == []
        assert (await store.snapshot(store.binding)).balance.reserved_quote_usdt == 2
        assert (await store.attempt(executor.config.id, store.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        release.set()
        await app.close()
        await store.close()


def filled_transport(request, sent, clock):
    import json
    at = int(NOW.timestamp() * 1000)
    wire = json.loads(sent[0]['data'])['clOrdId']
    fill = {'billId': '3', 'tradeId': 'trade-1', 'instType': 'SPOT', 'instId': 'LIFE-USDT',
            'ordId': '123456789', 'clOrdId': wire, 'fillSz': '2', 'fillPx': '1',
            'feeCcy': 'USDT', 'fee': '-.02', 'ts': str(at), 'side': 'buy'}
    path = request['url']
    if path.endswith(okx_constants.OKX_ORDER_DETAILS_PATH):
        return {'code': '0', 'data': [{'ordId': '123456789', 'clOrdId': wire, 'instType': 'SPOT',
                                      'instId': 'LIFE-USDT', 'state': 'filled', 'side': 'buy', 'tdMode': 'cash',
                                       'ordType': 'post_only', 'sz': '2', 'px': '1', 'accFillSz': '2'}]}
    if path.endswith(okx_constants.OKX_TRADE_FILLS_HISTORY_PATH):
        return {'code': '0', 'data': [fill]}
    if path.endswith(okx_constants.OKX_ACCOUNT_BILLS_ARCHIVE_PATH):
        common = {'type': '2', 'instId': 'LIFE-USDT', 'ordId': '123456789', 'tradeId': 'trade-1',
                  'clOrdId': wire, 'subType': '1', 'ts': str(at)}
        return {'code': '0', 'data': [{**common, 'billId': '3', 'ccy': 'USDT', 'fee': '-.02', 'balChg': '-2.02'},
                                      {**common, 'billId': '2', 'ccy': 'LIFE', 'fee': '0', 'balChg': '2'},
                                      {'billId': '1'}]}
    if path.endswith(okx_constants.OKX_BALANCE_PATH):
        return {'code': '0', 'data': [{'details': [{'ccy': 'LIFE', 'cashBal': '7', 'liab': '0'},
                                                   {'ccy': 'USDT', 'cashBal': '7.98', 'liab': '0'}]}]}
    return {'code': '0', 'data': []}


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', [False, True])
async def test_runner_fill_hint_is_verified_against_source_before_and_ledger_after_publication(tmp_path, conflict):
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.core.event.events import OrderFilledEvent
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    app, store, controller, executor, _, _ = await setup(tmp_path, observation_transport=filled_transport)
    app.install_observer(OkxSpotRepositoryObserver(
        app, independent_fill_value=lambda key, at: IndependentFillObservation(Decimal('.9'), at, at, 'independent_market'),
        independent_cashflow_value=lambda key, at: None, max_pages=2))
    try:
        executor.place_open_order()
        await app.drain()
        wire = (await store.attempt(executor.config.id, store.binding)).client_order_id
        # This isolated application has no service event queue. Native hint
        # helpers must be invoked on its repository worker; the configured
        # runner's loop callback is covered by the service/boundary tests.
        await store._call(controller.on_runner_order_filled, OrderFilledEvent(
            1, wire, 'LIFE-USDT', TradeType.BUY, OrderType.LIMIT_MAKER, Decimal('1'),
            Decimal('1') if conflict else Decimal('2'),
            AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.02'))]),
            exchange_trade_id='trade-1', exchange_order_id='123456789'))
        assert not await store._call(controller.verify_runner_fill_events)
        before = await store.snapshot(store.binding)
        if conflict:
            with pytest.raises(StorageError, match='SPOT_OBSERVATION_RUNNER_FILL_CONFLICT'):
                await app.reconcile(str(app.owner.session_id), app.owner.epoch)
            after = await store.snapshot(store.binding)
            assert (after.balance, after.budget) == (before.balance, before.budget)
        else:
            await app.reconcile(str(app.owner.session_id), app.owner.epoch)
            assert await store._call(controller.verify_runner_fill_events)
            assert (await store.attempt(executor.config.id, store.binding)).state.value == 'TERMINAL'
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_cold_application_recovers_original_fill_price_and_spending_without_resend(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    app, store, controller, executor, sent, clock = await setup(tmp_path, observation_transport=filled_transport)
    executor.place_open_order()
    await app.drain()
    at = int(NOW.timestamp() * 1000)
    prepared = app._issued[executor.config.id]
    fill = SpotFillObservation('trade-1', Decimal('2'), Decimal('1'), 'USDT', Decimal('-.02'), at,
                               Decimal('.9'), at, at, 'independent_market')
    command = FillSpot('original-fill', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                       'LIFE-USDT', 'BUY', prepared.session_id, prepared.epoch, '123456789', Decimal('2'), (fill,), NOW)
    original = store._ledger.apply_fills_snapshot

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('synthetic interruption after native money commit')
    monkeypatch.setattr(store._ledger, 'apply_fills_snapshot', fail)
    with pytest.raises(StorageError):
        await store.apply_fills(command, cut=(await store.snapshot(store.binding)).cut, owner=app.owner)
    settings = dict(binding=store.binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET, loss_policy=BUDGET,
                    config_version=1, account_lock_root=tmp_path / 'locks', clock=lambda: clock['now'],
                    fill_policy=store._fill_policy, accounting_policy=store._accounting_policy,
                    terminal_policy=store._terminal_policy, recovery_policy=store._recovery_policy)
    files, owner = store._files, app.owner
    await app.close()
    await store.close()
    cold = await open_legacy_spot(files, **settings)
    restored_controller = LifeLiquidityController(controller.config, MagicMock(), asyncio.Queue())
    restored = await FileSpotApplication.install(cold, restored_controller, app.connector, owner,
                                                 admission=app.admission, authorize_permit=app.authorize_permit,
                                                 risk_epoch=app.risk_epoch, max_pending=1,
                                                 ack_timeout_s=2, shutdown_timeout_s=.1)
    restored_controller._runner_orchestrator = controller._runner_orchestrator

    def no_reprice(*args):
        raise AssertionError('original pending valuation must be reused')
    restored.install_observer(OkxSpotRepositoryObserver(restored, independent_fill_value=no_reprice,
                                                        independent_cashflow_value=no_reprice, max_pages=2))
    try:
        pending = await cold.pending_spot_commands(cold.binding)
        assert len(pending) == 1 and pending[0].kind == 'FILL' and pending[0].command == command
        result = await restored.recover_pending()
        assert result[0].state == 'APPLIED'
        view = await cold.snapshot(cold.binding)
        assert view.balance.total_base_life == Decimal('7') and view.balance.total_quote_usdt == Decimal('7.98')
        assert view.budget.session_execution_loss_usdt == view.budget.session_subsidy_committed_usdt == Decimal('.22')
        assert (await cold.attempt(executor.config.id, cold.binding)).state.value == 'ACKED'
        assert await cold.pending_spot_commands(cold.binding) == ()
        assert cold._cold_unarmed and not restored.local_ready() and len(sent) == 1
    finally:
        await restored.close()
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
async def test_observer_recovers_later_activity_after_pending_write_without_resend(tmp_path, monkeypatch, kind):
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, SpotFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver

    def transport(request, sent, clock):
        body = filled_transport(request, sent, clock)
        path = request['url']
        if path.endswith(okx_constants.OKX_TRADE_FILLS_HISTORY_PATH):
            first = {**body['data'][0], 'fillSz': '1', 'fee': '-.01'}
            second = {**first, 'tradeId': 'trade-2', 'billId': '5'}
            body['data'] = [second, first]
        elif path.endswith(okx_constants.OKX_ACCOUNT_BILLS_ARCHIVE_PATH):
            quote, base, anchor = body['data']
            quote = {**quote, 'fee': '-.01', 'balChg': '-1.01'}
            base = {**base, 'balChg': '1'}
            body['data'] = [{**quote, 'tradeId': 'trade-2', 'billId': '5'},
                            {**base, 'tradeId': 'trade-2', 'billId': '4'}, quote, base, anchor]
        return body

    app, store, controller, executor, sent, clock = await setup(tmp_path, observation_transport=transport)
    executor.place_open_order()
    await app.drain()
    prepared = app._issued[executor.config.id]
    at = int(NOW.timestamp() * 1000)
    if kind == 'ACK':
        command = AckSpot('original-ack', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                          prepared.market, prepared.side, prepared.session_id, prepared.epoch, '123456789', NOW)
        monkeypatch.setattr(store._wal, 'acknowledge', lambda *args: (_ for _ in ()).throw(OSError('interrupted ACK')))
        with pytest.raises(CommitUnknown):
            await store.acknowledge(command, cut=(await store.snapshot(store.binding)).cut, owner=app.owner)
    else:
        fill = SpotFillObservation('trade-1', Decimal('1'), Decimal('1'), 'USDT', Decimal('-.01'), at,
                                   Decimal('.9'), at, at, 'independent_market')
        command = FillSpot('original-fill', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                           prepared.market, prepared.side, prepared.session_id, prepared.epoch, '123456789',
                           Decimal('1'), (fill,), NOW)
        original = store._ledger.apply_fills_snapshot

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('interrupted native fill')
        monkeypatch.setattr(store._ledger, 'apply_fills_snapshot', interrupted)
        with pytest.raises(CommitUnknown):
            await store.apply_fills(command, cut=(await store.snapshot(store.binding)).cut, owner=app.owner)
    settings = dict(binding=store.binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET, loss_policy=BUDGET,
                    config_version=1, account_lock_root=tmp_path / 'locks', clock=lambda: clock['now'],
                    fill_policy=store._fill_policy, accounting_policy=store._accounting_policy,
                    terminal_policy=store._terminal_policy, recovery_policy=store._recovery_policy)
    files, owner = store._files, app.owner
    await app.close()
    await store.close()
    cold = await open_legacy_spot(files, **settings)
    restored_controller = LifeLiquidityController(controller.config, MagicMock(), asyncio.Queue())
    restored = await FileSpotApplication.install(cold, restored_controller, app.connector, owner,
                                                 admission=app.admission, authorize_permit=app.authorize_permit,
                                                 risk_epoch=app.risk_epoch, max_pending=1,
                                                 ack_timeout_s=2, shutdown_timeout_s=.1)
    restored_controller._runner_orchestrator = controller._runner_orchestrator
    looked_up = []

    def value(key, at):
        looked_up.append(key)
        return IndependentFillObservation(Decimal('.8'), at, at, 'independent_market')
    restored.install_observer(OkxSpotRepositoryObserver(restored, independent_fill_value=value,
                                                        independent_cashflow_value=lambda *args: None, max_pages=2))
    try:
        receipts = await restored.recover_pending()
        view = await cold.snapshot(cold.binding)
        assert receipts[0].state == 'APPLIED' and await cold.pending_spot_commands(cold.binding) == ()
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (Decimal('7'), Decimal('7.98'))
        assert view.budget.session_execution_loss_usdt == Decimal('.42' if kind == 'ACK' else '.32')
        assert set(looked_up) == ({'trade-1', 'trade-2'} if kind == 'ACK' else {'trade-2'})
        assert (await cold.attempt(executor.config.id, cold.binding)).state.value == 'ACKED'
        assert cold._cold_unarmed and not restored.local_ready() and len(sent) == 1
    finally:
        await restored.close()
        await cold.close()


@pytest.mark.asyncio
async def test_control_application_requires_source_bound_signature_and_replay_cannot_rearm(tmp_path):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    app, store, controller, _, _, clock = await setup(tmp_path, observation_transport=empty_transport, operator_keys=KEYS)
    controller._runner_orchestrator.active_executors['life'] = []
    app.install_observer(OkxSpotRepositoryObserver(app, independent_fill_value=lambda key, at: None,
                                                   independent_cashflow_value=lambda key, at: None, max_pages=2))
    try:
        snapshot = await app.prepare_control_snapshot()
        assert snapshot.facts.runner_scope_complete and not app.local_ready()
        token = replace(app.owner, generation=UUID(int=91))
        command = OwnerTransition('activate-app', OwnerMode.ACTIVE, token)
        signature = KEYS.sign('offline-operator', store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                              issued_at=clock['now'], expires_at=clock['now'] + timedelta(seconds=1))
        with pytest.raises(StorageError, match='SOURCE_UNQUALIFIED'):
            await app.apply_owner_transition(command, approval=signature,
                                             snapshot=replace(snapshot, facts=replace(snapshot.facts, order_scope_complete=False)))
        receipt = await app.apply_owner_transition(command, approval=signature, snapshot=snapshot)
        assert not receipt.replayed and app.owner == app.runtime.owner == token and app.local_ready()
        assert (await app.apply_owner_transition(command, approval=signature, snapshot=snapshot)).replayed
        assert app.local_ready()  # Historical readback cannot close an existing fresh grant either.
        app.runtime.revoke()
        assert (await app.apply_owner_transition(command, approval=signature, snapshot=snapshot)).replayed
        assert not app.local_ready()  # A stored activation receipt cannot reopen local admission.
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_authenticated_fake_transport_fills_bills_wallet_and_terminal_use_repository(tmp_path):

    from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver

    app, store, controller, executor, sent, clock = await setup(tmp_path, observation_transport=filled_transport)
    observer = OkxSpotRepositoryObserver(app, independent_fill_value=lambda key, at: IndependentFillObservation(
        Decimal('.9'), at, at, 'independent_market'), independent_cashflow_value=lambda key, at: None, max_pages=2)
    app.install_observer(observer)
    try:
        executor.place_open_order()
        await app.drain()
        result = await app.reconcile(str(app.owner.session_id), app.owner.epoch)
        assert result.scope_complete and result.trade_events_reconciled
        view = await store.snapshot(store.binding)
        assert view.balance.total_base_life == Decimal('7')
        assert view.balance.total_quote_usdt == Decimal('7.98')
        assert view.balance.reserved_quote_usdt == 0
        assert view.budget.session_execution_loss_usdt == Decimal('.22')
        assert view.budget.session_subsidy_committed_usdt == Decimal('.22')
        assert (await store.attempt(executor.config.id, store.binding)).state.value == 'TERMINAL'
        before = view.balance, view.budget
        await app.reconcile(str(app.owner.session_id), app.owner.epoch)
        after = await store.snapshot(store.binding)
        assert (after.balance, after.budget) == before
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_stop_during_http_wait_retains_unknown_until_late_ack_is_accounted(tmp_path):
    delay = asyncio.Event()
    app, store, controller, executor, sent, _ = await setup(tmp_path, delay=delay)
    try:
        executor.place_open_order()
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(.01)
        assert len(sent) == 1
        app.revoke()
        delay.set()
        await app.drain()
        assert (await store.attempt('repository-quote', store.binding)).state.value == 'ACKED'
        assert (await store.snapshot(store.binding)).balance.reserved_quote_usdt == Decimal('2')
        assert not app.local_ready()
    finally:
        delay.set()
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wallet', 'bill_fee', 'bill_amount', 'anchor', 'foreign_order', 'algo',
                                   'fill_scope', 'order_payload', 'unqualified_value', 'runner'])
async def test_observation_faults_do_not_apply_money_or_release_holds(tmp_path, fault):
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver

    def transport(request, sent, clock):
        body = filled_transport(request, sent, clock)
        path = request['url']
        if fault == 'wallet' and path.endswith(okx_constants.OKX_BALANCE_PATH):
            body['data'][0]['details'][1]['cashBal'] = '999'
        if fault in ('bill_fee', 'bill_amount', 'anchor') and path.endswith(okx_constants.OKX_ACCOUNT_BILLS_ARCHIVE_PATH):
            if fault == 'bill_fee':
                body['data'][0]['fee'] = '-.03'
            elif fault == 'bill_amount':
                body['data'][0]['balChg'] = '-9'
            else:
                body['data'].pop()
        if fault == 'order_payload' and path.endswith(okx_constants.OKX_ORDER_DETAILS_PATH):
            body['data'][0]['px'] = '1.1'
        if fault == 'foreign_order' and path.endswith(okx_constants.OKX_ORDERS_PENDING_PATH):
            body['data'] = [{'ordId': '9', 'clOrdId': 'foreign', 'instType': 'SPOT', 'instId': 'LIFE-USDT', 'state': 'live'}]
        if fault == 'algo' and path.endswith(okx_constants.OKX_ORDERS_ALGO_PENDING_PATH):
            body['data'] = [{'algoId': '9'}]
        if fault == 'fill_scope' and path.endswith(okx_constants.OKX_TRADE_FILLS_HISTORY_PATH):
            body['data'][0]['ordId'] = '9'
        return body

    app, store, controller, executor, _, _ = await setup(tmp_path, observation_transport=transport)
    observer = OkxSpotRepositoryObserver(app, independent_fill_value=lambda key, at: IndependentFillObservation(
        Decimal('.9'), at, at, 'own_quote' if fault == 'unqualified_value' else 'independent_market'),
        independent_cashflow_value=lambda key, at: None, max_pages=2)
    app.install_observer(observer)
    try:
        executor.place_open_order()
        await app.drain()
        if fault == 'runner':
            controller._runner_orchestrator.active_executors['life'] = []
        before = await store.snapshot(store.binding)
        with pytest.raises((StorageError, ValueError)):
            await app.reconcile(str(app.owner.session_id), app.owner.epoch)
        after = await store.snapshot(store.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert (await store.attempt(executor.config.id, store.binding)).state.value == 'ACKED'
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'config', 'expiry'])
async def test_change_while_admission_worker_waits_prevents_any_preparation_or_http(tmp_path, change):
    import threading
    app, store, controller, executor, sent, clock = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = app.admission

    def admission(config):
        entered.set()
        assert release.wait(5)
        return original(config)
    app.admission = admission
    try:
        executor.place_open_order()
        assert await asyncio.to_thread(entered.wait, 3)
        if change == 'stop':
            controller.stop()
        elif change == 'config':
            controller.config_update_state.reject('CONFIG_LOAD_FAILED')
        else:
            clock['now'] = app.owner.expires_at
        release.set()
        with pytest.raises(PermissionError):
            await app.drain()
        assert sent == []
        assert await store.attempt(executor.config.id, store.binding) is None
        assert (await store.snapshot(store.binding)).balance.reserved_quote_usdt == 0
    finally:
        release.set()
        await app.close()
        await store.close()
