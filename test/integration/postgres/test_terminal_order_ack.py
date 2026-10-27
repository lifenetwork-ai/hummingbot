"""A positively identified cancel can precede the create acknowledgement."""
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import text

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent, OrderCancelledEvent, SellOrderCreatedEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.order import Order

owned = _owned


def setup_market(context, name, side=TradeType.BUY):
    context.market.name = context.market.display_name = name
    context.owner.connectors = {name: context.market}
    order = InFlightOrder(client_order_id='order-1', trading_pair='BTC-USDT', order_type=OrderType.LIMIT,
                          trade_type=side, amount=Decimal('.1'), price=Decimal('100'), creation_timestamp=1700000000)
    context.market.in_flight_orders[order.client_order_id] = order
    context.market.tracking_states[order.client_order_id] = order.to_json()
    context.owner.before_order(context.market, order)
    return order


@pytest.mark.parametrize('market', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_terminal_cancel_ack_before_create_resolves_intent_without_reopening(owned, market, side):
    order = setup_market(owned, market, side)
    owned.market.in_flight_orders.clear()  # The native tracker retires a canceled order.
    owned.recorder._did_cancel_order(MarketEvent.OrderCancelled.value, owned.market,
                                     OrderCancelledEvent(time.time(), order.client_order_id, 'exchange-1'))
    with owned.manager.get_new_session() as session:
        row = session.get(Order, order.client_order_id)
        assert not row.creation_pending
        assert row.exchange_order_id == 'exchange-1'
        assert row.last_status == 'OrderCancelled'
        assert AdminStore._order_sides(session, [row])[row.id] == side.name
    owned.owner._check_intents(owned.recorder)
    assert owned.owner.allow_new_orders and not owned.store.read()['recovery_state'].get('halt')
    event_class = BuyOrderCreatedEvent if side == TradeType.BUY else SellOrderCreatedEvent
    event_tag = MarketEvent.BuyOrderCreated if side == TradeType.BUY else MarketEvent.SellOrderCreated
    owned.recorder._did_create_order(event_tag.value, owned.market, event_class(
        timestamp=time.time(), type=order.order_type, trading_pair=order.trading_pair, amount=order.amount,
        price=order.price, order_id=order.client_order_id, creation_timestamp=order.creation_timestamp,
        exchange_order_id='exchange-1'))
    with owned.manager.get_new_session() as session:
        row = session.get(Order, order.client_order_id)
        assert row.last_status == 'OrderCancelled' and not row.creation_pending
        assert AdminStore._order_sides(session, [row])[row.id] == side.name


@pytest.mark.parametrize('market', ['okx', 'okx_perpetual'])
def test_terminal_cancel_without_exchange_identity_still_requires_recovery(owned, market):
    order = setup_market(owned, market)
    owned.market.in_flight_orders.clear()
    owned.recorder._did_cancel_order(MarketEvent.OrderCancelled.value, owned.market,
                                     OrderCancelledEvent(time.time(), order.client_order_id))
    with owned.manager.get_new_session() as session:
        assert session.get(Order, order.client_order_id).creation_pending
    with pytest.raises(SubmissionBlocked):
        owned.owner._check_intents(owned.recorder)
    assert owned.store.read()['recovery_state']['halt']


def test_terminal_cancel_conflicting_exchange_identity_rolls_back(owned):
    order = setup_market(owned, 'okx')
    with owned.manager.get_new_session() as session, session.begin():
        session.get(Order, order.client_order_id).exchange_order_id = 'original'
    with pytest.raises(ValueError):
        owned.recorder._did_cancel_order(MarketEvent.OrderCancelled.value, owned.market,
                                         OrderCancelledEvent(time.time(), order.client_order_id, 'different'))
    with owned.engine.connect() as connection:
        row = connection.execute(text('SELECT creation_pending,exchange_order_id,last_status FROM orders')).one()
        assert tuple(row) == (True, 'original', 'PendingBuy')
