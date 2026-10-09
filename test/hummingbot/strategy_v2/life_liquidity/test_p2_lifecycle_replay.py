"""Replay a synthetic LIFE listing lifecycle without exchange orders."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig


@pytest.mark.asyncio
async def test_unlisted_auction_live_disconnect_resync_and_stale_lifecycle():
    strategy = StrategyConfig.model_validate({
        "execution_mode": "simulation", "spot": {"enabled": True},
        "perpetual": {"enabled": True, "position_mode": "ONEWAY", "margin_mode": "cross", "leverage": 1},
        "session": {"duration": "4h"}, "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    })
    controller = LifeLiquidityController(
        LifeLiquidityConfig.model_construct(id="p2-replay", strategy=strategy), MagicMock(), MagicMock())
    controller.market_data_provider.ready = False
    controller.market_data_provider.initialize_order_book = AsyncMock(return_value=True)
    controller.trading_permissions_ready = lambda: True  # Isolate P2 permits; the live P4 gate stays disabled.
    controller._joint_risk_ready = lambda: True  # Isolate P2 from the new opt-in P6 risk gates.
    controller._hedge_ready = lambda: True
    state = {"phase": "unlisted", "now": 0, "server_ms": 2000, "book_ms": 1900,
             "health": BookFeedHealth(reason_code="BOOK_FEED_UNAVAILABLE")}
    book = SimpleNamespace(get_price=lambda is_buy: 1.1 if is_buy else 1)

    async def spot_get(*, path_url, params=None):
        if path_url == "/api/v5/public/instruments":
            data = [] if state["phase"] == "unlisted" else [{
                "instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                "openType": "call_auction", "listTime": "1000", "contTdSwTime": "3000",
            }]
            return {"code": "0", "data": data}
        if path_url == "/api/v5/public/time":
            return {"code": "0", "data": [{"ts": str(state["server_ms"])}]}
        assert path_url == "/api/v5/market/books"
        return {"code": "0", "data": [{
            "ts": str(state["book_ms"]), "seqId": 11, "bids": [["1", "10"]],
            "asks": [["1.1", "10"]],
        }]}

    async def swap_get(*, path_url, params=None):
        assert path_url == "/api/v5/public/instruments" and params == {"instType": "SWAP"}
        return {"code": "0", "data": []}

    spot = SimpleNamespace(
        _api_get=spot_get, trading_pairs_request_path="/api/v5/public/instruments",
        check_network_request_path="/api/v5/public/time", ready=True,
        get_order_book=lambda pair: book,
        order_book_tracker=SimpleNamespace(order_books={"LIFE-USDT": book},
                                           data_source=SimpleNamespace(book_feed_health=lambda pair: state["health"])),
    )
    swap = SimpleNamespace(_api_get=swap_get, trading_pairs_request_path="/api/v5/public/instruments")
    controller.market_data_provider.get_connector_with_fallback.side_effect = {
        "okx": spot, "okx_perpetual": swap,
    }.get
    controller.listing_gate.clock = lambda: state["now"]
    controller.perpetual_listing_gate.clock = lambda: state["now"]
    controller.snapshot_gate.clock = lambda: state["now"]

    async def advance(phase, now, server_ms, book_ms, health):
        state.update(phase=phase, now=now, server_ms=server_ms, book_ms=book_ms, health=health)
        await controller.control_task()
        await controller._perpetual_poll_task
        assert controller.processed_data["perpetual_contract_listed"] is False
        assert controller.processed_data["perpetual_quote_ready"] is False
        assert controller.determine_executor_actions() == []
        controller.actions_queue.put.assert_not_called()

    def healthy(epoch, received, exchange_ms, last):
        return BookFeedHealth(
            connected=True, synchronized=True, epoch=epoch, sequence_id=10,
            snapshot_exchange_timestamp_ms=exchange_ms, snapshot_received_monotonic=received,
            last_message_monotonic=last)

    await advance("unlisted", 0, 2000, 1900, state["health"])
    assert controller.processed_data["reason_code"] == "INSTRUMENT_NOT_FOUND"
    assert not controller.allow_create_executor_actions()

    await advance("auction", 5, 2000, 1900, healthy(1, 4, 1800, 5))
    assert controller.processed_data["reason_code"] == "CONTINUOUS_TRADING_NOT_STARTED"
    assert not controller.allow_create_executor_actions()

    await advance("live", 10, 3500, 3400, healthy(1, 9, 3300, 10))
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.allow_create_executor_actions()

    await advance("disconnected", 15, 3600, 3500,
                  BookFeedHealth(epoch=2, reason_code="BOOK_FEED_DISCONNECTED"))
    assert controller.processed_data["spot_market_data_ready"] is False
    assert not controller.allow_create_executor_actions()

    await advance("resync", 20, 4100, 3500, healthy(2, 19, 4000, 20))
    assert controller.processed_data["spot_market_data_ready"] is False
    assert not controller.allow_create_executor_actions()

    await advance("recovered", 25, 4200, 4050, healthy(2, 19, 4000, 25))
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.allow_create_executor_actions()

    state["now"] = 100
    assert not controller.allow_create_executor_actions()
