import json
from test.e2e.offline.exchange import Exchange
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["canceled", "filled"])
async def test_fill_candidate_canceled_after_ledger_read_cannot_reopen_terminal_order(state):
    fixtures = dict(order_event={"arg": {"channel": "orders"}, "data": [{}]}, balances={"data": [{"details": []}]})
    with patch("test.e2e.offline.exchange.Path.read_text", return_value=json.dumps(fixtures)):
        exchange = Exchange()
    order = dict(instId="LIFE-USDT", clOrdId="one", ordId="1", state=state,
                 accFillSz="0", sz="1", side="buy", px="0.1")
    exchange.orders["one"] = order
    response = await exchange.control({"fill": {"clOrdId": "one", "amount": "0.001", "fee": "0.000001"}})
    assert response.status == 409
    assert order["state"] == state and order["accFillSz"] == "0"
    assert exchange.fills == []


@pytest.mark.asyncio
async def test_perpetual_fill_snapshot_is_not_changed_by_a_later_cancel_during_publication():
    fixtures = dict(order_event={"arg": {"channel": "orders"}, "data": [{}]}, balances={"data": [{"details": []}]})
    with patch("test.e2e.offline.exchange.Path.read_text", return_value=json.dumps(fixtures)):
        exchange = Exchange()
    order = dict(instId="LIFE-USDT-SWAP", clOrdId="one", ordId="1", state="live", posSide="long",
                 accFillSz="0", sz="1", side="buy", px="0.1")
    exchange.orders["one"] = order
    events = []

    async def publish(message):
        events.append(message)
        if message["arg"]["channel"] == "positions":
            order["state"] = "canceled"
    exchange.broadcast = publish
    response = await exchange.control({"fill": {"clOrdId": "one", "amount": "0.1", "fee": "0.000001"}})
    assert response.status == 200
    fill_event = next(event for event in events if event["arg"]["channel"] == "orders")
    assert fill_event["data"][0]["state"] == "partially_filled"
    assert order["state"] == "canceled"
