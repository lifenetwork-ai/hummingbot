"""A busy venue cannot grant duplicate authority or stop funded cash reduction."""
import time
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import managed_native as managed_native_fixture
from test.integration.postgres.test_ownership import owned as owned_fixture
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from hummingbot.connector.okx_order_status import OKXOrderNotFoundError
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.order_submission_guard import MarketReadinessBlocked

managed_native = managed_native_fixture
base_owned = owned_fixture


@pytest.fixture
def owned(base_owned):
    base_owned.owner.scoped_recovery_enabled = True
    return base_owned


def isolate(context, exchange, recorder):
    from hummingbot.connector.markets_recorder import MarketsRecorder
    MarketsRecorder._shared_instance = recorder
    context.owner.scoped_recovery_enabled = True
    context.owner.automatic_recovery.status['cold_recovery_supported'] = True
    context.owner._mutate_recovery(lambda state: state.update(spot_outflow={
        'policy': {'max_net_outflow_quote': '50'}}))
    exchange._account_available_balances['BTC'] = Decimal('.5')
    exchange.account_balance_observations['BTC'] = {'cash_balance': Decimal('.5'), 'observed_at': time.time()}
    assert context.owner.automatic_recovery.pause('auto-order')


def test_busy_order_keeps_reservation_and_does_not_close_all_admission(managed_native):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    recovery = context.owner.automatic_recovery
    assert recovery.isolated and context.owner.allow_new_orders
    order = InFlightOrder('independent-sell', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.1'), time.time(), Decimal('100'))
    recovery.authorize_isolated_order(exchange, order)
    with pytest.raises(MarketReadinessBlocked):
        order.trade_type = TradeType.BUY
        recovery.authorize_isolated_order(exchange, order)
    assert exchange.in_flight_orders['auto-order'].exchange_order_id is None
    assert transport.call.await_count == 1
    assert not context.store.read()['recovery_state'].get('halt')


def test_not_found_keeps_isolation_and_positive_proofs_never_resend(managed_native):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    recovery = context.owner.automatic_recovery
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))):
        context.loop.run_until_complete(recovery.step(SimpleNamespace(markets_recorder=recorder,
                                                                      trade_fill_db=context.manager), force=True))
    assert recovery.isolated and recovery.active and context.owner.allow_new_orders
    assert recovery.status['operator_required'] is False
    assert recovery.next_poll > time.monotonic()
    assert transport.call.await_count == 1
    assert exchange.in_flight_orders['auto-order'].exchange_order_id is None


def test_stop_and_persistence_failure_never_become_scoped_authority(managed_native):
    context, exchange, recorder, _ = managed_native
    isolate(context, exchange, recorder)
    order = InFlightOrder('sell', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.1'), time.time(), Decimal('100'))
    context.manager.persistence_healthy = False
    with pytest.raises(Exception):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    context.manager.persistence_healthy = True
    with context.engine.begin() as db:
        db.execute(text("update bot_state set desired_state='stopped',control_version=control_version+1"))
    with pytest.raises(Exception):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)


def test_cold_restore_returns_with_unknown_reservation_instead_of_waiting_forever(managed_native):
    import asyncio

    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.core.order_submission_guard import install_guard
    from hummingbot.model.order import Order
    from hummingbot.model.runtime_journal import SubmissionAttempt

    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    context.owner.release()
    successor = BotOwnership(context.engine, 'hummingbot', scoped_recovery=True)
    assert successor.try_acquire()
    try:
        install_guard(successor)
        successor.prepare(1, 'test.yml')
        successor.connectors = {'okx': exchange}
        core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))):
            context.loop.run_until_complete(asyncio.wait_for(successor.automatic_recovery.restore(core), .5))
        assert successor.automatic_recovery.isolated
        assert not context.store.read()['recovery_state'].get('halt')
        # Returning from restore is not terminal proof or permission to repeat POST.
        with context.manager.get_new_session() as db:
            assert db.get(Order, 'auto-order').creation_pending
            assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
        assert 'auto-order' in exchange.in_flight_orders and transport.call.await_count == 1
    finally:
        successor.release()
        install_guard(context.owner)


