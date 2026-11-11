"""Crash boundaries use the real recorder, PostgreSQL fences and V2 accounting."""
import asyncio
import json
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as owned_fixture
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy import select, text

from hummingbot.core.data_type.common import TradeType
from hummingbot.model.executors import Executors
from hummingbot.model.position import Position
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

owned = owned_fixture


def test_attempt_is_durable_before_transport_and_contains_no_auth_headers(owned):
    from test.integration.postgres.test_ownership import assistant, intent, send

    from hummingbot.model.runtime_journal import SubmissionAttempt, digest
    order = intent(owned)
    rest, transport = assistant()

    async def receipt(request):
        with owned.engine.connect() as connection:
            attempt = connection.execute(select(SubmissionAttempt)).mappings().one()
        assert attempt['state'] == 'possibly_sent'
        assert attempt['payload_hash'] == digest(__import__('json').loads(request.data))
        assert attempt['deadline_ms'] == int(request.headers['expTime'])
        assert attempt['context']['order']['client_order_id'] == order.client_order_id
        assert 'headers' not in attempt['context']
        return transport.call.return_value

    transport.call.side_effect = receipt
    owned.loop.run_until_complete(send(rest))


@pytest.fixture
def managed_native(owned):
    from test.integration.postgres.test_ownership import assistant

    from bidict import bidict

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.data_type.common import OrderType
    from hummingbot.core.order_submission_guard import session_order_scope
    session = dict(id='mm-auto', generation=0, status='running', started_at=time.time() - 1,
                   expires_at=time.time() + 120, step_expires_at=time.time() + 120)
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:value AS jsonb)"),
                           {'value': json.dumps(session)})
    owned.owner.mm_ready_check = lambda: True
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                           trading_pairs=['BTC-USDT'])
    exchange._set_current_timestamp(1700000000)
    exchange._set_trading_pair_symbol_map(bidict({'BTC-USDT': 'BTC-USDT'}))
    exchange._trading_rules['BTC-USDT'] = TradingRule('BTC-USDT', min_order_size=Decimal('.00001'),
                                                      min_price_increment=Decimal('.1'),
                                                      min_base_amount_increment=Decimal('.00001'))
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    owned.owner.connectors = {'okx': exchange}
    assert recorder.ensure_runtime_checkpoint()
    config = executor_info('auto').config.model_copy(update={'mm_session_id': 'mm-auto', 'mm_generation': 0})
    rest, transport = assistant()
    response = transport.call.return_value
    rest._auth = exchange.authenticator
    response.status = 429
    response.text = AsyncMock(return_value='{"code":"50013","msg":"busy"}')
    response.json = AsyncMock(return_value={'code': '50013', 'data': []})

    async def create():
        with session_order_scope('mm-auto', 0, executor=config.model_dump(mode='json')):
            await exchange._create_order(TradeType.BUY, 'auto-order', 'BTC-USDT', Decimal('.1'),
                                         OrderType.LIMIT_MAKER, Decimal('100'))
        await asyncio.sleep(0)

    with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
        owned.loop.run_until_complete(create())
        yield owned, exchange, recorder, transport
    recorder.stop()
    owned.loop.run_until_complete(exchange._web_assistants_factory.close())


