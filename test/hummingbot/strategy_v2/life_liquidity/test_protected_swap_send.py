"""O.7 SWAP sends must carry their last check through the real REST throttler."""

import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant

D = Decimal


def connector_with_transport(response=None):
    sent = []
    throttler = PausedThrottler()

    class Response:
        status = 200
        content_type = "application/json"

        async def json(self):
            return response if response is not None else {"data": [{"sCode": "0", "ordId": "swap-42"}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()

    connector = OkxPerpetualDerivative(trading_pairs=[], trading_required=False)
    connector._contract_sizes = {"LIFE-USDT": D("0.25")}
    connector._perpetual_trading._position_mode = PositionMode.ONEWAY
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT-SWAP")
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    return connector, throttler, sent


@pytest.mark.asyncio
async def test_swap_permission_revoked_after_throttler_sends_zero_requests():
    connector, throttle, sent = connector_with_transport()
    connector.enable_protected_trading_pair("LIFE-USDT")
    permission = {"allowed": True}
    ack = []

    def check(wire):
        assert wire["sz"] == "2"
        assert wire["reduceOnly"] is True
        if not permission["allowed"]:
            raise PermissionError("SWAP_SEND_REVOKED")

    pending = asyncio.create_task(connector._place_order(
        "wire-1", "LIFE-USDT", D("0.5"), TradeType.SELL,
        OrderType.LIMIT_MAKER, D("1"), PositionAction.CLOSE,
        pre_send_check=check, on_ack=ack.append))
    await asyncio.wait_for(throttle.entered.wait(), 2)
    permission["allowed"] = False
    throttle.release.set()
    with pytest.raises(PermissionError, match="SWAP_SEND_REVOKED"):
        await pending
    assert sent == ack == []


@pytest.mark.asyncio
async def test_swap_ack_runs_only_after_a_successful_protected_wire_request():
    connector, throttle, sent = connector_with_transport()
    connector.enable_protected_trading_pair("LIFE-USDT")
    checked, ack = [], []
    pending = asyncio.create_task(connector._place_order(
        "wire-1", "LIFE-USDT", D("0.5"), TradeType.SELL,
        OrderType.LIMIT_MAKER, D("1"), PositionAction.CLOSE,
        pre_send_check=checked.append, on_ack=ack.append))
    await asyncio.wait_for(throttle.entered.wait(), 2)
    assert sent == ack == []
    throttle.release.set()
    assert (await pending)[0] == "swap-42"
    assert len(checked) == len(sent) == 1
    assert ack == ["swap-42"]
    assert checked[0]["ordType"] == "post_only"
    assert checked[0]["posSide"] == "net"


@pytest.mark.asyncio
@pytest.mark.parametrize("exchange_id", [None, "", 0])
async def test_malformed_success_response_cannot_create_a_false_ack(exchange_id):
    connector, throttle, sent = connector_with_transport({"data": [{"sCode": "0", "ordId": exchange_id}]})
    connector.enable_protected_trading_pair("LIFE-USDT")
    ack = []
    throttle.release.set()
    with pytest.raises(IOError, match="ORDER_ACK_UNAVAILABLE"):
        await connector._place_order(
            "wire", "LIFE-USDT", D("0.5"), TradeType.SELL, OrderType.LIMIT_MAKER,
            D("1"), PositionAction.CLOSE, pre_send_check=lambda _: None, on_ack=ack.append)
    assert len(sent) == 1 and ack == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"pre_send_check": lambda _: None}, {"on_ack": lambda _: None}])
async def test_armed_swap_pair_cannot_bypass_protected_callbacks(kwargs):
    connector, _, sent = connector_with_transport()
    connector.enable_protected_trading_pair("LIFE-USDT")
    with pytest.raises(PermissionError, match="PROTECTED_ORDER_REQUIRED"):
        await connector._place_order("wire", "LIFE-USDT", D("0.5"), TradeType.SELL,
                                     OrderType.LIMIT_MAKER, D("1"), PositionAction.OPEN, **kwargs)
    assert sent == []


@pytest.mark.parametrize("order_type", [OrderType.MARKET, OrderType.LIMIT])
@pytest.mark.asyncio
async def test_swap_protected_scheduler_requires_post_only_and_persisted_identity(order_type):
    connector, _, _ = connector_with_transport()
    connector.enable_protected_trading_pair("LIFE-USDT")
    with pytest.raises(ValueError):
        connector.submit_protected_order(order_id="wire", trading_pair="LIFE-USDT", amount=D("0.5"),
                                         price=D("1"), trade_type=TradeType.SELL, order_type=order_type,
                                         position_action=PositionAction.OPEN,
                                         pre_send_check=lambda _: None, on_ack=lambda _: None)
