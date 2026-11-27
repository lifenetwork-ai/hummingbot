"""Managed tracker events must await a durable write without blocking engine HTTP."""
import asyncio
import threading
from dataclasses import replace
from test.integration.postgres.test_ownership import (
    assistant,
    intent,
    owned as owned_fixture,
    reconciliation_core,
    send,
)
from test.integration.postgres.test_performance import report
from test.integration.postgres.test_recorder import create, fill
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.connector import markets_recorder
from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill

owned = owned_fixture


@pytest.mark.parametrize(('new_state', 'tag', 'cancel_waiter'), [
    (OrderState.OPEN, MarketEvent.BuyOrderCreated, False),
    (OrderState.FILLED, MarketEvent.BuyOrderCompleted, False),
    (OrderState.FAILED, MarketEvent.OrderFailure, False),
    (OrderState.CANCELED, MarketEvent.OrderCancelled, False),
    (OrderState.OPEN, MarketEvent.OrderFilled, False),
    (OrderState.OPEN, MarketEvent.OrderFilled, True),
])
def test_managed_order_update_keeps_probe_live_during_ownership_row_wait(owned, new_state, tag, cancel_waiter):
    tracker = ClientOrderTracker(owned.market)
    order = intent(owned)
    if tag is not MarketEvent.BuyOrderCreated:
        order.update_exchange_order_id('exchange-1')
        order.current_state = OrderState.OPEN
        owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, create())
    if new_state is OrderState.FILLED:
        order.executed_amount_base = order.amount
        order.executed_amount_quote = order.amount * order.price
        order.check_filled_condition()
    tracker.start_tracking_order(order)
    owned.market.current_timestamp = 1700000001
    initial_state = order.current_state

    def dispatch(event_tag, payload):
        assert event_tag is tag
        owned.market.tracking_states[order.client_order_id] = order.to_json()
        if tag is MarketEvent.BuyOrderCreated:
            owned.recorder._did_create_order(tag.value, owned.market, payload)
        elif tag is MarketEvent.OrderFilled:
            owned.recorder._did_fill_order(tag.value, owned.market, payload)
        else:
            owned.recorder._update_order_status(tag.value, owned.market, payload)
        with owned.manager.get_new_session() as session:
            assert session.get(Order, order.client_order_id).last_status == tag.name

    owned.market.trigger_event.side_effect = dispatch
    row_locked = threading.Event()
    fence_started = threading.Event()

    def hold_row():
        with owned.engine.begin() as connection:
            connection.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            row_locked.set()
            assert fence_started.wait(2)
            # Exceed the configured PostgreSQL lock_timeout with the actual
            # recorder holding the event until a durable receipt is available.
            threading.Event().wait(1.25)

    def detect_fence(connection, cursor, statement, parameters, context, executemany):
        if 'FOR SHARE' in statement:
            fence_started.set()

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]

        def probe():
            assert fence_started.wait(2)
            return probe_health(port, '/live')

        loop = asyncio.get_running_loop()
        if tag is MarketEvent.OrderFilled:
            row = fill()
            trade = TradeUpdate(row.exchange_trade_id, order.client_order_id, 'exchange-1', row.trading_pair,
                                row.timestamp, row.price, row.amount, row.price * row.amount, row.trade_fee)

            async def apply():
                await tracker.apply_trade_update(trade)
        else:
            update = OrderUpdate(order.trading_pair, 1700000001, new_state, order.client_order_id, 'exchange-1')

            async def apply():
                await tracker._process_order_update(update)

        blocker = loop.run_in_executor(None, hold_row)
        try:
            assert await asyncio.to_thread(row_locked.wait, 2)
            probing = loop.run_in_executor(None, probe)
            applying = loop.create_task(apply())
            healthy, detail = await probing
            assert healthy and detail['elapsed_ms'] < 300, detail
            report(f'managed_order_fence_probe_{tag.name}_{cancel_waiter}',
                   {'elapsed_ms': detail['elapsed_ms'], 'status': detail['status']})
            assert not applying.done()
            assert order.current_state is initial_state
            owned.market.trigger_event.assert_not_called()
            if cancel_waiter:
                applying.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await applying
            else:
                await applying
            await apply()
            assert owned.manager.persistence_healthy
            assert owned.owner.allow_new_orders and not owned.owner.lost
        finally:
            await blocker
            await runner.cleanup()

    event.listen(owned.engine, 'before_cursor_execute', detect_fence)
    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.engine, 'before_cursor_execute', detect_fence)
    assert order.current_state is new_state
    owned.market.trigger_event.assert_called_once()
    with owned.manager.get_new_session() as session:
        assert session.query(OrderStatus).filter_by(status=tag.name).count() == 1
        state = session.query(MarketState).one().saved_state[order.client_order_id]
        assert state['last_state'] == str(new_state.value)
        if tag is MarketEvent.OrderFilled:
            assert state['executed_amount_base'] == str(fill().amount)
            assert session.query(TradeFill).count() == 1


