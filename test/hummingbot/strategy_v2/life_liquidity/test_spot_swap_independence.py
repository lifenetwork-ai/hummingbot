"""A missing LIFE swap never turns a listed LIFE spot into an unavailable market."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig


@pytest.mark.asyncio
async def test_spot_data_ready_while_configured_swap_is_unlisted_then_lists():
    data = {
        "execution_mode": "simulation", "spot": {"enabled": True},
        "perpetual": {"enabled": True, "position_mode": "ONEWAY", "margin_mode": "cross", "leverage": 1},
        "session": {"duration": "4h"}, "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    }
    config = LifeLiquidityConfig.model_construct(id="life-test", strategy=StrategyConfig.model_validate(data))
    startup_markets = {"okx": {"LIFE-USDT"}}
    assert config.update_markets(startup_markets) is startup_markets
    assert "okx_perpetual" not in startup_markets
    controller = LifeLiquidityController(config, MagicMock(), MagicMock())
    now = {"value": 1.0, "swap_listed": False, "swap_error": False, "swap_type": "linear"}

    async def spot_get(*, path_url, params=None):
        if "instruments" in path_url:
            assert params == {"instType": "SPOT"}
            item = {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                    "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                    "openType": "", "listTime": "1000", "contTdSwTime": ""}
            return {"code": "0", "data": [item]}
        if "time" in path_url:
            return {"code": "0", "data": [{"ts": "2000"}]}
        return {"code": "0", "data": [{"ts": "1900", "seqId": 11,
                                      "bids": [["1", "10"]], "asks": [["1.1", "10"]]}]}

    async def swap_get(*, path_url, params=None):
        assert params == {"instType": "SWAP"}
        if now["swap_error"]:
            raise OSError("swap metadata unavailable")
        item = {"instType": "SWAP", "instId": "LIFE-USDT-SWAP", "state": "live",
                "ctType": now["swap_type"], "ctVal": "0.25", "ctMult": "1",
                "ctValCcy": "LIFE", "settleCcy": "USDT",
                "tickSz": "0.0001", "lotSz": "1", "minSz": "1"}
        return {"code": "0", "data": [item] if now["swap_listed"] else []}

    spot = SimpleNamespace(
        _api_get=spot_get, trading_pairs_request_path="/api/v5/public/instruments",
        check_network_request_path="/api/v5/public/time", ready=True,
        get_order_book=lambda pair: SimpleNamespace(get_price=lambda is_buy: 1.1 if is_buy else 1),
    )
    swap = SimpleNamespace(_api_get=swap_get, trading_pairs_request_path="/api/v5/public/instruments")
    controller.market_data_provider.get_connector_with_fallback.side_effect = {
        "okx": spot, "okx_perpetual": swap,
    }.get
    controller.market_data_provider.initialize_order_book = AsyncMock(return_value=True)
    controller.market_data_provider.ready = False
    controller.listing_gate.clock = lambda: now["value"]
    controller.perpetual_listing_gate.clock = lambda: now["value"]
    controller.snapshot_gate.clock = lambda: now["value"]
    controller._book_feed_health = lambda: BookFeedHealth(
        connected=True, synchronized=True, epoch=1, sequence_id=10,
        snapshot_exchange_timestamp_ms=1800, snapshot_received_monotonic=0,
        last_message_monotonic=0)

    async def control_and_poll_swap():
        await controller.control_task()
        await controller._perpetual_poll_task

    await control_and_poll_swap()
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.processed_data["perpetual_contract_listed"] is False
    assert controller.processed_data["perpetual_metadata_ready"] is False
    assert controller.processed_data["perpetual_reason_code"] == "INSTRUMENT_NOT_FOUND"
    assert controller.processed_data["perpetual_quote_ready"] is False
    controller.market_data_provider.initialize_order_book.assert_awaited_once_with("okx", "LIFE-USDT")

    now["value"] = 6.0
    now["swap_listed"] = True
    await control_and_poll_swap()
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.processed_data["perpetual_contract_listed"] is True
    assert controller.processed_data["perpetual_metadata_ready"] is True
    assert controller.perpetual_contract.contracts_to_life(Decimal("4")) == Decimal("1.00")
    assert controller.processed_data["perpetual_contract_value_life"] == "0.25"
    assert controller.processed_data["perpetual_quote_ready"] is False
    controller.market_data_provider.initialize_order_book.assert_awaited_once_with("okx", "LIFE-USDT")

    now["value"] = 11.0
    now["swap_type"] = "inverse"
    await control_and_poll_swap()
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.processed_data["perpetual_contract_listed"] is True
    assert controller.processed_data["perpetual_metadata_ready"] is False
    assert controller.processed_data["perpetual_reason_code"] == "SWAP_CONTRACT_TYPE_INVALID"
    assert controller.perpetual_contract is None

    now["value"] = 16.0
    now["swap_error"] = True
    await control_and_poll_swap()
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.processed_data["perpetual_contract_listed"] is False
    assert controller.processed_data["perpetual_reason_code"] == "INSTRUMENT_FETCH_ERROR"


@pytest.mark.asyncio
async def test_slow_swap_metadata_does_not_delay_spot_book_readiness(monkeypatch):
    data = {
        "execution_mode": "simulation", "spot": {"enabled": True},
        "perpetual": {"enabled": True, "position_mode": "ONEWAY", "margin_mode": "cross", "leverage": 1},
        "session": {"duration": "4h"}, "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    }
    config = LifeLiquidityConfig.model_construct(id="slow-swap", strategy=StrategyConfig.model_validate(data))
    controller = LifeLiquidityController(config, MagicMock(), MagicMock())
    controller.market_data_provider.ready = False
    controller.market_data_provider.initialize_order_book = AsyncMock(return_value=True)
    controller.market_data_provider.get_connector_with_fallback.return_value = SimpleNamespace(
        ready=True, get_order_book=lambda pair: SimpleNamespace(
            get_price=lambda is_buy: 1.1 if is_buy else 1))
    controller.listing_gate.clock = lambda: 0
    controller.perpetual_listing_gate.clock = lambda: 0
    controller.snapshot_gate.clock = lambda: 0
    controller._book_feed_health = lambda: BookFeedHealth(
        connected=True, synchronized=True, epoch=1, sequence_id=10,
        snapshot_exchange_timestamp_ms=1800, snapshot_received_monotonic=0,
        last_message_monotonic=0)
    swap_started, release_swap = asyncio.Event(), asyncio.Event()

    class StubSource:
        def __init__(self, provider, connector_name, inst_type):
            self.inst_type = inst_type

        async def fetch(self):
            if self.inst_type == "SWAP":
                swap_started.set()
                await release_swap.wait()
                return {"code": "0", "data": []}
            return {"code": "0", "data": [{
                "instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                "openType": "", "listTime": "1000", "contTdSwTime": "",
            }]}

        async def fetch_order_book(self, pair):
            return {"code": "0", "data": [{
                "ts": "1900", "seqId": 11, "bids": [["1", "10"]], "asks": [["1.1", "10"]],
            }]}

        async def fetch_server_time(self):
            return {"code": "0", "data": [{"ts": "2000"}]}

    monkeypatch.setattr("controllers.generic.life_liquidity.OkxInstrumentSource", StubSource)
    try:
        await asyncio.wait_for(controller.control_task(), timeout=0.5)
        await asyncio.wait_for(swap_started.wait(), timeout=0.5)
        pending_poll = controller._perpetual_poll_task
        await asyncio.wait_for(controller.control_task(), timeout=0.5)
        assert controller._perpetual_poll_task is pending_poll
        assert controller.processed_data["spot_market_data_ready"] is True
        assert controller.processed_data["perpetual_contract_listed"] is False
    finally:
        release_swap.set()
        task = getattr(controller, "_perpetual_poll_task", None)
        if task is not None:
            await task
