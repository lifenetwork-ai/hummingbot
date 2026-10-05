"""P4.13–P4.14 contracts at the actual OKX REST send boundary."""

import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, begin, manager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.send_gate import FinalSendGate, SendPermit
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


class PausedThrottler:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def execute_task(self, limit_id):
        throttler = self

        class Slot:
            async def __aenter__(self):
                throttler.entered.set()
                await throttler.release.wait()

            async def __aexit__(self, *_):
                pass

        return Slot()


@pytest.mark.asyncio
async def test_revocation_while_okx_request_waits_on_throttler_sends_zero_requests():
    sent = []
    valid = True
    throttler = PausedThrottler()

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            raise AssertionError("revoked order reached network")

    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._web_assistants_factory = SimpleNamespace(
        get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")

    def check(_wire_data):
        if not valid:
            raise PermissionError("SESSION_PERMISSION_REVOKED")

    pending = asyncio.create_task(connector._place_order(
        order_id="client-1", trading_pair="LIFE-USDT", amount=Decimal("1"),
        trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
        price=Decimal("1"), pre_send_check=check))
    await asyncio.wait_for(throttler.entered.wait(), timeout=2)
    valid = False
    throttler.release.set()
    with pytest.raises(PermissionError, match="SESSION_PERMISSION_REVOKED"):
        await pending
    assert sent == []


@pytest.mark.asyncio
async def test_okx_ack_updates_wal_with_same_wire_id(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.prepare("intent-1", client_order_id="wire-1", session_id="s1",
                epoch=1, reservation_id="r1")
    sent = []

    class Response:
        status = 200
        content_type = "application/json"

        async def json(self):
            return {"data": [{"sCode": "0", "ordId": "exchange-1"}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()

    throttler = PausedThrottler()
    throttler.release.set()
    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._web_assistants_factory = SimpleNamespace(
        get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    order_id, _ = await connector._place_order(
        order_id="wire-1", trading_pair="LIFE-USDT", amount=Decimal("1"),
        trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
        price=Decimal("1"), pre_send_check=lambda _wire_data: None,
        on_ack=lambda exchange_id: wal.acknowledge("intent-1", exchange_id))
    assert order_id == "exchange-1"
    assert len(sent) == 1
    assert '"clOrdId": "wire-1"' in sent[0]["data"]
    assert IntentWAL(path).get("intent-1").exchange_order_id == "exchange-1"
    assert IntentWAL(path).scoped_order_ids("s1", 1) == ("wire-1",)


@pytest.mark.asyncio
async def test_okx_lost_ack_keeps_wire_id_unresolved(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.prepare("intent-1", client_order_id="wire-1", session_id="s1",
                epoch=1, reservation_id="r1")

    class Session:
        async def request(self, **kwargs):
            raise TimeoutError("ACK lost after possible send")

    throttler = PausedThrottler()
    throttler.release.set()
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    with pytest.raises(TimeoutError):
        await connector._place_order(
            order_id="wire-1", trading_pair="LIFE-USDT", amount=Decimal("1"),
            trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
            price=Decimal("1"), pre_send_check=lambda _wire_data: None,
            on_ack=lambda exchange_id: wal.acknowledge("intent-1", exchange_id))
    assert IntentWAL(path).pending_reconciliation("s1", 1) == ("wire-1",)


@pytest.mark.asyncio
async def test_armed_life_pair_rejects_unprotected_connector_send():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair("LIFE-USDT")
    with pytest.raises(PermissionError, match="PROTECTED_ORDER_REQUIRED"):
        await connector._place_order(
            order_id="unprotected", trading_pair="LIFE-USDT", amount=Decimal("1"),
            trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
            price=Decimal("1"))


@pytest.mark.asyncio
async def test_protected_submit_preserves_preallocated_wire_id():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair("LIFE-USDT")
    connector._create_order = AsyncMock()
    assert connector.submit_protected_order(
        order_id="wire-1", trading_pair="LIFE-USDT", amount=Decimal("1"),
        trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
        price=Decimal("1"), pre_send_check=lambda _wire_data: None,
        on_ack=lambda _: None) == "wire-1"
    await asyncio.sleep(0)
    assert connector._create_order.await_args.kwargs["order_id"] == "wire-1"


@pytest.mark.asyncio
async def test_gateway_to_okx_create_order_uses_wal_id_and_post_only(tmp_path):
    sent = []

    class Response:
        status = 200
        content_type = "application/json"

        async def json(self):
            return {"data": [{"sCode": "0", "ordId": "exchange-1"}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()

    throttler = PausedThrottler()
    throttler.release.set()
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("1"),
        min_price_increment=Decimal("0.01"),
        min_base_amount_increment=Decimal("1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    wal = IntentWAL(tmp_path / "intents.json")
    gateway = ProtectedSpotGateway(connector, wal, authorize=lambda permit: True)
    gateway.arm_pair("LIFE-USDT")
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("intent-1", wire_id, "r1", "s1", 1, 1, 1,
                        Decimal("1"), Decimal("1"))
    assert gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                          order_type=OrderType.LIMIT_MAKER) == wire_id
    for _ in range(50):
        if wal.get("intent-1").state == "ACKED":
            break
        await asyncio.sleep(0.01)
    assert wal.get("intent-1").state == "ACKED", (
        sent, connector._on_order_failure.call_args.kwargs.get("exception"))
    assert len(sent) == 1
    assert f'"clOrdId": "{wire_id}"' in sent[0]["data"]
    assert '"ordType": "post_only"' in sent[0]["data"]


def test_gateway_persists_allocator_id_before_connector_queue_and_keeps_unknown_ack(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    observed = []

    class Connector:
        client_order_id_prefix = "HBOT"
        client_order_id_max_length = 32

        def submit_protected_order(self, **kwargs):
            persisted = IntentWAL(path).get("intent-1")
            observed.append((persisted.client_order_id, persisted.reservation_id,
                             persisted.session_id, persisted.epoch, persisted.state))
            assert persisted.client_order_id == kwargs["order_id"]
            return kwargs["order_id"]

    connector = Connector()
    gateway = ProtectedSpotGateway(connector, wal, authorize=lambda permit: True)
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("intent-1", wire_id, "reserve-1", "session-1", 1, 1, 1,
                        Decimal("1"), Decimal("1"))
    assert gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                          order_type=OrderType.LIMIT_MAKER) == wire_id
    assert observed == [(wire_id, "reserve-1", "session-1", 1, "SEND_UNKNOWN")]
    assert IntentWAL(path).pending_reconciliation("session-1", 1) == (wire_id,)
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                       order_type=OrderType.LIMIT_MAKER)


def test_gateway_rechecks_permission_at_final_send(tmp_path):
    state = {"allowed": True}
    connector = SimpleNamespace(client_order_id_prefix="HBOT", client_order_id_max_length=32)
    connector.submit_protected_order = lambda **kwargs: kwargs["pre_send_check"]
    gateway = ProtectedSpotGateway(connector, IntentWAL(tmp_path / "intents.json"),
                                   authorize=lambda permit: state["allowed"])
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("i", wire_id, "r", "s", 1, 1, 1, Decimal("1"), Decimal("1"))
    check = gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                           order_type=OrderType.LIMIT_MAKER)
    state["allowed"] = False
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check({"clOrdId": wire_id, "instId": "LIFE-USDT", "side": "buy",
               "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "1"})


def test_gateway_rejects_connector_quantization_or_payload_mutation(tmp_path):
    connector = SimpleNamespace(client_order_id_prefix="HBOT", client_order_id_max_length=32)
    connector.submit_protected_order = lambda **kwargs: kwargs["pre_send_check"]
    gateway = ProtectedSpotGateway(connector, IntentWAL(tmp_path / "intents.json"),
                                   authorize=lambda permit: True)
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("i", wire_id, "r", "s", 1, 1, 1, Decimal("1"), Decimal("1"))
    check = gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                           order_type=OrderType.LIMIT_MAKER)
    with pytest.raises(PermissionError, match="ORDER_CHANGED"):
        check({"clOrdId": wire_id, "instId": "LIFE-USDT", "side": "buy",
               "ordType": "post_only", "tdMode": "cash", "px": "0.99", "sz": "1"})


def test_allocated_id_cannot_be_reused_for_another_side_or_pair(tmp_path):
    connector = SimpleNamespace(client_order_id_prefix="HBOT", client_order_id_max_length=32)
    connector.submit_protected_order = lambda **kwargs: kwargs["order_id"]
    gateway = ProtectedSpotGateway(connector, IntentWAL(tmp_path / "intents.json"),
                                   authorize=lambda permit: True)
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("i", wire_id, "r", "s", 1, 1, 1, Decimal("1"), Decimal("1"))
    with pytest.raises(ValueError, match="PROTECTED_ORDER_INVALID"):
        gateway.submit(permit, side="SELL", trading_pair="LIFE-USDT",
                       order_type=OrderType.LIMIT_MAKER)
    with pytest.raises(ValueError, match="PROTECTED_ORDER_INVALID"):
        gateway.submit(permit, side="BUY", trading_pair="BTC-USDT",
                       order_type=OrderType.LIMIT_MAKER)


def test_wal_failure_never_enqueues_order(tmp_path):
    queued = []
    connector = SimpleNamespace(client_order_id_prefix="HBOT", client_order_id_max_length=32)
    connector.submit_protected_order = lambda **kwargs: queued.append(kwargs)
    wal = IntentWAL(tmp_path / "intents.json")
    wal._save = lambda records: (_ for _ in ()).throw(OSError("disk unavailable"))
    gateway = ProtectedSpotGateway(connector, wal, authorize=lambda permit: True)
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    permit = SendPermit("i", wire_id, "r", "s", 1, 1, 1, Decimal("1"), Decimal("1"))
    with pytest.raises(OSError, match="disk unavailable"):
        gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                       order_type=OrderType.LIMIT_MAKER)
    assert queued == []


def test_gateway_checks_real_session_epoch_after_queue_delay(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    final_gate = FinalSendGate(active)
    connector = SimpleNamespace(client_order_id_prefix="HBOT", client_order_id_max_length=32)
    connector.submit_protected_order = lambda **kwargs: kwargs["pre_send_check"]

    def authorize(permit):
        return final_gate.authorize(
            permit, config_version=1, risk_epoch=1, reference_ready=True,
            all_gates_ready=True, market_reference_ready=False,
            safety_state="NORMAL", economics_allowed=True, reservation_active=True,
            price_usdt=permit.price_usdt, quantity_base=permit.quantity_base)

    gateway = ProtectedSpotGateway(connector, IntentWAL(tmp_path / "intents.json"), authorize)
    wire_id = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
    current = active.current_session
    permit = SendPermit("i", wire_id, "r", current.session_id, current.epoch, 1, 1,
                        Decimal("1"), Decimal("1"))
    check = gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                           order_type=OrderType.LIMIT_MAKER)
    clock.advance(10)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check({"clOrdId": wire_id, "instId": "LIFE-USDT", "side": "buy",
               "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "1"})