@pytest.fixture
def tracked_fill(owned):
    order = intent(owned)
    order.update_exchange_order_id('exchange-1')
    order.current_state = OrderState.OPEN
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, create())
    owned.market.current_timestamp = 1700000001
    tracker = ClientOrderTracker(owned.market)
    tracker.start_tracking_order(order)
    row = fill()
    trade = TradeUpdate(row.exchange_trade_id, order.client_order_id, 'exchange-1', row.trading_pair,
                        row.timestamp, row.price, row.amount, row.price * row.amount, row.trade_fee)
    return tracker, order, trade


def test_managed_fill_row_wait_expires_without_acknowledging(owned, tracked_fill, monkeypatch):
    tracker, order, trade = tracked_fill
    monkeypatch.setattr(markets_recorder, '_ORDER_FENCE_RETRY_SECONDS', .25)
    attempts = []

    def mark_attempt(connection, cursor, statement, *args):
        if 'FOR SHARE NOWAIT' in statement:
            attempts.append(owned.manager.persistence_healthy)

    event.listen(owned.engine, 'before_cursor_execute', mark_attempt)
    try:
        with owned.engine.begin() as blocker:
            blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            with pytest.raises(OperationalError) as failure:
                owned.loop.run_until_complete(tracker.apply_trade_update(trade))
    finally:
        event.remove(owned.engine, 'before_cursor_execute', mark_attempt)
    assert failure.value.orig.pgcode == '55P03'
    assert len(attempts) >= 2 and all(attempts)
    assert not order.executed_amount_base and not owned.manager.persistence_healthy
    owned.market.trigger_event.assert_not_called()
    with owned.manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 0


def test_managed_fill_commit_lock_error_is_never_retried(owned, tracked_fill):
    tracker, order, trade = tracked_fill
    with owned.engine.begin() as conn:
        conn.execute(text("""
            CREATE FUNCTION fail_fill_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected late lock failure' USING ERRCODE = '55P03';
            END
            $$
        """))
        conn.execute(text("""
            CREATE CONSTRAINT TRIGGER fail_fill_commit
            AFTER INSERT ON trade_fills DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION fail_fill_commit()
        """))
    inserted = []

    def mark_insert(connection, cursor, statement, *args):
        if statement.startswith('INSERT INTO trade_fills'):
            inserted.append(True)

    event.listen(owned.engine, 'before_cursor_execute', mark_insert)
    try:
        with pytest.raises(OperationalError) as failure:
            owned.loop.run_until_complete(tracker.apply_trade_update(trade))
    finally:
        event.remove(owned.engine, 'before_cursor_execute', mark_insert)
    assert failure.value.orig.pgcode == '55P03'
    assert inserted == [True] and not owned.manager.persistence_healthy
    assert not order.executed_amount_base
    owned.market.trigger_event.assert_not_called()
    with owned.manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 0


def test_unrecorded_failure_does_not_wait_for_ownership_row(owned):
    original = create()
    order = InFlightOrder('rejected-before-admission', original.trading_pair, original.type, TradeType.BUY,
                          original.amount, original.creation_timestamp, original.price)
    owned.market.current_timestamp = 1700000001
    tracker = ClientOrderTracker(owned.market)
    tracker.start_tracking_order(order)
    update = OrderUpdate(order.trading_pair, 1700000001, OrderState.FAILED, order.client_order_id)
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        owned.loop.run_until_complete(asyncio.wait_for(tracker._process_order_update(update), timeout=.3))
    assert order.is_failure and owned.manager.persistence_healthy
    assert owned.market.trigger_event.call_args.args[0] is MarketEvent.OrderFailure
    with owned.manager.get_new_session() as session:
        assert session.get(Order, order.client_order_id) is None


