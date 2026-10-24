import asyncio
from decimal import Decimal
from test.hummingbot.cli.test_admin_api import client
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.connector.trading_rule import TradingRule


@pytest.mark.asyncio
async def test_discovery_uses_connector_rules_and_shares_one_refresh():
    api = AdminAPI(None, AdminSettings(True, "operator", "test-password"))
    source = SimpleNamespace(trading_rules={
        pair: TradingRule(pair, min_order_size=Decimal("0.01"))
        for pair in ("AVAX-USDT", "BTC-USDT", "ETH-BTC")
    }, _update_trading_rules=AsyncMock())
    api.reference_source = lambda: source
    results = await asyncio.gather(*(api.reference_catalogue() for _ in range(20)))
    source._update_trading_rules.assert_awaited_once()
    assert results[0]["pairs"] == ["AVAX-USDT", "BTC-USDT"]
    assert results[0]["observed_at"] is not None and not results[0]["stale"]
    api.reference_observed_at -= 61
    previous_observation = api.reference_observed_at
    source._update_trading_rules.side_effect = OSError("offline")
    stale = await api.reference_catalogue()
    assert stale["stale"] and stale["pairs"] == results[0]["pairs"]
    assert stale["observed_at"] == previous_observation


@pytest.mark.asyncio
async def test_reference_discovery_is_searchable_and_bounded_over_http(monkeypatch):
    monkeypatch.setattr(AdminAPI, "reference_catalogue", AsyncMock(return_value=dict(
        pairs=["AVAX-USDT", "BTC-USDT", "ETH-USDT"], observed_at=1700000000, stale=False)), raising=False)
    async with client() as (http, _):
        response = await http.get("/api/v1/admin/reference-pairs?limit=1")
        assert response.status == 200
        data = await response.json()
        assert data["items"] == ["AVAX-USDT"] and data["next_after"] == "AVAX-USDT"
        assert data["observed_at"] == "2023-11-14T22:13:20Z" and not data["stale"]
        data = await (await http.get("/api/v1/admin/reference-pairs?search=btc")).json()
        assert data["items"] == ["BTC-USDT"]
        assert (await http.get("/api/v1/admin/reference-pairs?limit=501")).status == 422