@pytest.mark.parametrize('state', ['OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED'])
def test_verified_unknown_send_recovers_automatically_without_a_second_post(managed_native, state):
    from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
    from hummingbot.model.order import Order
    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, transport = managed_native
    recovery = context.owner.automatic_recovery
    assert isinstance(recovery, AutomaticOrderRecovery)
    assert not context.store.read()['recovery_state'].get('halt')
    assert recovery.active and not context.owner.allow_new_orders
    amount = Decimal('.04') if state == 'PARTIALLY_FILLED' else Decimal('.1') if state == 'FILLED' else Decimal(0)
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy',
                  ordType='post_only', px='100', sz='.1', accFillSz=str(amount), uTime='1700000001000',
                  state={'OPEN': 'live', 'PARTIALLY_FILLED': 'partially_filled', 'FILLED': 'filled', 'CANCELED': 'canceled'}[state])
    trades = []
    if state in ('PARTIALLY_FILLED', 'FILLED'):
        trades = [dict(tradeId='fill-auto', ordId='venue-auto', instId='BTC-USDT', side='buy',
                       fillSz=str(amount), fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')]
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': trades})):
        context.loop.run_until_complete(recovery.step(core, force=True))
    assert not recovery.active and context.owner.allow_new_orders
    assert not context.store.read()['recovery_state'].get('halt')
    with context.manager.get_new_session() as db:
        saved = db.get(Order, 'auto-order')
        assert not saved.creation_pending and saved.exchange_order_id == 'venue-auto'
        assert db.query(TradeFill).count() == len(trades)
        from hummingbot.model.admin_access import AdminAuditEvent
        assert db.query(AdminAuditEvent).filter_by(action='automatic_recovery_resumed').count() == 1
    assert transport.call.await_count == 1


def test_repeated_not_found_does_not_retire_an_uncertain_native_order(managed_native):
    from hummingbot.connector.okx_order_status import OKXOrderNotFoundError
    from hummingbot.core.data_type.in_flight_order import OrderState
    context, exchange, recorder, transport = managed_native

    async def observe():
        for _ in range(10):
            await exchange._order_tracker.process_order_not_found('auto-order')
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))):
            await context.owner.automatic_recovery.step(SimpleNamespace(markets_recorder=recorder,
                                                                        trade_fill_db=context.manager), force=True)
    context.loop.run_until_complete(observe())
    assert exchange.in_flight_orders['auto-order'].current_state == OrderState.PENDING_CREATE
    assert context.owner.automatic_recovery.active and not context.owner.allow_new_orders
    assert transport.call.await_count == 1


def test_crash_after_send_recovers_from_new_journal_without_lifetime_replay(managed_native):
    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.core.order_submission_guard import install_guard
    from hummingbot.model.runtime_journal import RuntimeJournal
    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, transport = managed_native
    context.owner.release()  # Same durable boundary as the physical process lock disappearing.
    successor = BotOwnership(context.engine, 'hummingbot')
    assert successor.try_acquire()
    try:
        install_guard(successor)
        assert not context.store.read()['recovery_state'].get('halt')
        successor.prepare(1, 'test.yml')
        successor.connectors = {'okx': exchange}
        strategy = orchestrator()
        core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager,
                               strategy=SimpleNamespace(executor_orchestrator=strategy))
        status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                      px='100', sz='.1', accFillSz='.1', uTime='1700000001000', state='filled')
        trade = dict(tradeId='fill-auto', ordId='venue-auto', instId='BTC-USDT', side='buy',
                     fillSz='.1', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')
        with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [trade]})):
            context.loop.run_until_complete(successor.automatic_recovery.restore(core))
            context.loop.run_until_complete(successor.automatic_recovery.project(core))
            context.loop.run_until_complete(successor.automatic_recovery.project(core))
        held = recorder.get_all_positions()
        assert len(held) == 1 and held[0].amount == Decimal('.1') and held[0].cum_fees_quote == Decimal('.01')
        with context.manager.get_new_session() as db:
            assert db.query(TradeFill).count() == 1
            assert db.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
        assert transport.call.await_count == 1
    finally:
        successor.release()
        install_guard(context.owner)


def test_incomplete_native_hold_cannot_consume_a_larger_durable_fill(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate, TradeUpdate
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.model.runtime_journal import RuntimeJournal
    context, exchange, recorder, transport = managed_native
    order = exchange.in_flight_orders['auto-order']
    order.update_exchange_order_id('venue-auto')
    trade = TradeUpdate('fill-auto', 'auto-order', 'venue-auto', 'BTC-USDT', 1700000001,
                        Decimal(100), Decimal('.1'), Decimal(10),
                        AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))]))

    async def fill():
        exchange._order_tracker.process_trade_update(trade)
        await exchange._order_tracker.process_order_update(OrderUpdate('BTC-USDT', 1700000001,
                                                                       OrderState.FILLED, 'auto-order', 'venue-auto'))
    context.loop.run_until_complete(fill())
    runtime = orchestrator()
    info = executor_info('auto', amount='.05')
    runtime.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    with pytest.raises(ValueError, match='[Ff]ill|[Cc]overage'):
        runtime._update_positions_from_done_executors({'maker': [info]})
    assert recorder.get_all_positions() == []
    with context.manager.get_new_session() as db:
        assert db.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 1
    assert runtime.positions_held['maker'] == []