@pytest.mark.parametrize('after_flush', [False, True])
def test_managed_ack_rolls_back_when_physical_owner_lock_is_lost(owned, after_flush):
    order = intent(owned)
    tracker = ClientOrderTracker(owned.market)
    tracker.start_tracking_order(order)
    owned.market.current_timestamp = 1700000001
    reached = threading.Event()
    resume = threading.Event()
    checkpoint_flushed = False

    def before_query(connection, cursor, statement, *args):
        if connection is owned.owner.connection:
            assert threading.current_thread() is threading.main_thread()
            return
        if 'SELECT 1 FROM pg_locks' in statement and checkpoint_flushed == after_flush and not reached.is_set():
            reached.set()
            assert resume.wait(2)

    def after_query(connection, cursor, statement, *args):
        nonlocal checkpoint_flushed
        if statement.startswith('UPDATE market_states'):
            checkpoint_flushed = True

    async def exercise():
        update = OrderUpdate(order.trading_pair, 1700000001, OrderState.OPEN, order.client_order_id, 'exchange-1')
        task = asyncio.create_task(tracker._process_order_update(update))
        try:
            assert await asyncio.to_thread(reached.wait, 2)
            owned.owner.connection.execute(text('SELECT pg_advisory_unlock_all()'))
            owned.owner.connection.commit()
        finally:
            resume.set()
        with pytest.raises(OwnershipLost) as raised:
            await task
        assert raised.value.reason_code == 'ownership_identity_mismatch'

    event.listen(owned.engine, 'before_cursor_execute', before_query)
    event.listen(owned.engine, 'after_cursor_execute', after_query)
    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.engine, 'before_cursor_execute', before_query)
        event.remove(owned.engine, 'after_cursor_execute', after_query)
    assert owned.owner.lost and not owned.owner.allow_new_orders
    assert not owned.manager.persistence_healthy
    assert order.is_pending_create and order.exchange_order_id is None
    assert not order.processed_by_exchange_event.is_set()
    owned.market.trigger_event.assert_not_called()
    with owned.manager.get_new_session() as session:
        assert session.get(Order, order.client_order_id).last_status == 'PendingBuy'
        assert session.get(SubmissionAttempt, order.client_order_id).state == 'prepared'
        assert session.query(OrderStatus).count() == 1
        state = session.query(MarketState).one().saved_state[order.client_order_id]
        assert state['last_state'] == str(OrderState.PENDING_CREATE.value)


