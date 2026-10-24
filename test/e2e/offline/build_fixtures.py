"""Reuse existing OKX connector response builders as cross-process JSON fixtures."""
import json
from decimal import Decimal
from pathlib import Path
from test.hummingbot.connector.exchange.okx.test_okx_exchange import OkxExchangeTests
from types import SimpleNamespace

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder


def write_fixtures(path: Path):
    fixture = SimpleNamespace(base_asset="BTC", quote_asset="USDT", exchange_symbol_for_tokens=lambda base, quote: f"{base}-{quote}")
    instruments = OkxExchangeTests.all_symbols_request_mock_response.fget(fixture)
    instruments["data"][0].update(tickSz="0.01", lotSz="0.000001", minSz="0.000001")
    balances = OkxExchangeTests.balance_request_mock_response_for_base_and_quote.fget(fixture)
    for details in balances["data"][0]["details"]:
        details.update(eq="10" if details["ccy"] == "BTC" else "10000",
                       availEq="10" if details["ccy"] == "BTC" else "10000")
    order = InFlightOrder(client_order_id="fixture-id", exchange_order_id="fixture-exchange-id", trading_pair="BTC-USDT",
                          order_type=OrderType.LIMIT, trade_type=TradeType.BUY, amount=Decimal("0.1"),
                          price=Decimal("99"), creation_timestamp=1700000000)
    order_event = OkxExchangeTests.order_event_for_new_order_websocket_update(fixture, order)
    path.write_text(json.dumps({"instruments": instruments, "balances": balances, "order_event": order_event}))
