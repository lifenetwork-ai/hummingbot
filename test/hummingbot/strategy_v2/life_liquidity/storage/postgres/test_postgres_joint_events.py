"""Actual dual SDK PubSub, native accounting, reporting and qualified history."""
import asyncio
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_history import close, setup

import pytest

from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import (
    BuyOrderCreatedEvent,
    FundingPaymentCompletedEvent,
    MarketEvent,
    OrderFilledEvent,
    SellOrderCreatedEvent,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def sdk_events(s, product):
    at = s.t.journal.clock().timestamp()
    swap = product == 'SWAP'
    wire, exchange, leverage = ('SwapWire1', '987654321', 2) if swap else ('wire1', '123456789', 1)
    cls = SellOrderCreatedEvent if swap else BuyOrderCreatedEvent
    created = cls(at, OrderType.LIMIT_MAKER, 'LIFE-USDT', D('1') if swap else D('2'), D('1'), wire, at,
                  exchange_order_id=exchange, leverage=leverage, position='OPEN')
    fill = OrderFilledEvent(
        at, wire, 'LIFE-USDT', TradeType.SELL if swap else TradeType.BUY,
        OrderType.LIMIT_MAKER, D('1.01') if swap else D('1'), D('1'),
        AddedToCostTradeFee(
            percent=D('0'), percent_token='USDT',
            flat_fees=[TokenAmount('USDT', D('.01') if swap else D('.001'))]),
        exchange_trade_id='trade1', exchange_order_id=exchange, leverage=leverage, position='OPEN')
    return created, fill


def emit(s, product, *, fill_changes=None):
    created, fill = sdk_events(s, product)
    connector = s.t.swap_connector if product == 'SWAP' else s.t.connector
    connector.trigger_event(MarketEvent.SellOrderCreated if product == 'SWAP' else MarketEvent.BuyOrderCreated, created)
    connector.trigger_event(MarketEvent.OrderFilled, fill._replace(**(fill_changes or {})))


@pytest.mark.asyncio
async def test_both_actual_sdk_callbacks_use_signed_atomic_money_and_product_scoped_history(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_event_application import PgJointEventApplication
    s = await setup(database, apply_finance=False)
    try:
        app = await PgJointEventApplication.install(s.reporting, s.intake)
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        claims = await s.t.values[5].claims(s.t.cfg.binding)
        assert before.balance.total_quote_usdt == D('10.889')
        emit(s, 'SPOT')
        emit(s, 'SWAP')
        assert s.t.financial._runtime._observation_inflight and not s.t.financial._runtime.healthy
        assert not s.t.connector._current_trade_fills and not s.t.swap_connector._current_trade_fills
        await app.drain()
        for connector, market in zip((s.t.connector, s.t.swap_connector), ('okx', 'okx_perpetual')):
            assert connector._current_trade_fills == {TradeFillOrderDetails(market, 'trade1', 'LIFE-USDT')}
            assert connector.in_flight_orders == {}
        assert s.t.connector._exchange_order_ids == {'123456789': 'wire1'}
        assert s.t.swap_connector._exchange_order_ids == {'987654321': 'SwapWire1'}
        assert (await s.t.financial.snapshot(s.t.cfg.binding)).balance == before.balance
        assert await s.t.values[5].claims(s.t.cfg.binding) == claims
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 2
        assert not s.t.session.sent and not s.revoked
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'trade', 'credentials', 'controller', 'backpressure'])
async def test_joint_callback_fault_revokes_without_poisoning_either_sdk_or_inventing_money(database, fault):
    s = await setup(database)
    try:
        app = await s.reporting.install_joint_events(s.intake)
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        changes = {'exchange_trade_id': 'foreign'} if fault == 'trade' else {}
        if fault == 'fee':
            changes['trade_fee'] = AddedToCostTradeFee(percent=D('0'), percent_token='USDT', flat_fees=[TokenAmount('USDT', D('.02'))])
        elif fault == 'credentials':
            s.t.swap_connector.okx_perpetual_api_key = 'foreign'
        elif fault == 'controller':
            s.controller.config = s.controller.config.model_copy(update={'id': 'foreign'})
        elif fault == 'backpressure':
            app._callbacks = s.reporting.max_callbacks
        emit(s, 'SWAP', fill_changes=changes)
        with pytest.raises(StorageError):
            await app.drain()
        assert s.revoked and not s.t.financial._runtime.healthy
        for connector in (s.t.connector, s.t.swap_connector):
            assert not connector._current_trade_fills and not connector._exchange_order_ids
        assert (await s.t.financial.snapshot(s.t.cfg.binding)).balance == before.balance
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_joint_funding_callback_accounts_only_new_signed_bill_and_never_pays_it_twice(database):
    s = await setup(database)
    try:
        app = await s.reporting.install_joint_events(s.intake)
        at = int(s.t.journal.clock().timestamp() * 1000)
        s.t.bills.insert(0, dict(billId='880', instType='SWAP', instId='LIFE-USDT-SWAP', ordId='', tradeId='', type='8', subType='173',
                                 ccy='USDT', balChg='-.2', pnl='-.2', fee='0', ts=str(at)))
        s.t.balance['details'][1].update(cashBal='10.689', eq='10.699', uTime=str(at))
        s.t.balance['uTime'] = str(at)
        s.t.public_received_at = None
        funding = FundingPaymentCompletedEvent(at, 'okx_perpetual', 'LIFE-USDT', D('-.2'), D('.001'))
        s.t.swap_connector.trigger_event(MarketEvent.FundingPaymentCompleted, funding)
        await app.drain()
        assert (await s.t.financial.balance(s.t.cfg.binding)).total_quote_usdt == D('10.689')
        s.t.swap_connector.trigger_event(MarketEvent.FundingPaymentCompleted, funding)
        await app.drain()
        assert (await s.t.financial.balance(s.t.cfg.binding)).total_quote_usdt == D('10.689')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 2
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_stop_during_owned_financial_collection_joins_late_money_but_publishes_no_sdk_ids(database, monkeypatch):
    s = await setup(database)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        app = await s.reporting.install_joint_events(s.intake)
        original = s.intake.observe

        at = int(s.t.journal.clock().timestamp() * 1000)
        s.t.bills.insert(0, dict(billId='881', instType='SWAP', instId='LIFE-USDT-SWAP', ordId='', tradeId='', type='8', subType='173',
                                 ccy='USDT', balChg='-.2', pnl='-.2', fee='0', ts=str(at)))
        s.t.balance['details'][1].update(cashBal='10.689', eq='10.699', uTime=str(at))
        s.t.balance['uTime'] = str(at)
        s.t.public_received_at = None

        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.intake, 'observe', held)
        emit(s, 'SPOT')
        emit(s, 'SWAP')
        await asyncio.wait_for(entered.wait(), 4)
        task = asyncio.create_task(app.close())
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await task
        assert not app._registered and not s.t.financial._runtime.healthy
        assert not s.t.connector._current_trade_fills and not s.t.swap_connector._current_trade_fills
        assert (await s.t.financial.balance(s.t.cfg.binding)).total_quote_usdt == D('10.689')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 2
        assert not s.t.session.sent
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await close(s)


