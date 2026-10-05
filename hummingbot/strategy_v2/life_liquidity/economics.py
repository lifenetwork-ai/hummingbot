"""Per-order conservative economics in USDT and basis points."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from threading import RLock


def _valid(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


@dataclass(frozen=True)
class EconomicInputs:
    side: str
    price_usdt: Decimal
    quantity_base: Decimal
    value_usdt: Decimal
    tick_size: Decimal
    lot_size: Decimal
    min_size_base: Decimal
    maker_fee_rate: Decimal
    exit_fee_rate: Decimal
    impact_cost_quote: Decimal
    carry_cost_quote: Decimal
    inventory_risk_quote: Decimal
    uncertainty_bps: Decimal
    exit_value_includes_impact: bool


@dataclass(frozen=True)
class EconomicPolicy:
    objective: str
    min_net_edge_bps: Decimal

    def __post_init__(self):
        if self.objective not in ("profit_mm", "liquidity_service") or not _valid(self.min_net_edge_bps):
            raise ValueError("economic policy invalid")


@dataclass(frozen=True)
class EconomicDecision:
    allowed: bool
    reason_code: str
    final_price_usdt: Decimal
    final_quantity_base: Decimal
    gross_edge_quote: Decimal | None
    net_edge_quote: Decimal | None
    net_edge_bps: Decimal | None
    subsidy_reserved_quote: Decimal


def evaluate_quote(inputs: EconomicInputs, policy: EconomicPolicy,
                   *, subsidy_remaining_quote: Decimal | None = None) -> EconomicDecision:
    if (inputs.side not in ("BUY", "SELL")
            or not all(_valid(value, positive=True) for value in (
                inputs.price_usdt, inputs.quantity_base, inputs.value_usdt,
                inputs.tick_size, inputs.lot_size, inputs.min_size_base))
            or not all(isinstance(value, Decimal) and value.is_finite() for value in (
                inputs.maker_fee_rate, inputs.exit_fee_rate))
            or not all(_valid(value) for value in (
                inputs.impact_cost_quote, inputs.carry_cost_quote,
                inputs.inventory_risk_quote, inputs.uncertainty_bps))):
        return EconomicDecision(False, "ECONOMIC_INPUT_INVALID", Decimal("0"), Decimal("0"),
                                None, None, None, Decimal("0"))
    price_round = ROUND_FLOOR if inputs.side == "BUY" else ROUND_CEILING
    price = (inputs.price_usdt / inputs.tick_size).to_integral_value(rounding=price_round) * inputs.tick_size
    quantity = (inputs.quantity_base / inputs.lot_size).to_integral_value(rounding=ROUND_FLOOR) * inputs.lot_size

    def unavailable(reason: str) -> EconomicDecision:
        return EconomicDecision(False, reason, price, quantity, None, None, None, Decimal("0"))

    if quantity < inputs.min_size_base or price <= 0:
        return unavailable("ORDER_BELOW_MINIMUM")
    if inputs.exit_value_includes_impact and inputs.impact_cost_quote > 0:
        return unavailable("IMPACT_DOUBLE_COUNTED")
    side_sign = Decimal("1") if inputs.side == "BUY" else Decimal("-1")
    gross = side_sign * (inputs.value_usdt - price) * quantity
    reference_notional = inputs.value_usdt * quantity
    # Potential maker rebates are not credited before an actual fill.
    maker_cost = max(Decimal("0"), inputs.maker_fee_rate) * price * quantity
    exit_cost = max(Decimal("0"), inputs.exit_fee_rate) * reference_notional
    uncertainty_cost = inputs.uncertainty_bps / Decimal("10000") * reference_notional
    net = (gross - maker_cost - exit_cost - inputs.impact_cost_quote
           - inputs.carry_cost_quote - inputs.inventory_risk_quote - uncertainty_cost)
    net_bps = net / reference_notional * Decimal("10000")
    if policy.objective == "profit_mm":
        allowed = net_bps >= policy.min_net_edge_bps
        return EconomicDecision(allowed, "NET_EDGE_READY" if allowed else "NET_EDGE_BELOW_MINIMUM",
                                price, quantity, gross, net, net_bps, Decimal("0"))
    if not _valid(subsidy_remaining_quote):
        return EconomicDecision(False, "SUBSIDY_BUDGET_UNAVAILABLE", price, quantity,
                                gross, net, net_bps, Decimal("0"))
    subsidy = max(Decimal("0"), -net)
    allowed = subsidy <= subsidy_remaining_quote
    return EconomicDecision(allowed, "SERVICE_BUDGET_READY" if allowed else "SUBSIDY_BUDGET_EXCEEDED",
                            price, quantity, gross, net, net_bps,
                            subsidy if allowed else Decimal("0"))


class SubsidyBudgetLedger:
    def __init__(self, path: Path, *, campaign_id: str, campaign_limit_quote: Decimal,
                 day_limit_quote: Decimal, session_limit_quote: Decimal):
        if (not campaign_id or not _valid(campaign_limit_quote, positive=True)
                or not _valid(day_limit_quote, positive=True)
                or not _valid(session_limit_quote, positive=True)
                or session_limit_quote > day_limit_quote or day_limit_quote > campaign_limit_quote):
            raise ValueError("subsidy limits must be explicit and nested")
        self.path = Path(path)
        self.campaign_id = campaign_id
        self.campaign_limit_quote = campaign_limit_quote
        self.day_limit_quote = day_limit_quote
        self.session_limit_quote = session_limit_quote
        self._lock = RLock()
        self._entries: dict[str, dict[str, str | None]] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") != 1 or data.get("campaign_id") != campaign_id:
                raise ValueError("subsidy journal campaign mismatch")
            self._entries = data["entries"]

    @staticmethod
    def _committed(entry: dict[str, str | None]) -> Decimal:
        return Decimal(entry["actual"] if entry["actual"] is not None else entry["reserved"])

    @property
    def campaign_committed_quote(self) -> Decimal:
        with self._lock:
            return sum((self._committed(entry) for entry in self._entries.values()), Decimal("0"))

    def _save(self, entries: dict[str, dict[str, str | None]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "campaign_id": self.campaign_id,
                           "entries": entries}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reserve(self, intent_id: str, expected_cost_quote: Decimal, *,
                session_id: str, at_utc: datetime) -> bool:
        if (not intent_id or not session_id or not _valid(expected_cost_quote)
                or not isinstance(at_utc, datetime) or at_utc.tzinfo is None
                or at_utc.utcoffset() != timedelta(0)):
            raise ValueError("subsidy reservation invalid")
        day = at_utc.date().isoformat()
        with self._lock:
            if intent_id in self._entries:
                return False
            if self.campaign_committed_quote + expected_cost_quote > self.campaign_limit_quote:
                return False
            day_used = sum((self._committed(entry) for entry in self._entries.values()
                            if entry["day"] == day), Decimal("0"))
            session_used = sum((self._committed(entry) for entry in self._entries.values()
                                if entry["session_id"] == session_id), Decimal("0"))
            if (day_used + expected_cost_quote > self.day_limit_quote
                    or session_used + expected_cost_quote > self.session_limit_quote):
                return False
            updated = dict(self._entries)
            updated[intent_id] = {"day": day, "session_id": session_id,
                                  "reserved": str(expected_cost_quote), "actual": None}
            self._save(updated)
            self._entries = updated
            return True

    def reconcile(self, intent_id: str, *, actual_cost_quote: Decimal) -> bool:
        if not _valid(actual_cost_quote):
            raise ValueError("actual subsidy cost invalid")
        with self._lock:
            entry = self._entries[intent_id]
            if entry["actual"] is not None:
                if Decimal(entry["actual"]) != actual_cost_quote:
                    raise ValueError("subsidy reconciliation conflict")
                return False
            updated = dict(self._entries)
            updated[intent_id] = {**entry, "actual": str(actual_cost_quote)}
            self._save(updated)
            self._entries = updated
            return True


@dataclass(frozen=True)
class ExitInputs:
    position_base: Decimal
    side: str
    quantity_base: Decimal
    limit_price_usdt: Decimal
    independent_value_usdt: Decimal
    max_slippage_bps: Decimal
    remaining_exit_loss_quote: Decimal


@dataclass(frozen=True)
class ExitDecision:
    allowed: bool
    reason_code: str
    expected_loss_quote: Decimal | None


def evaluate_exit(inputs: ExitInputs) -> ExitDecision:
    if (not isinstance(inputs.position_base, Decimal) or not inputs.position_base.is_finite()
            or not _valid(inputs.quantity_base, positive=True)
            or not _valid(inputs.limit_price_usdt, positive=True)
            or not _valid(inputs.independent_value_usdt, positive=True)
            or not _valid(inputs.max_slippage_bps)
            or not _valid(inputs.remaining_exit_loss_quote)):
        return ExitDecision(False, "EXIT_INPUT_INVALID", None)
    if (inputs.position_base > 0 and inputs.side != "SELL"
            or inputs.position_base < 0 and inputs.side != "BUY"
            or inputs.position_base == 0 or inputs.quantity_base > abs(inputs.position_base)):
        return ExitDecision(False, "EXIT_WOULD_REVERSE_POSITION", None)
    adverse_per_base = (inputs.independent_value_usdt - inputs.limit_price_usdt
                        if inputs.side == "SELL" else
                        inputs.limit_price_usdt - inputs.independent_value_usdt)
    loss = max(Decimal("0"), adverse_per_base * inputs.quantity_base)
    slippage_bps = max(Decimal("0"), adverse_per_base) / inputs.independent_value_usdt * Decimal("10000")
    if slippage_bps > inputs.max_slippage_bps:
        return ExitDecision(False, "EXIT_SLIPPAGE_EXCEEDED", loss)
    if loss > inputs.remaining_exit_loss_quote:
        return ExitDecision(False, "EXIT_LOSS_BUDGET_EXCEEDED", loss)
    return ExitDecision(True, "EXIT_WITHIN_LIMITS", loss)
