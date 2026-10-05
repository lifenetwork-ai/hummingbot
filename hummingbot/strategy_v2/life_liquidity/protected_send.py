"""Opt-in LIFE spot send bridge with persisted OKX wire identity."""

from decimal import Decimal, InvalidOperation
from typing import Callable

from hummingbot.connector.utils import get_new_client_order_id
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


class ProtectedSpotGateway:
    def __init__(self, connector, wal: IntentWAL,
                 authorize: Callable[[SendPermit], bool]):
        self.connector = connector
        self.wal = wal
        self.authorize = authorize
        self._allocated_ids: dict[str, tuple[str, str]] = {}

    def arm_pair(self, trading_pair: str) -> None:
        self.connector.enable_protected_trading_pair(trading_pair)

    def allocate_client_order_id(self, *, side: str, trading_pair: str) -> str:
        if side not in ("BUY", "SELL"):
            raise ValueError("ORDER_SIDE_INVALID")
        wire_id = get_new_client_order_id(
            is_buy=side == "BUY", trading_pair=trading_pair,
            hbot_order_id_prefix=self.connector.client_order_id_prefix,
            max_id_len=self.connector.client_order_id_max_length)
        self._allocated_ids[wire_id] = (side, trading_pair)
        return wire_id

    def submit(self, permit: SendPermit, *, side: str, trading_pair: str,
               order_type: OrderType):
        if (side not in ("BUY", "SELL") or order_type != OrderType.LIMIT_MAKER
                or self._allocated_ids.get(permit.client_order_id) != (side, trading_pair)):
            raise ValueError("PROTECTED_ORDER_INVALID")

        def permitted() -> bool:
            result = self.authorize(permit)
            return result if isinstance(result, bool) else getattr(result, "allowed", False)

        def check_permission(wire_data: dict) -> None:
            # This function is re-evaluated inside RESTConnection after the
            # throttler, not captured as an earlier controller decision.
            expected = {"clOrdId": permit.client_order_id, "instId": trading_pair,
                        "side": side.lower(), "ordType": "post_only", "tdMode": "cash"}
            try:
                wire_price = Decimal(wire_data["px"])
                wire_amount = Decimal(wire_data["sz"])
            except (KeyError, InvalidOperation, TypeError, ValueError):
                raise PermissionError("ORDER_CHANGED")
            if (any(wire_data.get(key) != value for key, value in expected.items())
                    or set(wire_data) != set(expected) | {"px", "sz"}
                    or wire_price != permit.price_usdt
                    or wire_amount != permit.quantity_base):
                raise PermissionError("ORDER_CHANGED")
            if not permitted():
                raise PermissionError("SEND_PERMISSION_REVOKED")

        if not permitted():
            raise PermissionError("SEND_PERMISSION_REVOKED")
        self.wal.prepare(permit.intent_id, client_order_id=permit.client_order_id,
                         session_id=permit.session_id, epoch=permit.epoch,
                         reservation_id=permit.reservation_id)
        return self.connector.submit_protected_order(
            order_id=permit.client_order_id, trading_pair=trading_pair,
            amount=permit.quantity_base,
            trade_type=TradeType.BUY if side == "BUY" else TradeType.SELL,
            order_type=order_type, price=permit.price_usdt,
            pre_send_check=check_permission,
            on_ack=lambda exchange_id: self.wal.acknowledge(permit.intent_id, exchange_id))
