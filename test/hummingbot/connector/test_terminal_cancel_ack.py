"""Local cancel acknowledgments cannot reopen a completed native identity."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import MarketEvent


def native_tracker(exchange_order_id="venue"):
    connector = SimpleNamespace(current_timestamp=10, trigger_event=Mock())
    tracker = ClientOrderTracker(connector)
    order = InFlightOrder(client_order_id='order', exchange_order_id=exchange_order_id, trading_pair='BTC-USDT',
                          order_type=OrderType.LIMIT, trade_type=TradeType.BUY, amount=Decimal('1'),
                          price=Decimal('10'), creation_timestamp=1, initial_state=OrderState.OPEN)
    tracker.start_tracking_order(order)
    return tracker, order, connector


def update(state, at):
    return OrderUpdate(trading_pair='BTC-USDT', update_timestamp=at, new_state=state, client_order_id='order')


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [OrderState.FAILED, OrderState.CANCELED, OrderState.FILLED])
async def test_queued_pending_cancel_cannot_reopen_terminal_state(terminal):
    tracker, order, connector = native_tracker()
    if terminal == OrderState.FILLED:
        order.executed_amount_base = order.amount
        order.executed_amount_quote = order.amount * order.price
    # Both notifications are queued before either task has executed. Checking
    # is_done in the caller before enqueueing does not close this race.
    await asyncio.gather(tracker.process_order_update(update(terminal, 2)),
                         tracker.process_order_update(update(OrderState.PENDING_CANCEL, 3)))
    assert order.current_state == terminal and order.is_done
    assert tracker.fetch_cached_order('order') is order
    assert 'order' not in tracker.active_orders
    if terminal in (OrderState.FAILED, OrderState.CANCELED):
        trade = TradeUpdate(trade_id='late-fill', client_order_id='order', exchange_order_id='venue',
                            trading_pair='BTC-USDT', fill_timestamp=4, fill_price=Decimal('10'),
                            fill_base_amount=Decimal('.2'), fill_quote_amount=Decimal('2'), fee=AddedToCostTradeFee())
        tracker.process_trade_update(trade)
        tracker.process_trade_update(trade)
        assert order.executed_amount_base == Decimal('.2')
        fills = [call for call in connector.trigger_event.call_args_list if call.args[0] == MarketEvent.OrderFilled]
        assert len(fills) == 1


@pytest.mark.asyncio
async def test_live_order_keeps_pending_cancel_and_accepts_final_cancel():
    tracker, order, _ = native_tracker()
    await tracker.process_order_update(update(OrderState.PENDING_CANCEL, 2))
    assert order.current_state == OrderState.PENDING_CANCEL and not order.is_done
    await tracker.process_order_update(update(OrderState.CANCELED, 3))
    assert order.current_state == OrderState.CANCELED


@pytest.mark.asyncio
async def test_late_exchange_creation_is_not_treated_as_a_local_cancel_ack():
    tracker, order, _ = native_tracker()
    await tracker.process_order_update(update(OrderState.FAILED, 2))
    await tracker.process_order_update(update(OrderState.OPEN, 3))
    assert order.current_state == OrderState.OPEN


@pytest.mark.asyncio
async def test_late_cancel_ack_preserves_new_exchange_identity_without_reopening():
    tracker, order, _ = native_tracker(exchange_order_id=None)
    await tracker.process_order_update(update(OrderState.FAILED, 2))
    acknowledgment = OrderUpdate(trading_pair='BTC-USDT', update_timestamp=3,
                                 new_state=OrderState.PENDING_CANCEL, client_order_id='order', exchange_order_id='late-venue')
    await tracker.process_order_update(acknowledgment)
    assert order.current_state == OrderState.FAILED
    assert order.exchange_order_id == 'late-venue'