@pytest.mark.parametrize('changed_after_auth', [False, True, 'payload'])
def test_native_sell_rechecks_reservations_after_auth_before_transport(managed_native, changed_after_auth):
    import asyncio
    import json
    from test.integration.postgres.test_automatic_recovery import executor_info

    from hummingbot.core.order_submission_guard import session_order_scope
    from hummingbot.model.order import Order

    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    rest = exchange._web_assistants_factory.get_rest_assistant
    # The fixture supplies this same authenticated assistant to native _create_order.
    assistant = context.loop.run_until_complete(rest())
    response = transport.call.return_value
    response.status = 200
    response.json = AsyncMock(return_value={'code': '0', 'data': [{'clOrdId': 'safe-sell', 'ordId': 'venue-sell', 'sCode': '0'}]})
    response.text = AsyncMock(return_value=json.dumps({'code': '0', 'data': [{'clOrdId': 'safe-sell', 'ordId': 'venue-sell', 'sCode': '0'}]}))
    original = assistant._auth.rest_authenticate

    async def authenticate(request):
        request = await original(request)
        if changed_after_auth is True:
            exchange._account_available_balances['BTC'] = Decimal('0')
        elif changed_after_auth == 'payload':
            body = json.loads(request.data)
            body['side'] = 'buy'
            request.data = json.dumps(body)
        return request

    config = executor_info('sale').config.model_copy(update={
        'side': TradeType.SELL, 'mm_session_id': 'mm-auto', 'mm_generation': 0, 'level_id': 'sell_0'})

    async def create():
        with session_order_scope('mm-auto', 0, executor=config.model_dump(mode='json')):
            await exchange._create_order(TradeType.SELL, 'safe-sell', 'BTC-USDT', Decimal('.1'),
                                         OrderType.LIMIT_MAKER, Decimal('100'))
        await asyncio.sleep(0)

    with patch.object(assistant._auth, 'rest_authenticate', authenticate):
        context.loop.run_until_complete(create())
    assert transport.call.await_count == (1 if changed_after_auth else 2)
    with context.manager.get_new_session() as db:
        original_order = db.get(Order, 'auto-order')
        assert original_order.creation_pending and original_order.exchange_order_id is None
        sale = db.get(Order, 'safe-sell')
        assert sale is not None
        assert sale.exchange_order_id == (None if changed_after_auth else 'venue-sell')
    assert not context.store.read()['recovery_state'].get('halt')


def attached_core(context, exchange, recorder):
    from unittest.mock import Mock

    from controllers.market_making.life_mm import LifeMMController
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
    from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig

    class Controller:
        _entry_account_covers_fill = LifeMMController._entry_account_covers_fill

    attempt = context.owner.automatic_recovery._pending()[0]
    config = PositionExecutorConfig.model_validate(attempt['context']['executor'])
    controller = Controller()
    controller.config = config.model_copy(update={'max_data_age_seconds': 5})
    controller.market_data_provider = SimpleNamespace(time=time.time, get_connector_with_fallback=lambda _: exchange)
    strategy = SimpleNamespace(config=SimpleNamespace(mm_drawdown_scope=None), connectors={'okx': exchange},
                               controllers={'maker': controller}, current_timestamp=time.time(), markets={'okx': {'BTC-USDT'}},
                               market_data_provider=SimpleNamespace(time=time.time, get_price_by_type=lambda *args: Decimal('100')),
                               buy=Mock(), sell=Mock())
    strategy.executor_orchestrator = ExecutorOrchestrator(strategy)
    return SimpleNamespace(strategy=strategy, markets_recorder=recorder, trade_fill_db=context.manager,
                           KILL_TIMEOUT=1, cancel_outstanding_orders=AsyncMock())


def test_restored_entry_never_reposts_and_cancel_does_not_reopen_it(managed_native):
    import asyncio

    from hummingbot.core.data_type.in_flight_order import OrderState
    from hummingbot.core.event.events import OrderCancelledEvent

    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    core = attached_core(context, exchange, recorder)

    async def observe():
        context.owner.automatic_recovery.attach_quarantined_entries(core)
        executor, = core.strategy.executor_orchestrator.active_executors['maker']
        await asyncio.sleep(.01)
        order = exchange.in_flight_orders['auto-order']
        assert executor._open_order.order is order and not order.is_done
        order.current_state = OrderState.CANCELED
        executor.process_order_canceled_event(None, exchange, OrderCancelledEvent(time.time(), 'auto-order'))
        await executor.control_task()
        executor.place_open_order()  # No old-ID or replacement POST can be synthesized.
        assert transport.call.await_count == 1
        core.strategy.buy.assert_not_called()
        core.strategy.sell.assert_not_called()
        executor.stop()
    context.loop.run_until_complete(observe())


def test_clean_shutdown_keeps_durable_unknown_intent_without_a_false_halt(managed_native):
    from hummingbot.model.order import Order
    from hummingbot.model.runtime_journal import SubmissionAttempt
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    core = attached_core(context, exchange, recorder)

    async def shutdown(**kwargs):
        assert not core.strategy.executor_orchestrator.active_executors['maker']
        await core.before_position_checkpoint({})
        await core.after_order_cancellation()
        return True

    core.shutdown = shutdown

    async def observe():
        context.owner.automatic_recovery.attach_quarantined_entries(core)
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))) as read:
            assert await context.owner.shutdown_core(core, timeout=2)
            read.assert_not_awaited()
    context.loop.run_until_complete(observe())
    state = context.store.read()['recovery_state']
    assert not state.get('halt') and not state.get('shutdown_incomplete') and not state['runtime_active']
    assert state['automatic_recovery']['phase'] == 'settled_stopped'
    assert not context.owner.allow_new_orders and transport.call.await_count == 1
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'auto-order').creation_pending
        assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'