@pytest.mark.parametrize('concurrent', ['intent', 'stop', 'cancelled-waiter'])
def test_managed_cancel_waits_for_postgres_commit_without_blocking_live(owned, concurrent):
    stop_requested = concurrent == 'stop'
    tracker = ClientOrderTracker(owned.market)

    def open_order(identity, exchange_id):
        order = intent(owned, identity)
        order.update_exchange_order_id(exchange_id)
        order.current_state = OrderState.OPEN
        owned.market.tracking_states[identity] = order.to_json()
        record = replace(create(), order_id=identity, exchange_order_id=exchange_id)
        owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, record)
        tracker.start_tracking_order(order)
        return order

    order = open_order('order-1', 'exchange-1')
    peer = open_order('order-2', 'exchange-2')
    values = fill(order_id='order-2')
    trade = TradeUpdate('trade-2', 'order-2', 'exchange-2', peer.trading_pair, values.timestamp,
                        values.price, values.amount, values.price * values.amount, AddedToCostTradeFee())
    next_order = InFlightOrder('order-3', order.trading_pair, order.order_type, order.trade_type, order.amount,
                               order.creation_timestamp, order.price)
    owned.market.in_flight_orders[next_order.client_order_id] = next_order
    owned.market.tracking_states[next_order.client_order_id] = next_order.to_json()
    owned.market.current_timestamp = 1700000001
    core = reconciliation_core(owned)
    core.shutdown = AsyncMock(return_value=True)

    def dispatch(tag, payload):
        native = tracker.fetch_order(payload.order_id)
        owned.market.tracking_states[payload.order_id] = native.to_json()
        if tag is MarketEvent.OrderCancelled:
            owned.recorder._did_cancel_order(tag.value, owned.market, payload)
            with owned.manager.get_new_session() as session:
                assert session.get(Order, 'order-1').last_status == 'OrderCancelled'
        else:
            assert tag is MarketEvent.OrderFilled
            owned.recorder._did_fill_order(tag.value, owned.market, payload)
            with owned.manager.get_new_session() as session:
                assert session.get(TradeFill, ('okx', 'order-2', 'trade-2')) is not None

    owned.market.trigger_event.side_effect = dispatch
    committing = threading.Event()
    release_commit = threading.Event()
    engine_thread = threading.get_ident()

    def hold_cancel_commit(session):
        # The cancellation is the only worker transaction started before this
        # barrier. Both the ORM and batched writes reach it after their SQL.
        if session.info.get('worker_order_owner') is owned.owner and not committing.is_set():
            assert threading.get_ident() != engine_thread
            committing.set()
            assert release_commit.wait(5), 'Probe did not release the cancellation commit'

    event.listen(owned.manager._session_cls, 'before_commit', hold_cancel_commit)

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)
        fill_started = asyncio.Event()

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]

        async def apply_fill():
            fill_started.set()
            await tracker.apply_trade_update(trade)

        tasks = []
        try:
            update = OrderUpdate('BTC-USDT', 1700000001, OrderState.CANCELED, 'order-1', 'exchange-1')
            cancel = tracker.process_order_update(update)
            tasks.append(cancel)
            assert await asyncio.to_thread(committing.wait, 2), 'Cancellation did not reach commit'
            if concurrent == 'cancelled-waiter':
                cancel.cancel()
            fill_task = asyncio.create_task(apply_fill())
            # The late POST response wants the same journal row as the
            # cancellation. It must wait without blocking the probe too.
            request = SimpleNamespace(url='https://offline.invalid/api/v5/trade/order', data='{"clOrdId":"order-1"}')
            response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))
            receipt_task = asyncio.create_task(owned.owner.record_transport_receipt(request, response))
            tasks.extend([fill_task, receipt_task])
            if stop_requested:
                stop = asyncio.create_task(owned.owner.shutdown_core(core, timeout=2))
                tasks.append(stop)
                await asyncio.sleep(0)
                assert not owned.owner.allow_new_orders
                rest, transport = assistant()
            admit = asyncio.create_task(owned.owner.admit_order(owned.market, next_order))
            tasks.append(admit)
            await asyncio.wait_for(fill_started.wait(), 2)
            healthy, detail = await asyncio.to_thread(probe_health, port, '/live')
            assert healthy and detail['elapsed_ms'] < 300, detail
            report(f'managed_recorder_probe_{concurrent}', {'elapsed_ms': detail['elapsed_ms'], 'status': detail['status']})
            assert order.current_state is OrderState.OPEN
            assert not cancel.done()
            assert not peer.executed_amount_base
            owned.market.trigger_event.assert_not_called()
            release_commit.set()

            if stop_requested:
                send_task = asyncio.create_task(send(rest))
                with pytest.raises((SubmissionBlocked, asyncio.CancelledError)):
                    await send_task
                transport.call.assert_not_awaited()
                with pytest.raises((SubmissionBlocked, asyncio.CancelledError)):
                    await admit
                assert await stop
            else:
                await admit
            if concurrent == 'cancelled-waiter':
                with pytest.raises(asyncio.CancelledError):
                    await cancel
            else:
                await cancel
            try:
                await fill_task
            except asyncio.CancelledError:
                assert stop_requested  # Stop can cancel the waiter after the fill commits.
            await receipt_task
            await tracker.process_order_update(update)
            await tracker.apply_trade_update(trade)
        finally:
            release_commit.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.cleanup()

    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.manager._session_cls, 'before_commit', hold_cancel_commit)
    assert [call.args[0] for call in owned.market.trigger_event.call_args_list] == [
        MarketEvent.OrderCancelled, MarketEvent.OrderFilled]
    assert order.current_state is OrderState.CANCELED
    with owned.manager.get_new_session() as session:
        assert session.query(OrderStatus).filter_by(status='OrderCancelled').count() == 1
        assert session.query(TradeFill).count() == 1
        assert (session.get(Order, 'order-3') is None) == stop_requested
        assert session.query(MarketState).one().saved_state['order-1']['last_state'] == str(OrderState.CANCELED.value)
