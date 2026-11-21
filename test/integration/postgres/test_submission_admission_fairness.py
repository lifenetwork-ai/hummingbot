"""A native quote burst must leave loop turns for health and revocation checks."""
import asyncio
import json
import threading
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture
from test.integration.postgres.test_spot_outflow import setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from bidict import bidict
from psycopg2 import OperationalError
from sqlalchemy import event

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core import bot_ownership, engine_diagnostics
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import RuntimeJournal, SubmissionAttempt

owned = owned_fixture


@pytest.fixture
def native(owned, request):
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                           trading_pairs=['LIFE-USDT'])
    exchange._set_current_timestamp(time.time())
    exchange._set_trading_pair_symbol_map(bidict({'LIFE-USDT': 'LIFE-USDT'}))
    exchange._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('.01'),
                                                       min_price_increment=Decimal('.01'),
                                                       min_base_amount_increment=Decimal('.01'))
    exchange.get_fee = MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001')))
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    context = SimpleNamespace(**{**vars(owned), 'market': exchange, 'recorder': recorder})
    owned.owner.connectors = {'okx': exchange}
    session, _ = setup(context, max_net_outflow_quote=getattr(request, 'param', '1000'))
    rest, transport = assistant()
    rest._auth = exchange.authenticator

    async def reply(request):
        data = json.loads(request.data)
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={'code': '0', 'data': [dict(ordId='venue-' + data['clOrdId'], sCode='0')]})
        return response

    transport.call.side_effect = reply
    try:
        with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
            yield SimpleNamespace(**vars(context), session=session, transport=transport)
    finally:
        recorder.stop()
        owned.loop.run_until_complete(exchange._web_assistants_factory.close())


