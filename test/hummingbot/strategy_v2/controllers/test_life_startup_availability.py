"""Only read-only controller initialization may retry in place."""
import asyncio
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import PositionMode


@pytest.mark.asyncio
async def test_public_metadata_timeout_retries_same_controller_without_order_side_effects(monkeypatch):
    item, _ = make_controller(configuration(scenario="follow_token", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source.is_trading_required = False
    source._update_trading_rules = AsyncMock(side_effect=[TimeoutError("secret"), None])
    item.market_data_provider.acquire_order_books = AsyncMock(return_value={"BTC-USDT": True})
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    await item.on_start()
    assert source._update_trading_rules.await_count == 2
    item.market_data_provider.acquire_order_books.assert_awaited_once()
    assert item.actions_queue.empty() and item._startup_retry is None
    source._set_trading_pair_leverage.assert_not_called()


@pytest.mark.asyncio
async def test_account_mode_read_can_retry_but_leverage_mutation_cannot(monkeypatch):
    item, _ = make_controller(configuration(connector_name="okx_perpetual"))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(side_effect=[TimeoutError(), PositionMode.HEDGE])
    source._set_trading_pair_leverage = AsyncMock(side_effect=TimeoutError("uncertain mutation"))
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    with pytest.raises(TimeoutError):
        await item.on_start()
    assert source._fetch_account_position_mode.await_count == 2
    source._set_trading_pair_leverage.assert_awaited_once()
    assert not item._account_ready


@pytest.mark.asyncio
async def test_permanent_http_rejection_is_not_retried(monkeypatch):
    item, _ = make_controller(configuration(connector_name="okx_perpetual"))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(side_effect=OSError("HTTP status is 401. Error: private"))
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    with pytest.raises(OSError):
        await item.on_start()
    source._fetch_account_position_mode.assert_awaited_once()
    assert item._startup_retry is None


@pytest.mark.asyncio
async def test_stop_interrupts_read_retry_and_releases_reference_subscription():
    item, _ = make_controller(configuration(scenario="follow_token", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source.is_trading_required = False
    source._update_trading_rules = AsyncMock(side_effect=TimeoutError())
    retrying = asyncio.Event()

    async def sleep(delay):
        retrying.set()
        await asyncio.Event().wait()

    item._startup_retry_sleep = sleep
    item.market_data_provider.release_order_books = AsyncMock()
    item.start()
    await asyncio.wait_for(retrying.wait(), 1)
    assert item._startup_retry["reason_code"] == "controller_startup_read_retry"
    assert item._control_wait_until is not None
    item.stop()
    await asyncio.wait_for(item.wait_closed(), 1)
    source._update_trading_rules.assert_awaited_once()
    item.market_data_provider.release_order_books.assert_awaited_once()
    assert item.actions_queue.empty()
