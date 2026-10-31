"""No-transport proof must live exactly as long as the rejected order object."""
import gc
import weakref
from decimal import Decimal
from unittest.mock import MagicMock

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState


def rejected(identity):
    order = InFlightOrder(client_order_id=identity, trading_pair="BTC-USDT", order_type=OrderType.LIMIT,
                          trade_type=TradeType.BUY, amount=Decimal(".1"), price=Decimal("100"),
                          creation_timestamp=1700000000)
    order.current_state = OrderState.FAILED
    return order


def test_rejection_churn_retires_only_unreferenced_orders():
    owner = BotOwnership(MagicMock(), "test")
    retained = [rejected(f"retained-{index}") for index in range(3)]
    for order in retained:
        owner.order_failed(None, order)
    for index in range(10000):
        order = rejected(f"retired-{index}")
        owner.order_failed(None, order)
    retired = weakref.ref(order)
    del order
    gc.collect()
    recorder = MagicMock()
    recorder.sql_manager.assert_persistence_healthy = MagicMock()
    recorder.sql_manager.get_new_session.return_value.__enter__.return_value.query.return_value.filter.return_value.all.return_value = []
    owner._check_intents(recorder)
    assert retired() is None
    assert len(owner.unsent_rejections) == len(retained)
    assert all(order.client_order_id in owner.unsent_rejections for order in retained)
    retained.clear()
    gc.collect()
    assert len(owner.unsent_rejections) == 0


def test_retained_cancelled_order_keeps_proof_until_shutdown_releases_it():
    owner = BotOwnership(MagicMock(), "test")
    order = rejected("captured-by-shutdown")
    owner.order_failed(None, order)
    captured = [order]
    order.current_state = OrderState.CANCELED
    del order
    gc.collect()
    assert "captured-by-shutdown" in owner.unsent_rejections
    captured.clear()
    gc.collect()
    assert len(owner.unsent_rejections) == 0
