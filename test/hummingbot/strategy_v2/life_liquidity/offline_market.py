"""Synthetic exchange observations for runner acceptance; no safety-gate bypass."""

from dataclasses import replace
from types import SimpleNamespace

from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.strategy_v2.life_liquidity.market_data import is_order_book_ready


async def install_market(controller, connector, *, exchange_ms, monotonic=100.0):
    """Only replace the production release switch; exercise all data gates."""
    controller.trading_permissions_ready = lambda: True
    state = {"now": monotonic}
    instrument = {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                  "tickSz": "0.01", "lotSz": "0.1", "minSz": "0.1",
                  "listTime": str(exchange_ms - 1000), "openType": "", "contTdSwTime": ""}

    async def metadata():
        return {"code": "0", "data": [instrument]}

    assert await controller.listing_gate.poll(monotonic, metadata)
    assert controller.continuous_gate.evaluate(
        instrument, {"code": "0", "data": [{"ts": str(exchange_ms)}]})
    controller.snapshot_gate.clock = lambda: state["now"]
    snapshot = {"ts": str(exchange_ms), "seqId": 10,
                "bids": [["0.98", "100", "0", "1"]],
                "asks": [["1.02", "100", "0", "1"]]}
    assert controller.snapshot_gate.evaluate(
        {"code": "0", "data": [snapshot]},
        exchange_now_ms=exchange_ms, received_monotonic=monotonic,
        market_state="live", continuous_trading_ready=True)
    state["health"] = BookFeedHealth(
        connected=True, synchronized=True, epoch=1, sequence_id=10, snapshot_sequence_id=10,
        snapshot_exchange_timestamp_ms=exchange_ms, snapshot_received_monotonic=monotonic,
        last_message_monotonic=monotonic, reason_code="BOOK_FEED_SYNCHRONIZED")
    assert controller.continuity_gate.confirm(
        state["health"], controller.snapshot_gate.snapshot, request_started_monotonic=monotonic)
    book = OrderBook()
    book.apply_snapshot([OrderBookRow(0.98, 100, 10)], [OrderBookRow(1.02, 100, 10)], 10)
    connector.ready = True
    connector.get_order_book = lambda pair: book
    connector.order_book_tracker = SimpleNamespace(
        order_books={"LIFE-USDT": book}, data_source=SimpleNamespace(
            book_feed_health=lambda pair: state["health"]))
    controller.market_data_provider = SimpleNamespace(
        connectors={"okx": connector}, get_connector_with_fallback=lambda name: connector)
    controller.book_ready = is_order_book_ready(connector, "LIFE-USDT")
    assert controller._spot_quote_gates_ready()
    return state


def disconnect(state):
    state["health"] = replace(state["health"], connected=False)
