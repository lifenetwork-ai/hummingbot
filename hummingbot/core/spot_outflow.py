"""Bounded rolling projection of committed recorder fills and pending BUY risk."""
import heapq
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict

from pydantic import BaseModel, ConfigDict, Field

ZERO = Decimal("0")
MAX_FILLS = 250000
MAX_RESERVATIONS = 256
MAX_ORDER_FILLS = 10000


class SpotOutflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    window_seconds: int = Field(gt=0, le=604800, strict=True)
    max_net_outflow_quote: Decimal = Field(gt=0, allow_inf_nan=False)
    fee_allowance_rate: Decimal = Field(ge=0, le=1, allow_inf_nan=False)


class SpotOutflowPolicy(SpotOutflowConfig):
    trading_pair: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._]*-USDT$")


@dataclass
class Reservation:
    amount: Decimal
    price: Decimal
    flat_fee: Decimal
    committed: Decimal = ZERO
    seen: set = field(default_factory=set)
    terminal_amount: Decimal = None


class SpotOutflowProjection:
    def __init__(self, config, pair, *, coverage_start_ms):
        self.config, self.pair = config, pair
        self.coverage_start_ms = coverage_start_ms
        self.now_ms = 0
        self.entries, self.future, self.expiry = {}, [], []
        self._latest_expiry_ms = 0
        self.reservations: Dict[str, Reservation] = {}
        self.net = ZERO
        self.future_risk = ZERO
        self.future_costs = {}
        self.fees = {}
        self.failure = None
        self.invalid = set()
        self.clock_failure = False

    def advance(self, now_ms):
        self.clock_failure = not isinstance(now_ms, int) or now_ms < self.now_ms
        if self.clock_failure:
            return
        self.now_ms = now_ms
        start = now_ms - self.config.window_seconds * 1000
        while self.future and self.future[0][0] < now_ms:
            stamp, key = heapq.heappop(self.future)
            self.future_risk -= self.future_costs.pop(key, ZERO)
            value = self.entries.get(key)
            if value is None:
                continue
            reservation = self.reservations.get(key[1])
            if reservation is not None and key not in reservation.seen:
                self._commit_quantity(reservation, key, Decimal(value[1]))
            if stamp >= start:
                self._apply(value, 1)
                heapq.heappush(self.expiry, (stamp, key))
                self._latest_expiry_ms = max(self._latest_expiry_ms, stamp)
            else:
                self.invalid.discard(key)
                self.entries.pop(key, None)
        if len(self.expiry) >= 1024 and self._latest_expiry_ms < start:
            # All entries in this heap are due. Sorting once preserves exactly
            # the heappop order (including Decimal arithmetic) without repeated
            # heap repair during a large same-time expiry. Future risk is separate.
            self.expiry.sort()
            expired, self.expiry = self.expiry, []
            for _, key in expired:
                value = self.entries.pop(key, None)
                if value is not None:
                    self._apply(value, -1)
                    self.invalid.discard(key)
        else:
            while self.expiry and self.expiry[0][0] < start:
                _, key = heapq.heappop(self.expiry)
                value = self.entries.pop(key, None)
                if value is not None:
                    self._apply(value, -1)
                    self.invalid.discard(key)
        if not self.expiry:
            self._latest_expiry_ms = 0
        for key, reservation in list(self.reservations.items()):
            if reservation.terminal_amount is not None and reservation.committed >= reservation.terminal_amount:
                del self.reservations[key]

    def _apply(self, value, direction):
        _, _, cash, fees, _ = value
        if cash is not None:
            self.net += Decimal(cash) * direction
            for token, amount in fees:
                self.fees[token] = self.fees.get(token, ZERO) + Decimal(amount) * direction

    def _commit_quantity(self, reservation, key, amount):
        if len(reservation.seen) >= MAX_ORDER_FILLS:
            self.failure = "spot_outflow_order_fill_limit"
            return
        reservation.seen.add(key)
        reservation.committed += amount

    def add_fill(self, row):
        if row["market"] != "okx" or row["symbol"] != self.pair:
            return
        key = (row["market"], row["order_id"], row["exchange_trade_id"])
        stamp, amount, price = int(row["timestamp"]), Decimal(row["amount"]), Decimal(row["price"])
        if (stamp < 0 or not amount.is_finite() or amount <= 0 or not price.is_finite() or price <= 0
                or row["trade_type"] not in ("BUY", "SELL")):
            self.failure = "spot_outflow_fill_invalid"
            return
        cash, fees = None, {}
        try:
            original = row["trade_fee"]
            # OKX actual fees are flat amounts. A percentage is an estimate,
            # not an identified original-currency payment (same as reconciliation).
            if Decimal(str(original["percent"])) != 0:
                raise ValueError("Actual fee amount is unavailable")
            for fee in original["flat_fees"]:
                token, cost = fee["token"], Decimal(str(fee["amount"]))
                if not token or not cost.is_finite():
                    raise ValueError("Invalid original fee")
                fees[token] = fees.get(token, ZERO) + cost
            cash = price * amount * (1 if row["trade_type"] == "BUY" else -1) + fees.pop("USDT", ZERO)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            pass
        if key in self.entries:
            old_stamp, old_amount, old_cash, old_fees, (old_price, old_side, old_payload) = self.entries[key]
            # Compare decoded values, preserving Decimal numeric equality (even
            # with different quantum) and normal JSON object/list equality.
            if (old_stamp != stamp or Decimal(old_amount) != amount
                    or (None if old_cash is None else Decimal(old_cash)) != cash
                    or {token: Decimal(cost) for token, cost in old_fees} != fees
                    or Decimal(old_price) != price or old_side != row["trade_type"]
                    or json.loads(old_payload) != row["trade_fee"]):
                self.failure = "spot_outflow_fill_conflict"
            return
        reservation = self.reservations.get(row["order_id"])
        if reservation is not None and key in reservation.seen:
            return
        if stamp < self.now_ms:
            if reservation is not None:
                self._commit_quantity(reservation, key, amount)
            if stamp < self.now_ms - self.config.window_seconds * 1000:
                return
        if len(self.entries) >= MAX_FILLS:
            self.failure = "spot_outflow_projection_limit"
            return
        # Exact text avoids retaining a large graph of GC-tracked Decimals and
        # mutable fee JSON for the entire rolling window. Decode only when a
        # fill is applied, expires or is replayed; arithmetic remains Decimal.
        # Snapshot the original JSON rather than retaining the caller's object.
        value = (stamp, str(amount), None if cash is None else str(cash),
                 tuple((token, str(cost)) for token, cost in fees.items()),
                 (str(price), row["trade_type"], json.dumps(row["trade_fee"], separators=(",", ":"))))
        self.entries[key] = value
        if cash is None:
            self.invalid.add(key)
        if stamp < self.now_ms:
            self._apply(value, 1)
            heapq.heappush(self.expiry, (stamp, key))
            self._latest_expiry_ms = max(self._latest_expiry_ms, stamp)
        else:
            covered = (amount * reservation.price * (1 + self.config.fee_allowance_rate) + reservation.flat_fee
                       if reservation is not None else ZERO)
            self.future_costs[key] = max(ZERO, (cash or ZERO) - covered)
            self.future_risk += self.future_costs[key]
            heapq.heappush(self.future, (stamp, key))
        self.advance(self.now_ms)

    def reason(self):
        if self.failure:
            return self.failure
        if self.clock_failure:
            return "spot_outflow_clock_unavailable"
        if self.invalid:
            return "spot_outflow_fee_unavailable"
        if self.now_ms - self.coverage_start_ms < self.config.window_seconds * 1000:
            return "spot_outflow_legacy_window_uncovered"
        return None

    def reserved(self):
        return self.future_risk + sum((max(ZERO, item.amount - item.committed) * item.price * (1 + self.config.fee_allowance_rate)
                                       + item.flat_fee for item in self.reservations.values()), ZERO)

    def reserve(self, order, flat_fee=ZERO):
        if self.reason():
            return False
        amount, price, flat_fee = Decimal(order.amount), Decimal(order.price), Decimal(flat_fee)
        if any(not value.is_finite() or value <= 0 for value in (amount, price)) or not flat_fee.is_finite() or flat_fee < 0:
            return False
        existing = self.reservations.get(order.client_order_id)
        if existing is not None:
            return ((existing.amount, existing.price, existing.flat_fee) == (amount, price, flat_fee)
                    and max(ZERO, self.net) + self.reserved() <= self.config.max_net_outflow_quote)
        cost = amount * price * (1 + self.config.fee_allowance_rate) + flat_fee
        if len(self.reservations) >= MAX_RESERVATIONS or max(ZERO, self.net) + self.reserved() + cost > self.config.max_net_outflow_quote:
            return False
        self.reservations[order.client_order_id] = Reservation(amount, price, flat_fee)
        return True

    def confirm_terminal(self, order_id, committed_amount):
        item = self.reservations.get(order_id)
        if item is not None:
            if not committed_amount.is_finite() or not ZERO <= committed_amount <= item.amount:
                raise ValueError("Terminal quantity is invalid")
            item.terminal_amount = committed_amount
            self.advance(self.now_ms)

    def release_unsent(self, order_id):
        self.reservations.pop(order_id, None)

    def status(self):
        reason = self.reason()
        reserved = self.reserved()
        return dict(ready=reason is None, reason_code=reason,
                    recorded_net_outflow_quote=None if reason else self.net,
                    reserved_quote=reserved,
                    remaining_quote=None if reason else max(ZERO, self.config.max_net_outflow_quote - max(ZERO, self.net) - reserved),
                    non_usdt_fees={token: value for token, value in self.fees.items() if value},
                    window_start_ms=max(0, self.now_ms - self.config.window_seconds * 1000), window_end_ms=self.now_ms,
                    coverage_start_ms=self.coverage_start_ms, fill_count=len(self.entries))