@pytest.mark.asyncio
async def test_partial_listener_install_failure_removes_both_product_registrations(database, monkeypatch):
    s = await setup(database)
    original = s.t.swap_connector.add_listener

    def failed(tag, listener):
        original(tag, listener)
        raise StorageError('SYNTHETIC_LISTENER_FAILURE')
    monkeypatch.setattr(s.t.swap_connector, 'add_listener', failed)
    try:
        with pytest.raises(StorageError, match='SYNTHETIC_LISTENER_FAILURE'):
            await s.reporting.install_joint_events(s.intake)
        app = s.reporting._event_application
        assert app._closed and not app._registered and s.revoked
        for connector in (s.t.connector, s.t.swap_connector):
            for tag in app._tags(connector):
                assert app._forwarder not in connector.get_listeners(tag)
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_actual_v2_joint_startup_uses_both_owned_sources_and_still_denies_trading(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_runner_startup import stop

    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    s = await setup(database)
    runner = None
    try:
        connectors = {'okx': s.t.connector, 'okx_perpetual': s.t.swap_connector}
        s.controller.actions_queue = asyncio.Queue()
        s.controller.market_data_provider = MarketDataProvider(connectors)
        monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: (_ for _ in ()).throw(AssertionError('legacy recorder used')))
        await s.reporting.install_joint_events(s.intake)
        runner = StrategyV2Base(connectors, repository_reporting={'main': s.reporting}, dispatch_max_pending=2, dispatch_shutdown_timeout_s=1)
        assert runner.controllers == {'main': s.controller}
        assert runner.markets == {'okx': {'LIFE-USDT'}, 'okx_perpetual': {'LIFE-USDT'}}
        assert not runner.ready_to_trade
        assert runner.executor_orchestrator.active_executors == {'main': []}
        with pytest.raises(ValueError, match='PG_RUNNER_TRADING_ASSEMBLY_REQUIRED'):
            s.reporting.assert_executor_creation(None)
        assert not s.t.session.sent
    finally:
        await stop(runner)
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['listener', 'missing_product', 'foreign_connector', 'closed', 'credentials'])
async def test_joint_runner_refuses_incomplete_product_history_without_creating_tasks(database, fault):
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    s = await setup(database)
    try:
        connectors = {'okx': s.t.connector, 'okx_perpetual': s.t.swap_connector}
        s.controller.actions_queue = asyncio.Queue()
        s.controller.market_data_provider = MarketDataProvider(connectors)
        app = await s.reporting.install_joint_events(s.intake)
        if fault == 'listener':
            s.t.swap_connector.remove_listener(MarketEvent.OrderFilled, app._forwarder)
        elif fault == 'missing_product':
            del connectors['okx_perpetual']
        elif fault == 'foreign_connector':
            connectors['okx_perpetual'] = object()
        elif fault == 'closed':
            await app.close()
        else:
            s.t.swap_connector.okx_perpetual_api_key = 'foreign'
        tasks = set(asyncio.all_tasks())
        with pytest.raises(ValueError, match='REPOSITORY_RUNNER_STARTUP_INVALID'):
            StrategyV2Base(connectors, repository_reporting={'main': s.reporting}, dispatch_max_pending=2, dispatch_shutdown_timeout_s=1)
        assert not (set(asyncio.all_tasks()) - tasks) and not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_cold_database_runtime_and_listener_reinstall_reconcile_original_both_product_history(database):
    from datetime import datetime, timezone

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import (
        PgObservationAccountSource,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import PgSwapBoundary
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource
    s = await setup(database)
    cold = reporting = None
    try:
        app = await s.reporting.install_joint_events(s.intake)
        emit(s, 'SPOT')
        emit(s, 'SWAP')
        await app.drain()
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        claims = await s.t.values[5].claims(s.t.cfg.binding)
        await s.reporting.close()
        await s.t.financial.close()
        cold = PgSpotRepository(await PgRuntime.open(s.t.cfg))
        capital = PgSharedCapitalRepository(cold, owner=s.t.owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        carry = PgCarryRepository(capital, observation=lambda: None, max_rows=64, max_bytes=1048576)
        swap = PgSwapRepository(capital)
        journal = PgQuoteRunnerJournal(cold, owner=s.t.owner, controller_id='main', config_version=1,
                                       runner_facts=s.t.journal.runner_facts, local_ready=lambda: False,
                                       clock=lambda: datetime.now(timezone.utc), max_actions=64)
        account = await PgObservationAccountSource.create(journal, connector=s.t.connector, max_uid_age_ms=1000,
                                                          max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                          max_pending=2, collection_timeout_ms=1000)
        reader = PgSpotAccountReader(account, max_reads=128, max_bytes=4194304, timeout_ms=4000)
        collector = PgJointAccountReconciler(reader, swap, max_pages=2, max_age_ms=1000)
        sender = await PgSwapAccountSource.create(
            PgSwapBoundary(swap), connector=s.t.swap_connector,
            clock=lambda: datetime.now(timezone.utc), local_fence=lambda: False,
            max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
            collection_timeout_ms=1000, max_pending=1)
        source = PgHedgeSource(collector, carry=carry, swap_source=sender, max_levels=8, timeout_ms=5000)

        def no_reprice(*args):
            pytest.fail('Cold history cannot choose new values for original monetary facts')
        intake = PgJointFinancialObserver(source, independent_fill_value=no_reprice, independent_cashflow_value=no_reprice)
        reporting = await PgRecorderApplication.install(
            cold, controller=s.controller, owner=s.t.owner, sources=s.registered, revoke=lambda: s.revoked.append(True),
            local_ready=lambda: False, max_pending=4, max_callbacks=16, shutdown_timeout_s=2,
            max_rows=64, max_read_bytes=1048576)
        await reporting.install_joint_events(intake)
        for connector, market in zip((s.t.connector, s.t.swap_connector), ('okx', 'okx_perpetual')):
            assert connector._current_trade_fills == {TradeFillOrderDetails(market, 'trade1', 'LIFE-USDT')}
            assert connector.in_flight_orders == {}
        after = await cold.snapshot(s.t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert await capital.claims(s.t.cfg.binding) == claims
        assert not s.t.session.sent
    finally:
        if reporting is not None:
            await reporting.close()
        if cold is not None:
            await cold.close()
        await close(s)
