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

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill

owned = owned_fixture


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
    with owned.engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_cancel_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(.45);
                RETURN NEW;
            END
            $$
        """))
        connection.execute(text("""
            CREATE CONSTRAINT TRIGGER delay_cancel_commit
            AFTER INSERT ON order_statuses DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (NEW.status = 'OrderCancelled')
            EXECUTE FUNCTION delay_cancel_commit()
        """))

    committing = threading.Event()

    def watch_flush(session, context, instances):
        if any(isinstance(value, OrderStatus) and value.status == 'OrderCancelled' for value in session.new):
            committing.set()

    event.listen(owned.manager._session_cls, 'before_flush', watch_flush)

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)
        fill_started = threading.Event()

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]

        def probe():
            assert fill_started.wait(2)
            return probe_health(port, '/live')

        async def apply_fill():
            fill_started.set()
            await tracker.apply_trade_update(trade)

        try:
            probe_job = asyncio.get_running_loop().run_in_executor(None, probe)
            update = OrderUpdate('BTC-USDT', 1700000001, OrderState.CANCELED, 'order-1', 'exchange-1')
            cancel = tracker.process_order_update(update)
            assert await asyncio.to_thread(committing.wait, 2)
            if concurrent == 'cancelled-waiter':
                cancel.cancel()
            fill_task = asyncio.create_task(apply_fill())
            # The late POST response wants the same journal row as the
            # cancellation. It must wait without blocking the probe too.
            request = SimpleNamespace(url='https://offline.invalid/api/v5/trade/order', data='{"clOrdId":"order-1"}')
            response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))
            receipt_task = asyncio.create_task(owned.owner.record_transport_receipt(request, response))
            if stop_requested:
                stop = asyncio.create_task(owned.owner.shutdown_core(core, timeout=2))
                await asyncio.sleep(0)
                assert not owned.owner.allow_new_orders
                rest, transport = assistant()
            admit = asyncio.create_task(owned.owner.admit_order(owned.market, next_order))
            healthy, detail = await probe_job
            assert healthy and detail['elapsed_ms'] < 300, detail
            report(f'managed_recorder_probe_{concurrent}', {'elapsed_ms': detail['elapsed_ms'], 'status': detail['status']})
            assert order.current_state is OrderState.OPEN
            assert not peer.executed_amount_base
            owned.market.trigger_event.assert_not_called()

            if stop_requested:
                send_task = asyncio.create_task(send(rest))
                with pytest.raises((SubmissionBlocked, asyncio.CancelledError)):
                    await send_task
                transport.call.assert_not_awaited()
                with pytest.raises(SubmissionBlocked):
                    await admit
                assert await stop
            else:
                await admit
            if concurrent == 'cancelled-waiter':
                with pytest.raises(asyncio.CancelledError):
                    await cancel
            else:
                await cancel
            await fill_task
            await receipt_task
            await tracker.process_order_update(update)
            await tracker.apply_trade_update(trade)
        finally:
            await runner.cleanup()

    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.manager._session_cls, 'before_flush', watch_flush)
    assert [call.args[0] for call in owned.market.trigger_event.call_args_list] == [
        MarketEvent.OrderCancelled, MarketEvent.OrderFilled]
    assert order.current_state is OrderState.CANCELED
    with owned.manager.get_new_session() as session:
        assert session.query(OrderStatus).filter_by(status='OrderCancelled').count() == 1
        assert session.query(TradeFill).count() == 1
        assert (session.get(Order, 'order-3') is None) == stop_requested
        assert session.query(MarketState).one().saved_state['order-1']['last_state'] == str(OrderState.CANCELED.value)