def test_restored_partial_cancel_waits_for_account_then_holds_original_fee_once(managed_native):
    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    core = attached_core(context, exchange, recorder)
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='.04', uTime='1700000001000', state='canceled')
    trade = dict(tradeId='partial-fill', ordId='venue-auto', instId='BTC-USDT', side='buy',
                 fillSz='.04', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')

    async def observe():
        context.owner.automatic_recovery.attach_quarantined_entries(core)
        executor, = core.strategy.executor_orchestrator.active_executors['maker']
        with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [trade]})):
            await context.owner.automatic_recovery._merge(core, exchange,
                                                          context.owner.automatic_recovery._pending()[0],
                                                          exchange._order_tracker.fetch_order('auto-order'))
        await executor.control_task()
        assert not executor.is_closed  # USDT account observation has not covered the execution.
        exchange.account_balance_observations['USDT'] = {'cash_balance': Decimal('100'), 'observed_at': time.time()}
        await executor.control_task()
        assert executor.is_closed
        core.strategy.executor_orchestrator._update_positions_from_done_executors()
        core.strategy.executor_orchestrator._update_positions_from_done_executors()
        held, = core.strategy.executor_orchestrator.positions_held['maker']
        assert held.net_amount_base == Decimal('.04') and held.cum_fees_quote == Decimal('.01')
        core.strategy.buy.assert_not_called()
        core.strategy.sell.assert_not_called()
    context.loop.run_until_complete(observe())
    with context.manager.get_new_session() as db:
        assert db.query(TradeFill).filter_by(order_id='auto-order').count() == 1
    assert transport.call.await_count == 1


def clone_pending(context, identity, *, state='uncertain'):
    """Seed the durable snapshot of another concurrent send at the crash boundary."""
    from copy import deepcopy

    from sqlalchemy import inspect

    from hummingbot.model.order import Order
    from hummingbot.model.runtime_journal import SubmissionAttempt
    with context.manager.get_new_session() as db, db.begin():
        for model, key in ((Order, 'id'), (SubmissionAttempt, 'order_id')):
            original = db.get(model, 'auto-order')
            values = {column.key: deepcopy(getattr(original, column.key)) for column in inspect(model).columns}
            values[key] = identity
            if model is SubmissionAttempt:
                values['state'] = state
                values['context']['order']['client_order_id'] = identity
                values['context']['executor']['id'] = identity
            db.add(model(**values))


def test_possibly_sent_sibling_is_reserved_without_worker_racing_transport(managed_native):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'concurrent', state='possibly_sent')
    order = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.35'), time.time(), Decimal('100'))
    with pytest.raises(MarketReadinessBlocked, match='uncertain_base_reserved'):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    assert [row['order_id'] for row in context.owner.automatic_recovery._pending()] == ['auto-order']
    assert transport.call.await_count == 1


def test_inconclusive_first_order_does_not_starve_later_positive_proof(managed_native):
    context, exchange, recorder, _ = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'later')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    calls = []

    async def merge(core, connector, attempt, order):
        calls.append(attempt['order_id'])
        if attempt['order_id'] == 'auto-order':
            raise OKXOrderNotFoundError('51603')
        return None  # The proof boundary is exercised independently in the partial-fill test.

    with patch.object(recovery, '_merge', merge):
        context.loop.run_until_complete(recovery.step(SimpleNamespace(markets_recorder=recorder,
                                                                      trade_fill_db=context.manager), force=True))
    assert calls == ['auto-order', 'later']
    assert recovery.isolated and not context.store.read()['recovery_state'].get('halt')


def test_previous_owner_and_current_risk_halt_cannot_use_scoped_admission(managed_native):
    from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    order = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.1'), time.time(), Decimal('100'))
    context.owner._mutate_recovery(lambda state: state.update(halt=True, halt_kind='manual'))
    with pytest.raises(SubmissionBlocked):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    context.owner.release()
    successor = BotOwnership(context.engine, context.store.bot_id, scoped_recovery=True)
    try:
        assert successor.try_acquire()
        with pytest.raises((OwnershipLost, SubmissionBlocked)):
            context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    finally:
        successor.release()
    assert transport.call.await_count == 1