@pytest.mark.parametrize(('after_probe', 'final_state'), [
    ('send', 'acknowledged'), ('stop', 'terminal'), ('cancel', 'rejected'),
])
def test_slow_intent_read_keeps_engine_live_until_durable_admission(native, after_probe, final_state):
    delayed, release = threading.Event(), threading.Event()
    order_id = 'slow-intent'
    original_reply = native.transport.call.side_effect

    def hold_checkpoint(connection, cursor, statement, parameters, context, executemany):
        if 'FROM market_states' in statement and not delayed.is_set():
            delayed.set()
            release.wait(1.5)

    async def venue(request):
        with native.manager.get_new_session() as db:
            assert db.get(Order, order_id).creation_pending
            assert db.get(SubmissionAttempt, order_id).state == 'possibly_sent'
        return await original_reply(request)

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        server = web.AppRunner(app)
        await server.setup()
        await web.TCPSite(server, '127.0.0.1', 0).start()
        port = server.addresses[0][1]

        def probe():
            assert delayed.wait(2)
            return probe_health(port, '/live')

        checking = asyncio.create_task(asyncio.to_thread(probe))
        await asyncio.sleep(0)
        with session_order_scope(native.session.id, native.session.generation):
            order = asyncio.create_task(native.market._create_order(
                TradeType.BUY, order_id, 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
        try:
            healthy, detail = await checking
            assert healthy, detail
            native.transport.call.assert_not_awaited()
            with native.manager.get_new_session() as db:
                assert db.get(Order, order_id) is None and db.get(SubmissionAttempt, order_id) is None
            if after_probe == 'stop':
                native.owner.begin_shutdown()
            elif after_probe == 'cancel':
                order.cancel()
                await asyncio.sleep(0)
                assert not order.done() and order in native.owner.submission_tasks
        finally:
            release.set()
            results = await asyncio.gather(order, return_exceptions=True)
            await server.cleanup()
        if after_probe == 'cancel':
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert results == [None]

    event.listen(native.engine, 'before_cursor_execute', hold_checkpoint)
    try:
        with patch.object(native.transport.call, 'side_effect', venue):
            native.loop.run_until_complete(exercise())
    finally:
        event.remove(native.engine, 'before_cursor_execute', hold_checkpoint)
    sent = after_probe == 'send'
    assert native.transport.call.await_count == int(sent)
    with native.manager.get_new_session() as db:
        assert not db.get(Order, order_id).creation_pending
        assert db.get(SubmissionAttempt, order_id).state == final_state
    assert not native.owner.submission_tasks and not native.store.read()['recovery_state'].get('halt')


def test_cancel_during_intent_fence_does_not_retry_a_confirmed_rollback(native):
    attempting, release = threading.Event(), threading.Event()

    def hold_fence(connection, cursor, statement, parameters, context, executemany):
        if 'FOR SHARE NOWAIT' in statement and not attempting.is_set():
            attempting.set()
            release.wait(1.5)

    async def exercise():
        with native.engine.connect() as blocker:
            blocker.exec_driver_sql('SELECT bot_id FROM bot_state FOR UPDATE')
            with session_order_scope(native.session.id, native.session.generation):
                order = asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'busy-cancel', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
            try:
                assert await asyncio.to_thread(attempting.wait, 2)
                order.cancel()
                await asyncio.sleep(0)
                assert not order.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(order, 2)
            finally:
                release.set()
                blocker.rollback()
                if not order.done():
                    order.cancel()
                    await asyncio.gather(order, return_exceptions=True)

    event.listen(native.engine, 'before_cursor_execute', hold_fence)
    try:
        native.loop.run_until_complete(exercise())
    finally:
        event.remove(native.engine, 'before_cursor_execute', hold_fence)
    native.transport.call.assert_not_awaited()
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
    assert not native.owner.active_intents and not native.owner.submission_tasks
    assert native.owner.spot_outflow.status()['reserved_quote'] == 0
    assert native.manager.persistence_healthy and not native.store.read()['recovery_state'].get('halt')


def test_slow_intent_rolls_back_if_original_owner_lock_disappears(native):
    reading, release = threading.Event(), threading.Event()
    order = InFlightOrder('lost-lock', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal('5'), time.time(), Decimal('10'))
    native.market._order_tracker.start_tracking_order(order)

    def hold_checkpoint(connection, cursor, statement, parameters, context, executemany):
        if 'FROM market_states' in statement and not reading.is_set():
            reading.set()
            release.wait(1.5)

    async def exercise():
        with session_order_scope(native.session.id, native.session.generation):
            admitting = asyncio.create_task(native.owner.admit_order(native.market, order))
        try:
            assert await asyncio.to_thread(reading.wait, 2)
            native.owner.connection.exec_driver_sql('SELECT pg_advisory_unlock_all()')
            native.owner.connection.commit()
        finally:
            release.set()
        with pytest.raises(OwnershipLost):
            await admitting

    event.listen(native.engine, 'before_cursor_execute', hold_checkpoint)
    try:
        native.loop.run_until_complete(exercise())
    finally:
        event.remove(native.engine, 'before_cursor_execute', hold_checkpoint)
    assert native.owner.lost and not native.manager.persistence_healthy
    native.transport.call.assert_not_awaited()
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0


def test_native_eight_quote_burst_allows_health_callback_between_admissions(native, monkeypatch):
    monkeypatch.setattr(bot_ownership, 'MAX_INTENT_BATCH', 1)
    admitted, probe_counts = [], []
    original = native.recorder.persist_order_intent

    async def record(*args):
        result = await original(*args)
        admitted.append(args[1].client_order_id)
        if len(admitted) == 1:
            asyncio.get_running_loop().call_soon(lambda: probe_counts.append(len(admitted)))
        return result

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [
                asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'burst-' + str(i), 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    with patch.object(native.recorder, 'persist_order_intent', side_effect=record):
        native.loop.run_until_complete(burst())
    assert len(admitted) == native.transport.call.await_count == 8
    assert probe_counts == [1], f'Health callback waited behind {probe_counts} synchronous admissions'
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 8
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert {x.state for x in db.query(SubmissionAttempt)} == {'acknowledged'}
    assert not native.store.read()['recovery_state'].get('halt')


def test_worker_intent_verifies_owner_and_locks_row_in_one_statement(native):
    worker_fences = []

    def observe_fence(connection, cursor, statement, parameters, context, executemany):
        if threading.current_thread() is not threading.main_thread() and (
                'FOR SHARE NOWAIT' in statement or 'SELECT 1 FROM pg_locks' in statement):
            worker_fences.append(statement)

    async def admit():
        order = InFlightOrder('intent-round-trips', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                              Decimal('5'), time.time(), Decimal('10'))
        native.market._order_tracker.start_tracking_order(order)
        with session_order_scope(native.session.id, native.session.generation):
            await native.owner.admit_order(native.market, order)

    event.listen(native.engine, 'before_cursor_execute', observe_fence)
    try:
        native.loop.run_until_complete(admit())
    finally:
        event.remove(native.engine, 'before_cursor_execute', observe_fence)

    assert 'FOR SHARE NOWAIT' in worker_fences[0]
    assert 'SELECT 1 FROM pg_locks' in worker_fences[0]
    assert 'SELECT 1 FROM pg_locks' in worker_fences[-1] and 'FOR SHARE' not in worker_fences[-1]
    with native.manager.get_new_session() as db:
        assert db.get(Order, 'intent-round-trips').creation_pending
        assert db.get(SubmissionAttempt, 'intent-round-trips').state == 'prepared'
    native.transport.call.assert_not_awaited()


def test_native_intent_and_ack_flush_each_snapshot_with_its_order(native):
    flushed = []
    queued = InFlightOrder('queued-sibling', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                           Decimal('5'), time.time(), Decimal('10'))
    native.market._order_tracker.start_tracking_order(queued)

    def observe_flush(session, *_):
        if session.info.get('worker_order_owner') is native.owner:
            flushed.append({type(row).__name__ for row in set(session.new) | set(session.dirty)})

    async def send():
        with session_order_scope(native.session.id, native.session.generation):
            await native.market._create_order(TradeType.BUY, 'combined-snapshot', 'LIFE-USDT',
                                              Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10'))

    event.listen(native.manager._session_cls, 'before_flush', observe_flush)
    try:
        native.loop.run_until_complete(send())
    finally:
        event.remove(native.manager._session_cls, 'before_flush', observe_flush)

    assert len(flushed) == 2, flushed
    assert all({'Order', 'OrderStatus', 'SubmissionAttempt', 'MarketState'} <= rows for rows in flushed)
    native.transport.call.assert_awaited_once()
    with native.manager.get_new_session() as db:
        order = db.get(Order, 'combined-snapshot')
        assert order.exchange_order_id == 'venue-combined-snapshot' and not order.creation_pending
        assert db.get(SubmissionAttempt, order.id).state == 'acknowledged'
        assert db.query(MarketState).one().saved_state == {order.id: native.market.tracking_states[order.id]}
        assert db.get(Order, queued.client_order_id) is None


def test_native_burst_commits_bounded_intent_batches_before_sending(native):
    batches = []
    reply = native.transport.call.side_effect

    def inspect_flush(session, *_):
        orders = {row.order_id for row in session.new if isinstance(row, RuntimeJournal) and row.kind == 'intent'}
        if orders:
            batches.append(orders)
            assert sum(isinstance(row, MarketState) for row in set(session.new) | set(session.dirty)) == 1

    async def venue(request):
        order_id = json.loads(request.data)['clOrdId']
        with native.manager.get_new_session() as db:
            attempt = db.get(SubmissionAttempt, order_id)
            assert attempt.state == 'possibly_sent' and attempt.context['executor']['id'] == order_id
            batch, = [batch for batch in batches if order_id in batch]
            assert {identity for identity in batch if db.get(Order, identity) is not None} == batch
        return await reply(request)

    async def burst():
        tasks = []
        for index in range(8):
            identity = f'batch-{index}'
            with session_order_scope(native.session.id, native.session.generation, executor={'id': identity}):
                tasks.append(asyncio.create_task(native.market._create_order(
                    TradeType.BUY, identity, 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10'))))
        await asyncio.gather(*tasks)

    event.listen(native.manager._session_cls, 'before_flush', inspect_flush)
    try:
        with patch.object(native.transport.call, 'side_effect', venue):
            native.loop.run_until_complete(burst())
    finally:
        event.remove(native.manager._session_cls, 'before_flush', inspect_flush)
    assert batches == [{f'batch-{i}' for i in range(4)}, {f'batch-{i}' for i in range(4, 8)}]
    assert native.transport.call.await_count == 8


def test_batch_updates_each_connector_snapshot_with_its_own_intents(native):
    swap = MagicMock()
    swap.name, swap.display_name = 'okx_perpetual', 'okx_perpetual'
    swap.account_uid, swap.trading_environment = native.market.account_uid, native.market.trading_environment
    swap.authenticator = SimpleNamespace(api_key='offline-perpetual')
    native.recorder._markets.append(swap)
    native.owner.connectors[swap.name] = swap
    spot_order, swap_order = [
        InFlightOrder(identity, 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                      Decimal('5'), time.time(), Decimal('10'))
        for identity in ('batched-spot', 'batched-perp')
    ]
    native.market._order_tracker.start_tracking_order(spot_order)
    swap.in_flight_orders = {swap_order.client_order_id: swap_order}
    swap.tracking_states = {swap_order.client_order_id: swap_order.to_json()}

    async def admit():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(native.owner.admit_order(native.market, spot_order),
                                 native.owner.admit_order(swap, swap_order))

    native.loop.run_until_complete(admit())
    with native.manager.get_new_session() as db:
        assert {row.market: set(row.saved_state) for row in db.query(MarketState)} == {
            'okx': {'batched-spot'}, 'okx_perpetual': {'batched-perp'}}
        assert {row.order_id: row.state for row in db.query(SubmissionAttempt)} == {
            'batched-spot': 'prepared', 'batched-perp': 'prepared'}
    native.transport.call.assert_not_awaited()


def test_cancelled_batch_leader_before_write_releases_peers_to_retry(native):
    prepare = native.owner._prepare_order_intent

    def cancel_leader(connector, order, **kwargs):
        result = prepare(connector, order, **kwargs)
        if order.client_order_id == 'cancel-leader-0':
            asyncio.current_task().cancel()
        return result

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            outcomes = await asyncio.gather(*(native.market._create_order(
                TradeType.BUY, f'cancel-leader-{i}', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10'))
                for i in range(4)), return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert outcomes[1:] == [None, None, None]
        assert not native.owner.submission_tasks and not native.owner.submission_admission.locked()

    with patch.object(native.owner, '_prepare_order_intent', side_effect=cancel_leader):
        native.loop.run_until_complete(burst())
    with native.manager.get_new_session() as db:
        assert {row.order_id: row.state for row in db.query(SubmissionAttempt)} == {
            f'cancel-leader-{index}': 'acknowledged' for index in range(1, 4)}
    assert native.transport.call.await_count == 3
    assert not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('interrupt', ['cancel-peer', 'lose-ownership'])
def test_intent_batch_drains_after_cancellation_or_ownership_loss(native, interrupt):
    entered, proceed = threading.Event(), threading.Event()

    def hold_snapshot(connection, cursor, statement, *args):
        if 'FROM market_states' in statement and not entered.is_set():
            entered.set()
            assert proceed.wait(2)

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [asyncio.create_task(native.market._create_order(
                TradeType.BUY, f'interrupted-{i}', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                for i in range(4)]
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            native.transport.call.assert_not_awaited()
            if interrupt == 'cancel-peer':
                tasks[1].cancel()
                await asyncio.sleep(0)
                assert not tasks[1].done() and tasks[1] in native.owner.submission_tasks
            else:
                native.owner.connection.exec_driver_sql('SELECT pg_advisory_unlock_all()')
                native.owner.connection.commit()
        finally:
            proceed.set()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if interrupt == 'cancel-peer':
            assert isinstance(outcomes[1], asyncio.CancelledError)
        assert not native.owner.submission_tasks and not native.owner.submission_admission.locked()

    event.listen(native.engine, 'before_cursor_execute', hold_snapshot)
    try:
        native.loop.run_until_complete(burst())
    finally:
        event.remove(native.engine, 'before_cursor_execute', hold_snapshot)
    with native.manager.get_new_session() as db:
        if interrupt == 'cancel-peer':
            sent = {json.loads(call.args[0].data)['clOrdId'] for call in native.transport.call.await_args_list}
            assert sent == {'interrupted-0', 'interrupted-2', 'interrupted-3'}
            assert db.get(SubmissionAttempt, 'interrupted-1').state == 'rejected'
            assert native.manager.persistence_healthy
        else:
            native.transport.call.assert_not_awaited()
            assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
            assert native.owner.lost and not native.manager.persistence_healthy


@pytest.mark.parametrize('native', ['120'], indirect=True)
def test_intent_batch_reserves_spot_budget_in_order(native):
    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(native.market._create_order(
                TradeType.BUY, f'limited-{i}', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10'))
                for i in range(4)))

    native.loop.run_until_complete(burst())
    assert [json.loads(call.args[0].data)['clOrdId'] for call in native.transport.call.await_args_list] == [
        'limited-0', 'limited-1']
    with native.manager.get_new_session() as db:
        assert {row.order_id for row in db.query(SubmissionAttempt)} == {'limited-0', 'limited-1'}
    assert native.owner.spot_outflow.status()['reserved_quote'] == Decimal('101')


@pytest.mark.parametrize('committed', [False, True], ids=['rolled-back', 'commit-reply-lost'])
def test_intent_batch_commit_failure_keeps_every_order_behind_send_gate(native, monkeypatch, committed):
    failure_drivers = []
    driver_class = type(native.owner.connection.connection.driver_connection)
    commit = driver_class.commit

    def inspect_flush(session, *_):
        orders = [r for r in session.new if isinstance(r, RuntimeJournal) and r.kind == 'intent']
        if len(orders) == 4:
            failure_drivers.append(session.connection().connection.driver_connection)

    def disconnect(driver):
        if driver in failure_drivers:
            failure_drivers.remove(driver)
            if committed:
                commit(driver)
            driver.close()
            raise OperationalError('test lost intent batch COMMIT response')
        return commit(driver)

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(native.market._create_order(
                TradeType.BUY, f'bad-commit-{i}', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10'))
                for i in range(4)))

    monkeypatch.setattr(driver_class, 'commit', disconnect)
    event.listen(native.manager._session_cls, 'before_flush', inspect_flush)
    try:
        native.loop.run_until_complete(burst())
    finally:
        event.remove(native.manager._session_cls, 'before_flush', inspect_flush)
    native.transport.call.assert_not_awaited()
    assert native.store.read()['recovery_state']['halt']
    assert not native.owner.allow_new_orders and not native.manager.persistence_healthy
    assert not native.owner.submission_tasks
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == 4 * committed
        assert db.query(SubmissionAttempt).count() == 4 * committed
        assert all(row.state == 'prepared' and row.payload_hash is None for row in db.query(SubmissionAttempt))


def test_stop_between_native_admissions_prevents_the_rest_of_the_burst(native):
    original = native.recorder.persist_order_intents
    admitted = []

    async def record(intents):
        result = await original(intents)
        admitted.extend(intent.order.client_order_id for intent in intents)
        asyncio.get_running_loop().call_soon(native.owner.begin_shutdown)
        return result

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [
                asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'stopped-' + str(i), 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    with patch.object(native.recorder, 'persist_order_intents', side_effect=record):
        native.loop.run_until_complete(burst())
    # Stop now runs before the first order's final send fence too. Its prepared
    # intent must be rejected durably instead of crossing the transport gate.
    assert len(admitted) == 4 and native.transport.call.await_count == 0
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 4
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert {row.state for row in db.query(SubmissionAttempt)} == {'terminal'}
    assert not native.store.read()['recovery_state'].get('halt')


def test_native_burst_paired_callback_wait_with_real_postgres(native, monkeypatch):
    from test.integration.postgres.test_performance import queries, report

    from hummingbot.core import order_submission_guard as gates
    monkeypatch.setattr(bot_ownership, 'MAX_INTENT_BATCH', 1)
    original_record, persist_intent = native.recorder.record_order_intent, native.recorder.persist_order_intent
    current = gates.before_order

    async def predecessor(connector, order):
        gates.active_guard().before_order(connector, order)

    # The same bounded synthetic round-trip cost is present in both schedules.
    def delayed_roundtrip(*args):
        time.sleep(.002)

    async def run(legacy):
        admitted, receipt = [], {}
        started = time.perf_counter()

        def observe_admission(order):
            admitted.append(order.client_order_id)
            if len(admitted) == 1:
                queued_at = time.perf_counter()

                def probe():
                    receipt.update(admissions_before_probe=len(admitted),
                                   callback_wait_ms=(time.perf_counter() - queued_at) * 1000)

                asyncio.get_running_loop().call_soon(probe)

        def record(market, order):
            original_record(market, order)
            observe_admission(order)

        async def persist(market, order):
            result = await persist_intent(market, order)
            observe_admission(order)
            return result

        with patch.object(native.recorder, 'record_order_intent', side_effect=record), \
                patch.object(native.recorder, 'persist_order_intent', side_effect=persist), \
                patch.object(gates, 'before_order', predecessor if legacy else current), \
                queries(native.engine) as statements:
            with session_order_scope(native.session.id, native.session.generation):
                tasks = [asyncio.create_task(native.market._create_order(
                    TradeType.BUY, f'paired-{legacy}-{i}', 'LIFE-USDT', Decimal('5'),
                    OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(8)]
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
        receipt['burst_completion_ms'] = (time.perf_counter() - started) * 1000
        receipt['sql_statements'] = len(statements)
        assert len(admitted) == 8
        return receipt

    event.listen(native.engine, 'after_cursor_execute', delayed_roundtrip)
    try:
        before = native.loop.run_until_complete(run(True))
        after = native.loop.run_until_complete(run(False))
    finally:
        event.remove(native.engine, 'after_cursor_execute', delayed_roundtrip)
    assert before['admissions_before_probe'] == 8 and after['admissions_before_probe'] == 1
    assert native.transport.call.await_count == 16
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 16
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert all(row.amount == Decimal('5') and row.price == Decimal('10') for row in db.query(Order))
    report('submission_admission_callback_wait', dict(before=before, after=after, injected_sql_delay_seconds=.002,
                                                      scope='Native connector and real PostgreSQL; not Cloud HTTP latency'))


@pytest.mark.parametrize('retirement', ['canceled', 'removed'])
def test_native_order_retired_while_queued_cannot_leave_an_intent_or_halt(native, retirement):
    async def run():
        await native.owner.submission_admission.acquire()
        with session_order_scope(native.session.id, native.session.generation):
            task = asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'retired', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
        await asyncio.sleep(0)
        assert task in native.owner.submission_tasks
        if retirement == 'canceled':
            native.market._order_tracker.process_order_update(OrderUpdate(
                trading_pair='LIFE-USDT', update_timestamp=time.time(), new_state=OrderState.CANCELED,
                client_order_id='retired'))
        else:
            native.market.stop_tracking_order('retired')
        native.owner.submission_admission.release()
        await task
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    native.loop.run_until_complete(run())
    native.transport.call.assert_not_awaited()
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
    assert not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('legacy', [True, False], ids=['predecessor', 'candidate'])
def test_fresh_burst_callback_and_completion_work(native, legacy, monkeypatch):
    """Equal empty ledgers avoid growing the candidate's workload after a replay."""
    from test.integration.postgres.test_performance import queries, report

    from hummingbot.core import order_submission_guard as gates
    monkeypatch.setattr(bot_ownership, 'MAX_INTENT_BATCH', 1)
    current, original_record = gates.before_order, native.recorder.record_order_intent
    persist_intent = native.recorder.persist_order_intent
    admitted, timing = [], {}

    async def predecessor(connector, order):
        gates.active_guard().before_order(connector, order)

    def observe_admission(order):
        admitted.append(order.client_order_id)
        if len(admitted) == 1:
            queued_at = time.perf_counter()

            def observe():
                timing.update(admissions_before_callback=len(admitted),
                              callback_wait_ms=(time.perf_counter() - queued_at) * 1000)

            asyncio.get_running_loop().call_soon(observe)

    def record(market, order):
        original_record(market, order)
        observe_admission(order)

    async def persist(market, order):
        result = await persist_intent(market, order)
        observe_admission(order)
        return result

    def delay(*args):
        time.sleep(.002)

    async def run():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'fresh-' + str(i), 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    event.listen(native.engine, 'after_cursor_execute', delay)
    try:
        with patch.object(native.recorder, 'record_order_intent', side_effect=record), \
                patch.object(native.recorder, 'persist_order_intent', side_effect=persist), \
                patch.object(gates, 'before_order', predecessor if legacy else current), queries(native.engine) as statements:
            started = time.perf_counter()
            native.loop.run_until_complete(run())
            timing.update(burst_completion_ms=(time.perf_counter() - started) * 1000, sql_statements=len(statements))
    finally:
        event.remove(native.engine, 'after_cursor_execute', delay)
    assert timing['admissions_before_callback'] == (8 if legacy else 1)
    assert len(admitted) == native.transport.call.await_count == 8
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 8
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
    report('fresh_burst_predecessor' if legacy else 'fresh_burst_candidate', timing)


def test_first_ownership_conflict_retries_after_a_short_shared_cooldown(native):
    schedule = []
    release_busy = native.owner._release_busy_intent
    deferred = asyncio.Event()

    def record_retry(order):
        now = time.monotonic()
        with patch.object(bot_ownership, 'time', SimpleNamespace(monotonic=lambda: now)):
            release_busy(order)
        schedule.append(native.owner._intent_admission_retry_at - now)
        deferred.set()

    async def run():
        with native.engine.begin() as blocker:
            blocker.exec_driver_sql('SELECT bot_id FROM bot_state FOR UPDATE').all()
            with session_order_scope(native.session.id, native.session.generation):
                order = asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'brief-owner-conflict', 'LIFE-USDT', Decimal('5'),
                    OrderType.LIMIT_MAKER, Decimal('10')))
            await asyncio.wait_for(deferred.wait(), 2)
            native.transport.call.assert_not_awaited()
            assert native.manager.persistence_healthy
            assert not native.owner.active_intents and not native.owner.sent_intents
        await asyncio.wait_for(order, 2)

    with patch.object(native.owner, '_release_busy_intent', side_effect=record_retry):
        native.loop.run_until_complete(run())

    delay, = schedule
    assert 0 < delay <= .05, f'First retry was deferred for {delay:.3f}s'
    native.transport.call.assert_awaited_once()
    with native.manager.get_new_session() as db:
        assert db.query(SubmissionAttempt).one().state == 'acknowledged'
    assert not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('after_wait', ['send', 'stop', 'retire', 'reprice'])
def test_native_intent_contention_waits_on_same_identity_and_rechecks_authority(native, after_wait, monkeypatch):
    from hummingbot.core.order_submission_guard import QuoteRefreshRequired

    diagnostics = engine_diagnostics.EngineDiagnostics()
    monkeypatch.setattr(engine_diagnostics, '_ACTIVE', diagnostics)

    async def run():
        blocker = native.engine.connect()
        tx = blocker.begin()
        blocker.exec_driver_sql('SELECT bot_id FROM bot_state FOR UPDATE').all()
        task = None
        try:
            with session_order_scope(native.session.id, native.session.generation):
                task = asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'busy-native', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
            async with asyncio.timeout(3):
                while sum(phase['operation'] == 'order.intent' and phase['error']
                          for phase in diagnostics.snapshot()['submission_recent']) < 2:
                    await asyncio.sleep(.01)
            assert not task.done(), 'A proven unsent conflict must wait instead of triggering executor retries'
            assert task in native.owner.submission_tasks and not native.owner.submission_admission.locked()
            native.transport.call.assert_not_awaited()
            assert native.manager.persistence_healthy
            assert not native.owner.active_intents and not native.owner.sent_intents
            assert native.owner.spot_outflow.status()['reserved_quote'] == 0
            assert 'busy-native' in native.market.in_flight_orders
            with native.manager.get_new_session() as db:
                assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
            if after_wait == 'stop':
                native.owner.begin_shutdown()
            elif after_wait == 'retire':
                native.market.stop_tracking_order('busy-native')
            elif after_wait == 'reprice':
                native.owner.mm_quote_check = MagicMock(side_effect=QuoteRefreshRequired('price changed while waiting'))
            tx.commit()
            await asyncio.wait_for(task, 2)
            await asyncio.sleep(.02)
        finally:
            if tx.is_active:
                tx.rollback()
            blocker.close()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not native.owner.submission_tasks and not native.owner.submission_admission.locked()

    native.loop.run_until_complete(run())
    sends = int(after_wait == 'send')
    phases = diagnostics.snapshot()['submission_recent']
    admission, = [phase for phase in phases if phase['operation'] == 'order.admission']
    intents = [phase for phase in phases if phase['operation'] == 'order.intent']
    expected_backoff_ms = sum(bot_ownership.INTENT_ADMISSION_RETRY_DELAYS[:2]) * 1000
    assert admission['ownership_backoff_ms'] >= expected_backoff_ms - 10
    assert admission['admission_slot_wait_ms'] >= 0
    assert admission['recorder_lock_wait_ms'] >= 0
    assert len(intents) == 2 + sends
    assert all(0 <= phase['ownership_row_check_ms'] <= phase['duration_ms'] for phase in intents)
    assert all(phase['order'] == admission['order'] for phase in intents)
    assert native.transport.call.await_count == sends
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == sends
        if sends:
            assert db.query(Order).one().id == 'busy-native'
            assert db.query(SubmissionAttempt).one().state == 'acknowledged'
            assert native.owner._intent_admission_retry_index == 0
    assert native.manager.persistence_healthy and not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('hold_seconds', [.065, 1.35], ids=['short-lock', 'sustained-lock'])
@pytest.mark.parametrize('fixed', [True, False], ids=['fixed', 'adaptive'])
def test_eight_native_waiters_share_cooldown_without_failures_or_duplicate_sends(native, hold_seconds, fixed, monkeypatch):
    from test.integration.postgres.test_performance import report

    from hummingbot.core.event.event_forwarder import EventForwarder
    from hummingbot.core.event.event_logger import EventLogger
    from hummingbot.core.event.events import MarketEvent

    if fixed:
        monkeypatch.setattr(bot_ownership, 'INTENT_ADMISSION_RETRY_DELAYS', (.5,), raising=False)
    diagnostics = engine_diagnostics.EngineDiagnostics()
    monkeypatch.setattr(engine_diagnostics, '_ACTIVE', diagnostics)
    failures, attempts, accepted = EventLogger(), [], {}
    first_attempt = asyncio.Event()
    buy_created = EventForwarder(lambda update: accepted.setdefault(update.order_id, time.monotonic()))
    native.market.add_listener(MarketEvent.OrderFailure, failures)
    native.market.add_listener(MarketEvent.BuyOrderCreated, buy_created)
    blocked = [False]

    def fence(connection, cursor, statement, *args):
        if blocked[0] and 'FOR SHARE' in statement and 'NOWAIT' in statement:
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                native.loop.call_soon_threadsafe(first_attempt.set)

    async def run():
        with session_order_scope(native.session.id, native.session.generation):
            await native.market._create_order(
                TradeType.SELL, 'resting-sell', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10.1'))
        assert native.market.in_flight_orders['resting-sell'].current_state is OrderState.OPEN
        tasks = []
        with native.engine.begin() as blocker:
            blocker.exec_driver_sql('SELECT bot_id FROM bot_state FOR UPDATE').all()
            blocked[0] = True
            started = time.monotonic()
            with session_order_scope(native.session.id, native.session.generation):
                tasks = [
                    asyncio.create_task(native.market._create_order(
                        TradeType.BUY, f'waiting-{i}', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                    for i in range(8)
                ]
            try:
                await asyncio.wait_for(first_attempt.wait(), 2)
                await asyncio.sleep(hold_seconds)
                assert all(not t.done() for t in tasks)
                assert not native.owner.submission_admission.locked()
                assert native.transport.call.await_count == 1 and not accepted and not failures.event_log
                if hold_seconds > 1:
                    assert 2 <= len(attempts) <= 6, 'Waiters must not each poll the DB during shared cooldown'
                else:
                    assert 1 <= len(attempts) <= (1 if fixed else 2)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        blocked[0] = False
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert native.market.in_flight_orders['resting-sell'].current_state is OrderState.OPEN
        return started

    event.listen(native.engine, 'before_cursor_execute', fence)
    try:
        started = native.loop.run_until_complete(run())
    finally:
        event.remove(native.engine, 'before_cursor_execute', fence)
        native.market.remove_listener(MarketEvent.OrderFailure, failures)
        native.market.remove_listener(MarketEvent.BuyOrderCreated, buy_created)
    assert not failures.event_log and native.transport.call.await_count == 9
    order_ids = {f'waiting-{i}' for i in range(8)}
    assert set(accepted) == order_ids
    with native.manager.get_new_session() as db:
        assert {x.id for x in db.query(Order)} == order_ids | {'resting-sell'}
        assert db.query(SubmissionAttempt).count() == 9
        assert {x.state for x in db.query(SubmissionAttempt)} == {'acknowledged'}
    assert native.manager.persistence_healthy and not native.store.read()['recovery_state'].get('halt')
    admissions = [row for row in diagnostics.snapshot()['submission_recent']
                  if row['operation'] == 'order.admission' and row['at_ns'] >= int(started * 1e9)]
    assert len(admissions) == len(order_ids)
    # With eight orders the nearest-rank p95 is the largest observation.
    report(f'admission_backoff_{hold_seconds}_{"fixed" if fixed else "adaptive"}', dict(
        samples=len(admissions), blocked_attempts=len(attempts),
        admission_p95_ms=max(row['duration_ms'] for row in admissions),
        buy_quote_gap_ms=(min(accepted.values()) - started) * 1000,
        scope='Real PostgreSQL, native connector, simulated venue acknowledgement'))
