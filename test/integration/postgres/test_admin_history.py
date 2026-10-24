from decimal import Decimal

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event
from sqlalchemy.orm import Session

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill


@pytest.fixture
def history(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    with Session(pg_engine) as session, session.begin():
        for index in range(12):
            market = "okx" if index % 2 == 0 else "okx_perpetual"
            order = Order(id=f"order-{index:02d}", config_file_path="life_mm_profiles.yaml", strategy="v2_with_controllers",
                          market=market, symbol="LIFE-USDT", base_asset="LIFE", quote_asset="USDT",
                          creation_timestamp=1700000000000, order_type="LIMIT_MAKER", amount=Decimal("100"),
                          price=Decimal("0.1"), leverage=1, last_status="OrderFilled", last_update_timestamp=1700000000100)
            session.add(order)
            session.add(OrderStatus(order=order, timestamp=1700000000000, status="BuyOrderCreated"))
            session.add(TradeFill(order=order, config_file_path=order.config_file_path, strategy=order.strategy,
                                  market=market, symbol=order.symbol, base_asset="LIFE", quote_asset="USDT",
                                  timestamp=1700000000000 + index, trade_type="BUY", order_type="LIMIT_MAKER",
                                  price=Decimal("0.1"), amount=Decimal("1.5"), leverage=1,
                                  trade_fee={"percent": "0", "flat_fees": [{"token": "USDT", "amount": "0.01"}]},
                                  trade_fee_in_quote=Decimal("0.01"), exchange_trade_id=f"fill-{index}"))
    return AdminStore(store)


def test_market_scopes_and_keyset_pages_keep_identical_pairs_separate(history):
    first = history.orders(market="spot", limit=2)
    assert len(first["items"]) == 2
    assert all(row["market"] == "spot" and row["side"] == "BUY" for row in first["items"])
    second = history.orders(market="spot", limit=2, cursor=first["next_cursor"])
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    perpetual = history.fills(market="perpetual", limit=100)
    assert len(perpetual["items"]) == 6
    assert all(row["market"] == "perpetual" for row in perpetual["items"])
    with pytest.raises(ValueError):
        history.orders(market="perpetual", limit=2, cursor=first["next_cursor"])


def test_order_detail_and_half_open_fill_window(history):
    detail = history.order("order-00", timeline_limit=1)
    assert detail["order"]["id"] == "order-00"
    assert detail["timeline"][0]["status"] == "BuyOrderCreated"
    assert "order_id=order-00" in detail["fills_url"]
    assert history.order("missing") is None
    fills = history.fills(start_ms=1700000000000, end_ms=1700000000001)
    assert len(fills["items"]) == 1
    assert fills["items"][0]["fee_in_quote"] == Decimal("0.01")


def test_history_query_count_does_not_grow_per_order(history):
    counts = []

    def count(*args):
        counts.append(1)

    event.listen(history.engine, "before_cursor_execute", count)
    try:
        history.orders(limit=1)
        small = len(counts)
        counts.clear()
        history.orders(limit=100)
        assert len(counts) == small
        assert small <= 3
    finally:
        event.remove(history.engine, "before_cursor_execute", count)


@pytest.mark.asyncio
async def test_real_http_history_uses_pg_scopes_and_decimal_strings(history):
    settings = AdminSettings(True, "operator", "test-password")
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(None, settings, store=history).register(app)
    async with TestClient(TestServer(app), headers={"Authorization": BasicAuth("operator", "test-password").encode()}) as client:
        response = await client.get(ADMIN_PREFIX + "/orders?market=spot&limit=2")
        assert response.status == 200
        page = await response.json()
        assert len(page["items"]) == 2
        assert page["items"][0]["price"] == "0.1"
        assert page["items"][0]["market"] == "spot"
        response = await client.get(ADMIN_PREFIX + "/fills?market=perpetual")
        assert response.status == 200
        assert (await response.json())["items"][0]["fee_in_quote"] == "0.01"
        assert (await client.get(ADMIN_PREFIX + "/orders?limit=501")).status == 422
        assert (await client.get(ADMIN_PREFIX + "/orders?market=spot&market=perpetual")).status == 422