@pytest.mark.parametrize('change', ['other_bot', 'missing_original_fee'])
def test_foreign_or_legacy_intent_cannot_grant_scoped_authority(managed_native, change):
    from hummingbot.model.runtime_journal import SubmissionAttempt
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    from copy import deepcopy
    with context.manager.get_new_session() as db, db.begin():
        row = db.get(SubmissionAttempt, 'auto-order')
        saved = deepcopy(row.context)
        if change == 'other_bot':
            saved['bot_id'] = 'another-bot'
        else:
            saved['reservation'].pop('cash_buy_fee')
        row.context = saved
    order = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.1'), time.time(), Decimal('100'))
    with pytest.raises(MarketReadinessBlocked):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    assert not context.owner.allow_new_orders and not context.owner.automatic_recovery.isolated
    assert not context.store.read()['recovery_state'].get('halt') and transport.call.await_count == 1


def test_disconnect_handoff_keeps_original_reservation_and_never_restores_send_authority(managed_native):
    from copy import deepcopy
    from datetime import datetime, timezone

    from hummingbot.core.admin_identity import Principal
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    from hummingbot.model.credential_store import CredentialStore
    from hummingbot.model.exchange_credential import ExchangeCredential
    from hummingbot.model.runtime_journal import (
        RuntimeCheckpoint,
        SubmissionAttempt,
        binding_hash,
        bindings,
        can_bootstrap,
    )

    context, exchange, recorder, transport = managed_native
    with context.manager.get_new_session() as db, db.begin():
        db.add(ExchangeCredential(bot_id=context.store.bot_id, exchange='okx', connector='okx', environment='live',
                                  version=1, account_id='www:offline', state='active', envelope={'fixture': 'ciphertext'},
                                  changed_by='fixture', changed_at=datetime.now(timezone.utc)))
        db.flush()
        row = db.get(SubmissionAttempt, 'auto-order')
        saved = deepcopy(row.context)
        saved['bindings'] = bindings(db, context.store.bot_id)
        row.context = saved
        db.get(RuntimeCheckpoint, 'test.yml').bindings_hash = binding_hash(db, context.store.bot_id)
    isolate(context, exchange, recorder)
    core = attached_core(context, exchange, recorder)
    store = CredentialStore(context.store)
    # The native-order fixture has only the small final-gate session projection.
    # Use a full persisted session for the actual credential command decoder.
    import json
    from random import Random
    from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

    from hummingbot.client.config.mm_profiles import MMProfilesConfig
    from hummingbot.core.mm_session import MMSession, MMSessionRequest
    catalogue = context.loop.run_until_complete(MMProfilesConfig.load(CATALOGUE))
    current_session = MMSession.create(catalogue, MMSessionRequest(market_mode='spot', profile_id='follow_market',
                                                                   duration_seconds=600), rng=Random(1), session_id='mm-auto')
    with context.engine.begin() as db:
        db.execute(text('UPDATE bot_state SET mm_session=CAST(:value AS jsonb)'),
                   {'value': json.dumps(current_session.model_dump(mode='json'))})
    receipt = store.accept_disconnect(Principal('basic:operator', 'basic', 'admin'), exchange='okx', connector='okx',
                                      environment='live', expected_version=1, expected_control_version=1,
                                      idempotency_key='disconnect-test', request_id='disconnect-test')
    with context.manager.get_new_session() as db:
        state = context.store.read()
        assert not can_bootstrap(db, state)  # Revoking is still blocked for ordinary trading.
        assert can_bootstrap(db, state, retiring=True)
        risk_stopped = deepcopy(state)
        risk_stopped['recovery_state']['strategy_checkpoints'] = {'test.yml': {'_is_stop_triggered': True}}
        assert not can_bootstrap(db, risk_stopped, retiring=True)

    async def shutdown(**kwargs):
        if core.before_position_checkpoint:
            await core.before_position_checkpoint({})
        await core.after_order_cancellation()
        return True

    core.shutdown = shutdown

    async def observe():
        context.owner.automatic_recovery.attach_quarantined_entries(core)
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))) as read:
            assert await context.owner.shutdown_core(core, timeout=2)
            # Cleanup retains the same obligation after adopting its own recorder
            # attribution, with no strategy/executor and no trading permission.
            core.strategy = None
            context.owner.config_file_path = 'credentials_cleanup'
            assert await context.owner.shutdown_core(core, timeout=2)
            read.assert_not_awaited()
    context.loop.run_until_complete(observe())
    order = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                          Decimal('.1'), time.time(), Decimal('100'))
    with pytest.raises(SubmissionBlocked):
        context.owner.automatic_recovery.authorize_isolated_order(exchange, order)
    state = context.store.read()
    assert not state['recovery_state'].get('halt') and not state['recovery_state']['runtime_active']
    assert state['recovery_state']['credential_operation']['id'] == receipt['id']
    assert not context.owner.allow_new_orders and transport.call.await_count == 1
    assert store.load('okx', 'okx', 'live')['state'] == 'revoking'
    assert store.load('okx', 'okx', 'live')['envelope'] is not None
