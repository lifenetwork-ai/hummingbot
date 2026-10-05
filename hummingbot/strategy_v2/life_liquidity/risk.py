"""Conservative spot risk and reservation state."""

from dataclasses import dataclass
from decimal import Decimal
from threading import RLock


def _finite(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


@dataclass(frozen=True)
class RiskLimits:
    min_inventory_base: Decimal
    max_inventory_base: Decimal
    max_gross_quote: Decimal
    max_net_base: Decimal

    def __post_init__(self):
        if (not _finite(self.min_inventory_base)
                or not _finite(self.max_inventory_base, positive=True)
                or not _finite(self.max_gross_quote, positive=True)
                or not _finite(self.max_net_base, positive=True)
                or self.min_inventory_base > self.max_inventory_base):
            raise ValueError("risk limits must be explicit finite quantities")


@dataclass(frozen=True)
class SpotIntent:
    intent_id: str
    side: str
    quantity_base: Decimal
    limit_price_usdt: Decimal
    session_id: str
    epoch: int

    def __post_init__(self):
        if (not self.intent_id or self.side not in ("BUY", "SELL")
                or not _finite(self.quantity_base, positive=True)
                or not _finite(self.limit_price_usdt, positive=True)
                or not self.session_id or not isinstance(self.epoch, int)
                or isinstance(self.epoch, bool) or self.epoch < 1):
            raise ValueError("spot intent invalid")


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason_code: str
    reservation_id: str | None


@dataclass
class _Reservation:
    intent: SpotIntent
    remaining_base: Decimal
    filled_base: Decimal = Decimal("0")
    state: str = "OPEN"


class ReservationLedger:
    def __init__(self, *, life_balance: Decimal, usdt_balance: Decimal, limits: RiskLimits):
        if not _finite(life_balance) or not _finite(usdt_balance):
            raise ValueError("balances must be finite and nonnegative")
        self.life_balance = life_balance
        self.usdt_balance = usdt_balance
        self.limits = limits
        self._reservations: dict[str, _Reservation] = {}
        self._trades: dict[str, tuple[str, Decimal, Decimal]] = {}
        self._lock = RLock()

    @property
    def reserved_usdt(self) -> Decimal:
        with self._lock:
            return sum((item.remaining_base * item.intent.limit_price_usdt
                        for item in self._reservations.values()
                        if item.intent.side == "BUY"), Decimal("0"))

    @property
    def reserved_life(self) -> Decimal:
        with self._lock:
            return sum((item.remaining_base for item in self._reservations.values()
                        if item.intent.side == "SELL"), Decimal("0"))

    def reserve(self, intent: SpotIntent, *, reference_price: Decimal) -> RiskDecision:
        if not _finite(reference_price, positive=True):
            return RiskDecision(False, "REFERENCE_UNAVAILABLE", None)
        with self._lock:
            if intent.intent_id in self._reservations:
                return RiskDecision(False, "DUPLICATE_INTENT", None)
            buy_base = sum((item.remaining_base for item in self._reservations.values()
                            if item.intent.side == "BUY"), Decimal("0"))
            sell_base = self.reserved_life
            new_buy_base = buy_base + (intent.quantity_base if intent.side == "BUY" else 0)
            new_sell_base = sell_base + (intent.quantity_base if intent.side == "SELL" else 0)
            if intent.side == "BUY" and self.reserved_usdt + intent.quantity_base * intent.limit_price_usdt > self.usdt_balance:
                reason = "INSUFFICIENT_USDT"
            elif intent.side == "SELL" and new_sell_base > self.life_balance:
                reason = "INSUFFICIENT_LIFE"
            elif self.life_balance + new_buy_base > self.limits.max_inventory_base:
                reason = "INVENTORY_MAX"
            elif self.life_balance - new_sell_base < self.limits.min_inventory_base:
                reason = "INVENTORY_MIN"
            elif (self.life_balance * reference_price
                  + sum((item.remaining_base * item.intent.limit_price_usdt
                         for item in self._reservations.values()), Decimal("0"))
                  + intent.quantity_base * intent.limit_price_usdt > self.limits.max_gross_quote):
                reason = "GROSS_EXPOSURE_LIMIT"
            elif max(abs(self.life_balance + new_buy_base),
                     abs(self.life_balance - new_sell_base)) > self.limits.max_net_base:
                reason = "NET_EXPOSURE_LIMIT"
            else:
                self._reservations[intent.intent_id] = _Reservation(intent, intent.quantity_base)
                return RiskDecision(True, "RESERVED", intent.intent_id)
            return RiskDecision(False, reason, None)

    def record_fill(self, intent_id: str, trade_id: str, quantity: Decimal, price: Decimal) -> bool:
        if not trade_id or not _finite(quantity, positive=True) or not _finite(price, positive=True):
            raise ValueError("FILL_INVALID")
        with self._lock:
            trade = (intent_id, quantity, price)
            if trade_id in self._trades:
                if self._trades[trade_id] != trade:
                    raise ValueError("FILL_ID_CONFLICT")
                return False
            item = self._reservations[intent_id]
            if (item.state == "TERMINAL" or quantity > item.remaining_base
                    or item.intent.side == "BUY" and price > item.intent.limit_price_usdt
                    or item.intent.side == "SELL" and price < item.intent.limit_price_usdt):
                raise ValueError("FILL_EXCEEDS_INTENT")
            if item.intent.side == "BUY":
                self.usdt_balance -= quantity * price
                self.life_balance += quantity
            else:
                self.life_balance -= quantity
                self.usdt_balance += quantity * price
            item.filled_base += quantity
            item.remaining_base -= quantity
            self._trades[trade_id] = trade
            return True

    def request_cancel(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                item.state = "CANCEL_PENDING"

    def mark_unknown(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                item.state = "UNKNOWN"

    def requires_reconciliation(self, intent_id: str) -> bool:
        with self._lock:
            return self._reservations[intent_id].state in ("CANCEL_PENDING", "UNKNOWN")

    def confirm_terminal(self, intent_id: str, *, cumulative_filled: Decimal,
                         fills_reconciled: bool, exchange_state: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if exchange_state not in ("CANCELED", "FILLED"):
                raise ValueError("ORDER_NOT_TERMINAL")
            if not fills_reconciled:
                raise ValueError("FILLS_UNRECONCILED")
            if cumulative_filled != item.filled_base:
                raise ValueError("FILL_CUMULATIVE_MISMATCH")
            if exchange_state == "FILLED" and cumulative_filled != item.intent.quantity_base:
                raise ValueError("FILL_CUMULATIVE_MISMATCH")
            item.remaining_base = Decimal("0")
            item.state = "TERMINAL"


class RollingFillLimiter:
    def __init__(self, *, window_ms: int, max_filled_base: Decimal):
        if (not isinstance(window_ms, int) or isinstance(window_ms, bool) or window_ms <= 0
                or not _finite(max_filled_base, positive=True)):
            raise ValueError("rolling fill limit requires explicit positive values")
        self.window_ms = window_ms
        self.max_filled_base = max_filled_base
        self._events: dict[str, tuple[str, Decimal, int]] = {}
        self._lock = RLock()

    def record_fill(self, trade_id: str, side: str, quantity: Decimal, *,
                    observed_monotonic_ms: int) -> bool:
        if (not trade_id or side not in ("BUY", "SELL")
                or not _finite(quantity, positive=True)
                or not isinstance(observed_monotonic_ms, int)
                or isinstance(observed_monotonic_ms, bool) or observed_monotonic_ms < 0):
            raise ValueError("rolling fill observation invalid")
        with self._lock:
            if trade_id in self._events:
                old_side, old_quantity, _ = self._events[trade_id]
                if (old_side, old_quantity) != (side, quantity):
                    raise ValueError("rolling fill trade ID conflict")
                return False
            self._events[trade_id] = (side, quantity, observed_monotonic_ms)
            return True

    def can_replenish(self, side: str, quantity: Decimal, *, current_inventory_base: Decimal,
                      target_inventory_base: Decimal, now_monotonic_ms: int) -> bool:
        if (side not in ("BUY", "SELL") or not _finite(quantity, positive=True)
                or not _finite(current_inventory_base) or not _finite(target_inventory_base)
                or not isinstance(now_monotonic_ms, int) or now_monotonic_ms < 0):
            return False
        risk_increasing = (current_inventory_base + quantity > target_inventory_base
                           if side == "BUY" else
                           current_inventory_base - quantity < target_inventory_base)
        if not risk_increasing:
            return True
        with self._lock:
            recent = sum((amount for event_side, amount, timestamp in self._events.values()
                          if event_side == side and 0 <= now_monotonic_ms - timestamp < self.window_ms),
                         Decimal("0"))
        return recent + quantity <= self.max_filled_base