@pytest.mark.parametrize('interruption', ['stop', 'risk_halt', 'owner_loss'])
def test_newer_authority_wins_over_a_successful_delayed_proof(managed_native, interruption):
    from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
    context, exchange, recorder, transport = managed_native
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='live')
    successor = None

    async def scenario():
        nonlocal successor
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await finish.wait()
            return {'code': '0', 'data': [status]}
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=delayed)), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': []})):
            task = asyncio.create_task(context.owner.automatic_recovery.step(
                SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager), force=True))
            await asyncio.wait_for(entered.wait(), 2)
            if interruption == 'stop':
                with context.engine.begin() as connection:
                    connection.execute(text("UPDATE bot_state SET desired_state='stopped', control_version=control_version+1"))
            elif interruption == 'risk_halt':
                context.owner.require_recovery('drawdown_controller_limit')
            else:
                with context.engine.begin() as connection:
                    connection.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': context.owner.backend_pid})
                successor = BotOwnership(context.engine, 'hummingbot')
                assert successor.try_acquire()
            finish.set()
            if interruption == 'owner_loss':
                with pytest.raises(OwnershipLost):
                    await task
            else:
                await task
    try:
        context.loop.run_until_complete(scenario())
        assert not context.owner.allow_new_orders
        state = context.store.read()
        if interruption == 'stop':
            assert state['desired_state'] == 'stopped' and state['control_version'] == 2
            assert not state['recovery_state'].get('halt')
        elif interruption == 'risk_halt':
            assert state['recovery_state']['halt'] and state['recovery_state']['reason'] == 'drawdown_controller_limit'
        else:
            assert state['owner_uuid'] == successor.owner_uuid
        assert transport.call.await_count == 1
    finally:
        if successor is not None:
            successor.release()


def test_verified_stopped_attempt_allows_a_later_explicit_start_with_fresh_scope(managed_native):
    context, exchange, recorder, transport = managed_native
    previous = context.owner.automatic_recovery
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='canceled')
    core = SimpleNamespace(strategy=SimpleNamespace(config=None, controllers={}), trade_fill_db=context.manager,
                           markets_recorder=recorder, connector_manager=SimpleNamespace(connectors={'okx': exchange}))
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET desired_state='stopped', control_version=2"))
    with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': []})):
        context.loop.run_until_complete(previous.step(core, force=True))
    assert previous.status['phase'] == 'settled_stopped' and not context.owner.allow_new_orders
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET desired_state='running', control_version=3, "
                                "mm_session=jsonb_set(mm_session,'{id}','\"new-session\"')"))
    context.owner.version = 3
    with patch.object(type(exchange), 'ready', PropertyMock(return_value=True)), \
            patch.object(context.owner, '_open_orders', AsyncMock(return_value=[])):
        context.loop.run_until_complete(context.owner.reconcile(core))
    assert context.owner.automatic_recovery is not previous
    assert not context.owner.automatic_recovery.active and context.owner.automatic_recovery.resume_scope is None
    assert context.owner.allow_new_orders and not context.store.read()['recovery_state'].get('halt')
    context.owner._check_mm_authority(context.store.read(), ('new-session', 0))
    assert transport.call.await_count == 1


