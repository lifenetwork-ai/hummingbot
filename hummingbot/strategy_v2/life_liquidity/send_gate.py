"""Final order-send authorization bound to immutable intent and session epochs."""

from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.session import SessionManager


@dataclass(frozen=True)
class SendPermit:
    intent_id: str
    client_order_id: str
    reservation_id: str
    session_id: str
    epoch: int
    config_version: int
    risk_epoch: int
    price_usdt: Decimal
    quantity_base: Decimal

    def __post_init__(self):
        versions = (self.epoch, self.config_version, self.risk_epoch)
        if (not self.intent_id or not self.client_order_id or not self.reservation_id
                or not self.session_id
                or any(not isinstance(value, int) or value < 1 for value in versions)
                or not isinstance(self.price_usdt, Decimal) or not self.price_usdt.is_finite()
                or self.price_usdt <= 0 or not isinstance(self.quantity_base, Decimal)
                or not self.quantity_base.is_finite() or self.quantity_base <= 0):
            raise ValueError("send permit invalid")


@dataclass(frozen=True)
class SendDecision:
    allowed: bool
    reason_code: str


class FinalSendGate:
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def authorize(self, permit: SendPermit, *, config_version: int, risk_epoch: int,
                  reference_ready: bool, all_gates_ready: bool, market_reference_ready: bool,
                  safety_state: str, economics_allowed: bool, reservation_active: bool,
                  price_usdt: Decimal, quantity_base: Decimal) -> SendDecision:
        current = self.session_manager.current_session
        if (current is None or permit.session_id != current.session_id
                or permit.epoch != current.epoch
                or not self.session_manager.can_quote(
                    reference_ready=reference_ready, all_gates_ready=all_gates_ready,
                    market_reference_ready=market_reference_ready)):
            return SendDecision(False, "SESSION_PERMISSION_REVOKED")
        if config_version != permit.config_version or current.config_version != permit.config_version:
            return SendDecision(False, "CONFIG_VERSION_CHANGED")
        if risk_epoch != permit.risk_epoch:
            return SendDecision(False, "RISK_EPOCH_CHANGED")
        if price_usdt != permit.price_usdt or quantity_base != permit.quantity_base:
            return SendDecision(False, "ORDER_CHANGED")
        if not reservation_active:
            return SendDecision(False, "RESERVATION_UNAVAILABLE")
        if safety_state not in ("NORMAL", "DEGRADED"):
            return SendDecision(False, "SAFETY_BLOCKED")
        if not economics_allowed:
            return SendDecision(False, "ECONOMICS_BLOCKED")
        return SendDecision(True, "SEND_PERMITTED")