def test_large_lifetime_history_does_not_enter_incremental_handover_scope(managed_native):
    from hummingbot.model.runtime_journal import can_bootstrap
    context, exchange, recorder, transport = managed_native
    with context.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO orders (id, config_file_path, strategy, market, symbol, base_asset, quote_asset,
              creation_timestamp, order_type, amount, leverage, price, last_status, last_update_timestamp, creation_pending)
            SELECT 'old-' || n, 'test.yml', 'pure_market_making', 'okx', 'BTC-USDT', 'BTC', 'USDT',
              1600000000000, 'LIMIT_MAKER', .1, 1, 100, 'OrderCancelled', 1600000001000, false
            FROM generate_series(1, 10000) n
        """))
    with context.manager.get_new_session() as session:
        assert can_bootstrap(session, context.store.read())
    pending = context.owner.automatic_recovery._bootstrap_pending(SimpleNamespace(trade_fill_db=context.manager))
    assert [row['order_id'] for row in pending] == ['auto-order']


def test_lost_cancel_response_reconciles_the_same_native_order_without_manual_halt(managed_native):
    context, exchange, recorder, transport = managed_native
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='live')
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=lambda *a, **k: {'code': '0', 'data': [dict(status)]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': []})):
        context.loop.run_until_complete(context.owner.automatic_recovery.step(core, force=True))
        assert context.owner.allow_new_orders
        order = exchange.in_flight_orders['auto-order']
        with patch.object(exchange, '_place_cancel', AsyncMock(side_effect=asyncio.TimeoutError)) as cancel:
            context.loop.run_until_complete(exchange._execute_order_cancel(order))
        assert context.owner.automatic_recovery.active and not context.owner.allow_new_orders
        assert not context.store.read()['recovery_state'].get('halt')
        status['state'] = 'canceled'
        context.loop.run_until_complete(context.owner.automatic_recovery.step(core, force=True))
        assert context.owner.allow_new_orders and not context.owner.automatic_recovery.active
        assert cancel.await_count == 1 and transport.call.await_count == 1


def test_cancel_ack_does_not_retire_a_managed_order_before_terminal_fill_proof(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState
    context, exchange, recorder, transport = managed_native
    order = exchange.in_flight_orders['auto-order']
    with patch.object(exchange, '_place_cancel', AsyncMock(return_value=True)), patch.object(
            type(exchange), 'is_cancel_request_in_exchange_synchronous', new_callable=PropertyMock,
            return_value=True):
        context.loop.run_until_complete(exchange._execute_order_cancel_and_process_update(order))
        context.loop.run_until_complete(asyncio.sleep(0))
    assert order.current_state == OrderState.PENDING_CANCEL
    assert 'auto-order' in exchange.in_flight_orders


def test_bounded_admission_backpressure_clears_after_a_durable_ack_without_halt(owned):
    from test.integration.postgres.test_ownership import intent
    from test.integration.postgres.test_recorder import create

    from hummingbot.core.automatic_recovery import MAX_ATTEMPTS
    from hummingbot.core.event.events import MarketEvent
    from hummingbot.core.order_submission_guard import MarketReadinessBlocked
    from hummingbot.model.order import Order
    for index in range(MAX_ATTEMPTS):
        intent(owned, 'queued-' + str(index))
    with pytest.raises(MarketReadinessBlocked):
        intent(owned, 'overflow')
    assert owned.owner.allow_new_orders and not owned.store.read()['recovery_state'].get('halt')
    with owned.manager.get_new_session() as session:
        assert session.query(Order).count() == MAX_ATTEMPTS
    event = create()
    event.order_id = 'queued-0'
    event.price = Decimal('100')
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, event)
    intent(owned, 'after-ack')
    assert not owned.store.read()['recovery_state'].get('halt')


def test_environment_change_cannot_inherit_the_old_automatic_checkpoint(managed_native, monkeypatch):
    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.model.runtime_journal import environment_identity
    context, exchange, recorder, transport = managed_native
    old_mode = environment_identity()['mode']
    context.owner.release()
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo' if old_mode == 'live' else 'live')
    successor = BotOwnership(context.engine, 'hummingbot')
    assert successor.try_acquire()
    try:
        assert successor.check_owner()['recovery_state']['halt']
        assert not successor.allow_new_orders
    finally:
        successor.release()


def test_positive_proof_repairs_a_pubsub_failure_before_the_recorder_transaction(managed_native):
    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, transport = managed_native
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='.1', uTime='1700000001000', state='filled')
    trade = dict(tradeId='fill-auto', ordId='venue-auto', instId='BTC-USDT', side='buy',
                 fillSz='.1', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')
    with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [trade]})), \
            patch.object(recorder._fill_order_forwarder, '_to_function', side_effect=ValueError('before-session fault')):
        context.loop.run_until_complete(context.owner.automatic_recovery.step(
            SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager), force=True))
    with context.manager.get_new_session() as session:
        saved = session.query(TradeFill).one()
        assert saved.amount == Decimal('.1') and saved.trade_fee['flat_fees'][0]['amount'] == '0.01'
    assert context.manager.persistence_healthy and context.owner.allow_new_orders
    assert not context.store.read()['recovery_state'].get('halt') and transport.call.await_count == 1


def executor_info(identity, side='BUY', amount='.1', price='100', fee='.01'):
    config = PositionExecutorConfig(id=identity, timestamp=1700000000, connector_name='okx',
                                    trading_pair='BTC-USDT', side=TradeType[side],
                                    amount=Decimal(amount), entry_price=Decimal(price), controller_id='maker')
    return ExecutorInfo(id=identity, timestamp=config.timestamp, type=config.type, status=RunnableStatus.TERMINATED,
                        close_type=CloseType.POSITION_HOLD, close_timestamp=1700000001000, config=config,
                        net_pnl_pct=Decimal(0), net_pnl_quote=Decimal(0), cum_fees_quote=Decimal(fee),
                        filled_amount_quote=Decimal(amount) * Decimal(price), is_active=False, is_trading=False,
                        controller_id='maker', custom_info={'held_position_orders': [{
                            'client_order_id': identity + '-order', 'trade_type': side,
                            'executed_amount_base': amount,
                            'executed_amount_quote': str(Decimal(amount) * Decimal(price)),
                            'cumulative_fee_paid_quote': fee}]})


def orchestrator():
    strategy = SimpleNamespace(controllers={'maker': object()}, markets={'okx': {'BTC-USDT'}},
                               connectors={}, current_timestamp=1700000002, market_data_provider=MagicMock(),
                               config=SimpleNamespace(mm_drawdown_scope={
                                   'maker': SimpleNamespace(connector_name='okx', trading_pair='BTC-USDT')}))
    strategy.market_data_provider.get_price_by_type.return_value = Decimal(110)
    return ExecutorOrchestrator(strategy)


@pytest.mark.parametrize('drawdown', [True, False])
def test_held_transfer_is_durable_before_executor_can_be_retired(owned, drawdown):
    runtime = orchestrator()
    info = executor_info('first')
    if not drawdown:
        runtime.strategy.config.mm_drawdown_scope = None
        info.config.mm_session_id = 'managed-without-drawdown'
        info.config.mm_generation = 0
        from test.integration.postgres.test_recorder import fill

        from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
        from hummingbot.core.event.events import MarketEvent
        owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market,
                                       fill(order_id='first-order', amount=Decimal('.1'), price=Decimal('100'),
                                            trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))])))
    runtime.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    runtime._update_positions_from_done_executors({'maker': [info]})
    # Simulate process loss here: neither explicit Stop nor store_all_positions ran.
    with owned.engine.connect() as connection:
        position = connection.execute(select(Position)).mappings().one()
        executor = connection.execute(select(Executors)).mappings().one()
    assert position['amount'] == Decimal('.1')
    assert position['cum_fees_quote'] == Decimal('.01')
    assert executor['id'] == 'first'
    restarted = orchestrator()
    assert restarted.positions_held['maker'][0].net_amount_base == Decimal('.1')
    # A repeated terminal event after a commit whose acknowledgement was lost must
    # not add its quantity/fees to the already-restored hold a second time.
    restarted.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    restarted._update_positions_from_done_executors({'maker': [info]})
    assert restarted.positions_held['maker'][0].net_amount_base == Decimal('.1')


@pytest.mark.parametrize('missing', ['fill', 'order'])
def test_managed_hold_cannot_certify_ram_execution_without_durable_financial_evidence(managed_native, missing):
    from hummingbot.model.order import Order
    from hummingbot.model.runtime_journal import RuntimeJournal
    context, _, recorder, _ = managed_native
    runtime = orchestrator()
    info = executor_info('auto' if missing == 'fill' else 'missing')
    info.config.mm_session_id, info.config.mm_generation = 'mm-auto', 0
    runtime.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    # PubSub can update native RAM before a recorder callback fails. A healthy
    # persistence latch alone cannot certify this nonzero native execution.
    assert context.manager.persistence_healthy
    with pytest.raises(ValueError, match='[Ff]ill coverage|[Dd]urable order'):
        runtime._update_positions_from_done_executors({'maker': [info]})
    assert not recorder.get_all_positions() and not runtime.positions_held['maker']
    with context.manager.get_new_session() as db:
        assert not db.get(Order, 'auto-order').position_applied
        assert not db.query(RuntimeJournal).filter_by(kind='hold').first()


def test_native_transfer_order_preserves_basis_across_a_crash(owned):
    runtime = orchestrator()
    infos = [executor_info('buy-a', amount='1', price='100'),
             executor_info('sell', side='SELL', amount='.5', price='110'),
             executor_info('buy-b', amount='.5', price='120')]
    runtime.active_executors['maker'] = [SimpleNamespace(config=i.config, executor_info=i) for i in infos]
    for info in infos:
        runtime._update_positions_from_done_executors({'maker': [info]})
    restarted = orchestrator()
    held = restarted.positions_held['maker'][0]
    assert held.net_amount_base == Decimal('1')
    assert held.avg_entry_price == Decimal('110')
    assert held.realized_pnl_quote == Decimal('5')
    assert held.cum_fees_quote == Decimal('.03')


def test_cancel_terminal_waits_for_the_complete_fill_set_then_persists_before_retiring(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, transport = managed_native
    order = exchange.in_flight_orders['auto-order']
    raw = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy',
               ordType='post_only', px='100', sz='.1', accFillSz='.04', uTime='1700000001000', state='canceled')
    fill = dict(tradeId='cancel-fill', ordId='venue-auto', instId='BTC-USDT', side='buy',
                fillSz='.04', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')
    terminal = OrderUpdate('BTC-USDT', 1700000001, OrderState.CANCELED, 'auto-order', 'venue-auto', {'venue_order': raw})
    with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [raw]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': []})):
        context.loop.run_until_complete(exchange._order_tracker.process_order_update(terminal))
    assert not order.is_done and 'auto-order' in exchange.in_flight_orders
    with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [raw]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [fill]})):
        context.loop.run_until_complete(exchange._order_tracker.process_order_update(terminal))
    assert order.current_state == OrderState.CANCELED and order.executed_amount_base == Decimal('.04')
    with context.manager.get_new_session() as session:
        saved = session.query(TradeFill).one()
        assert saved.amount == Decimal('.04') and Decimal(saved.trade_fee['flat_fees'][0]['amount']) == Decimal('.01')
    assert not context.store.read()['recovery_state'].get('halt')
    assert not context.owner.terminal_tasks


def test_complete_private_zero_cancel_keeps_the_fast_path_without_extra_rest(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    context, exchange, recorder, transport = managed_native
    raw = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy',
               ordType='post_only', px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='canceled')
    terminal = OrderUpdate('BTC-USDT', 1700000001, OrderState.CANCELED, 'auto-order', 'venue-auto', {'venue_order': raw})
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=AssertionError('Unexpected REST read'))):
        context.loop.run_until_complete(exchange._order_tracker.process_order_update(terminal))
    assert 'auto-order' not in exchange.in_flight_orders
    assert not context.store.read()['recovery_state'].get('halt')


def test_duplicate_terminal_messages_share_one_inflight_proof(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    context, exchange, recorder, transport = managed_native
    entered, release = asyncio.Event(), asyncio.Event()
    raw = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy',
               ordType='post_only', px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='canceled')

    async def read(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'code': '0', 'data': [raw]}

    async def run():
        update = OrderUpdate('BTC-USDT', 1700000001, OrderState.CANCELED, 'auto-order', 'venue-auto')
        first = exchange._order_tracker.process_order_update(update)
        await entered.wait()
        second = exchange._order_tracker.process_order_update(update)
        await asyncio.sleep(0)
        try:
            assert exchange._request_order_update.await_count == 1, 'Duplicate terminal event started another REST proof'
        finally:
            release.set()
            await asyncio.gather(first, second)
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=read)), \
            patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': []})):
        context.loop.run_until_complete(run())
    assert not context.owner.terminal_tasks


def test_terminal_proofs_bound_concurrent_venue_reads(managed_native):
    from hummingbot.core import automatic_recovery as module
    from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate
    context, exchange, recorder, transport = managed_native
    release = asyncio.Event()
    active, peak = 0, 0

    async def proof(connector, order):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await release.wait()
            return OrderUpdate(order.trading_pair, 1700000001, OrderState.CANCELED, order.client_order_id), []
        finally:
            active -= 1

    async def run():
        tasks = []
        for index in range(6):
            order = InFlightOrder.from_json(exchange.in_flight_orders['auto-order'].to_json())
            order.client_order_id = 'bounded-' + str(index)
            update = OrderUpdate(order.trading_pair, 1700000001, OrderState.CANCELED, order.client_order_id)
            tasks.append(asyncio.create_task(context.owner.automatic_recovery.terminal_update(exchange, order, update)))
        for _ in range(10):
            await asyncio.sleep(0)
        try:
            assert peak == 3, 'Terminal proofs exceeded their bounded concurrency'
        finally:
            release.set()
            await asyncio.gather(*tasks)
    with patch.object(module, 'read_order_proof', proof):
        context.loop.run_until_complete(run())
    assert not context.owner.terminal_tasks
