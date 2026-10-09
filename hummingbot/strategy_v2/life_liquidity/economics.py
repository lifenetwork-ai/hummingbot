"""Per-order conservative economics in USDT and basis points."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
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


@dataclass(frozen=True)
class SubsidyBudgetStatus:
    session_committed_quote: Decimal
    day_committed_quote: Decimal
    campaign_committed_quote: Decimal
    available_quote: Decimal


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
        self._uncertain = False
        self._must_exist = self.path.exists() or self.path.is_symlink()
        self._entries: dict[str, dict] = {}
        if self._must_exist:
            if self.path.is_symlink() or not self.path.is_file():
                raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") == 1:
                raise ValueError("SUBSIDY_BUDGET_MIGRATION_REQUIRED")
            if data.get("schema_version") != 2 or data.get("campaign_id") != campaign_id:
                raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
            if data.get("policy") != self._policy():
                raise ValueError("SUBSIDY_BUDGET_POLICY_MISMATCH")
            self._entries = self._validated_entries(data.get("entries"))

    def _policy(self) -> dict[str, str]:
        return {"campaign_limit_quote": str(self.campaign_limit_quote),
                "day_limit_quote": str(self.day_limit_quote),
                "session_limit_quote": str(self.session_limit_quote)}

    @staticmethod
    def _validated_entries(entries: object) -> dict[str, dict]:
        if not isinstance(entries, dict):
            raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
        cycles = {}
        for intent_id, entry in entries.items():
            if (not isinstance(intent_id, str) or not intent_id
                    or not isinstance(entry, dict)
                    or not {"day", "session_id", "reserved", "actual"} <= set(entry)
                    or set(entry) - {"day", "session_id", "reserved", "actual", "fill_floor", "inventory_cycle"}
                    or not isinstance(entry["day"], str) or not entry["day"]
                    or not isinstance(entry["session_id"], str) or not entry["session_id"]):
                raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
            for field in ("reserved", "actual", "fill_floor"):
                value = entry.get(field)
                if value is None and field == "actual":
                    continue
                if value is None and field == "fill_floor" and field not in entry:
                    continue
                try:
                    parsed = Decimal(value) if isinstance(value, str) else None
                except Exception as exc:
                    raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE") from exc
                if parsed is None or not _valid(parsed):
                    raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
            if (entry["actual"] is not None
                    and Decimal(entry["actual"]) < Decimal(entry.get("fill_floor", "0"))):
                raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
            cycle = entry.get("inventory_cycle")
            if "inventory_cycle" in entry:
                if (not isinstance(cycle, dict) or set(cycle) != {"id", "members", "proof", "actuals"}
                        or not isinstance(cycle["id"], str) or not cycle["id"]
                        or not isinstance(cycle["members"], list)
                        or any(not isinstance(member, str) or not member for member in cycle["members"])
                        or cycle["members"] != sorted(set(cycle["members"]))
                        or intent_id not in cycle["members"]
                        or not isinstance(cycle["proof"], str) or len(cycle["proof"]) != 64
                        or any(char not in "0123456789abcdef" for char in cycle["proof"])
                        or not isinstance(cycle["actuals"], dict)
                        or set(cycle["actuals"]) != set(cycle["members"])):
                    raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
                if cycle["id"] in cycles and cycles[cycle["id"]] != cycle:
                    raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
                cycles[cycle["id"]] = cycle
                for member in cycle["members"]:
                    other = entries.get(member, {})
                    if (other.get("inventory_cycle") != cycle or other.get("actual") is None
                            or other["actual"] != cycle["actuals"][member]):
                        raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
        return entries

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _assert_disk_matches(self, *, required: bool) -> None:
        if self._uncertain or self.path.is_symlink() or not self.path.is_file():
            if (not self._uncertain and not required and not self._must_exist
                    and not self.path.exists() and not self._entries):
                return
            raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (data.get("schema_version") != 2 or data.get("campaign_id") != self.campaign_id
                or data.get("policy") != self._policy()
                or self._validated_entries(data.get("entries")) != self._entries):
            raise ValueError("SUBSIDY_JOURNAL_UNAVAILABLE")

    def initialize_empty(self) -> None:
        """Create a verified zero-use journal before any service quote is approved."""
        with self._lock, self._file_lock():
            if self._must_exist or self.path.exists() or self.path.is_symlink() or self._entries:
                raise ValueError("SUBSIDY_INITIALIZATION_UNSAFE")
            self._save({})
            self._must_exist = True

    @staticmethod
    def _day(at_utc: datetime) -> str:
        if (not isinstance(at_utc, datetime) or at_utc.tzinfo is None
                or at_utc.utcoffset() != timedelta(0)):
            raise ValueError("UTC timestamp required")
        return at_utc.date().isoformat()

    @staticmethod
    def _committed(entry: dict) -> Decimal:
        if entry["actual"] is not None:
            return Decimal(entry["actual"])
        return max(Decimal(entry["reserved"]), Decimal(entry.get("fill_floor", "0")))

    @property
    def campaign_committed_quote(self) -> Decimal:
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            return sum((self._committed(entry) for entry in self._entries.values()), Decimal("0"))

    def _save(self, entries: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 2, "campaign_id": self.campaign_id,
                           "policy": self._policy(), "entries": entries},
                          handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            if replace_attempted:
                self._uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reserve(self, intent_id: str, expected_cost_quote: Decimal, *,
                session_id: str, at_utc: datetime) -> bool:
        if (not isinstance(intent_id, str) or not intent_id
                or not isinstance(session_id, str) or not session_id
                or not _valid(expected_cost_quote)):
            raise ValueError("subsidy reservation invalid")
        day = self._day(at_utc)
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            event = {"day": day, "session_id": session_id,
                     "reserved": str(expected_cost_quote), "actual": None}
            previous = self._entries.get(intent_id)
            if previous is not None:
                if previous != event:
                    raise ValueError("SUBSIDY_RESERVATION_CONFLICT")
                return False
            status = self._status(session_id=session_id, day=day)
            if expected_cost_quote > status.available_quote:
                return False
            updated = dict(self._entries)
            updated[intent_id] = event
            self._save(updated)
            self._entries = updated
            self._must_exist = True
            return True

    def reconcile(self, intent_id: str, *, actual_cost_quote: Decimal) -> bool:
        return self._reconcile(intent_id, actual_cost_quote=actual_cost_quote, release_proven=False)

    def _reconcile(self, intent_id: str, *, actual_cost_quote: Decimal,
                   release_proven: bool) -> bool:
        if not _valid(actual_cost_quote):
            raise ValueError("actual subsidy cost invalid")
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries[intent_id]
            if actual_cost_quote < Decimal(entry.get("fill_floor", "0")):
                raise ValueError("SUBSIDY_RECONCILIATION_BELOW_FILLS")
            if actual_cost_quote < Decimal(entry["reserved"]) and release_proven is not True:
                raise ValueError("SUBSIDY_RELEASE_UNPROVEN")
            if entry["actual"] is not None:
                if Decimal(entry["actual"]) != actual_cost_quote:
                    raise ValueError("subsidy reconciliation conflict")
                return False
            updated = dict(self._entries)
            updated[intent_id] = {**entry, "actual": str(actual_cost_quote)}
            self._save(updated)
            self._entries = updated
            return True

    def record_fill_floor(self, intent_id: str, cumulative_cost_quote: Decimal) -> bool:
        """Conservatively charge attributed fills while the quote hold remains open."""
        if not _valid(cumulative_cost_quote):
            raise ValueError("SUBSIDY_FILL_COST_INVALID")
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries[intent_id]
            if entry["actual"] is not None:
                if cumulative_cost_quote == Decimal(entry.get("fill_floor", "0")):
                    return False
                raise ValueError("SUBSIDY_ALREADY_SETTLED")
            previous = Decimal(entry.get("fill_floor", "0"))
            if cumulative_cost_quote < previous:
                raise ValueError("SUBSIDY_FILL_COST_REGRESSION")
            if cumulative_cost_quote == previous:
                return False
            updated = dict(self._entries)
            updated[intent_id] = {**entry, "fill_floor": str(cumulative_cost_quote)}
            self._save(updated)
            self._entries = updated
            return True

    def matches_fill_floor(self, intent_id: str, cumulative_cost_quote: Decimal) -> bool:
        if not _valid(cumulative_cost_quote):
            return False
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries.get(intent_id)
            return (entry is not None
                    and Decimal(entry.get("fill_floor", "0")) == cumulative_cost_quote)

    def matches_intent_session(self, intent_id: str, session_id: str) -> bool:
        """Attribution cannot move a fill or released hold into another session."""
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries.get(intent_id)
            return entry is not None and entry["session_id"] == session_id

    def verified_inventory_cycles(self) -> tuple[dict, ...]:
        """Return detached durable proofs for independent attribution verification."""
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            cycles = {entry["inventory_cycle"]["id"]: entry["inventory_cycle"]
                      for entry in self._entries.values() if "inventory_cycle" in entry}
            return tuple(json.loads(json.dumps(cycles[key])) for key in sorted(cycles))

    def _settle_inventory_cycle(self, cycle_id: str, *, proof: str,
                                actuals: dict[str, Decimal]) -> bool:
        """Commit all members together; the attributor verifies the fill proof."""
        if (not isinstance(cycle_id, str) or not cycle_id or not actuals
                or any(not isinstance(key, str) or not key or not _valid(value)
                       for key, value in actuals.items())):
            raise ValueError("SUBSIDY_CYCLE_INVALID")
        cycle = {"id": cycle_id, "members": sorted(actuals), "proof": proof,
                 "actuals": {key: str(actuals[key]) for key in sorted(actuals)}}
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            prior = [entry.get("inventory_cycle") for entry in self._entries.values()
                     if entry.get("inventory_cycle", {}).get("id") == cycle_id]
            if prior:
                if any(record != cycle for record in prior):
                    raise ValueError("SUBSIDY_CYCLE_CONFLICT")
                return False
            updated = dict(self._entries)
            for intent_id, actual in actuals.items():
                entry = self._entries[intent_id]
                if entry["actual"] is not None:
                    raise ValueError("SUBSIDY_CYCLE_ALREADY_SETTLED")
                if actual < Decimal(entry.get("fill_floor", "0")):
                    raise ValueError("SUBSIDY_RECONCILIATION_BELOW_FILLS")
                updated[intent_id] = {**entry, "actual": str(actual), "inventory_cycle": cycle}
            self._validated_entries(updated)
            self._save(updated)
            self._entries = updated
            return True

    def verified_fill_floors(self) -> dict[str, Decimal]:
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            return {intent_id: Decimal(entry["fill_floor"])
                    for intent_id, entry in self._entries.items()
                    if Decimal(entry.get("fill_floor", "0")) > 0}

    def _status(self, *, session_id: str, day: str) -> SubsidyBudgetStatus:
        campaign = sum((self._committed(entry) for entry in self._entries.values()), Decimal("0"))
        daily = sum((self._committed(entry) for entry in self._entries.values()
                     if entry["day"] == day), Decimal("0"))
        session = sum((self._committed(entry) for entry in self._entries.values()
                       if entry["session_id"] == session_id), Decimal("0"))
        available = max(Decimal("0"), min(
            self.campaign_limit_quote - campaign,
            self.day_limit_quote - daily,
            self.session_limit_quote - session))
        return SubsidyBudgetStatus(session, daily, campaign, available)

    def verified_status(self, *, session_id: str, at_utc: datetime) -> SubsidyBudgetStatus:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session ID required")
        day = self._day(at_utc)
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            return self._status(session_id=session_id, day=day)

    def matches_reservation(self, intent_id: str, expected_cost_quote: Decimal, *,
                            session_id: str, at_utc: datetime) -> bool:
        day = self._day(at_utc)
        expected = {"day": day, "session_id": session_id,
                    "reserved": str(expected_cost_quote), "actual": None}
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            return self._entries.get(intent_id) == expected

    def reserved_for(self, intent_id: str, *, session_id: str) -> Decimal | None:
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries.get(intent_id)
            if entry is None or entry["session_id"] != session_id or entry["actual"] is not None:
                return None
            return Decimal(entry["reserved"])

    def release_unsent(self, intent_id: str, *, wal, reservations) -> bool:
        """Credit back only after both order and capital journals prove no send."""
        from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
        from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

        record = IntentWAL(wal.path).get(intent_id)
        if record.state != "ABORTED_BEFORE_SEND":
            raise ValueError("SUBSIDY_RELEASE_UNPROVEN")
        durable = ReservationLedger.restore(reservations.path, limits=reservations.limits)
        if (record.reservation_id != intent_id
                or not durable.is_terminal_intent(intent_id)
                or not durable.can_abort_unsent(
                    intent_id, record.session_id, record.epoch)):
            raise ValueError("SUBSIDY_RELEASE_UNPROVEN")
        return self._reconcile(intent_id, actual_cost_quote=Decimal("0"), release_proven=True)

    def settle_zero_fill_terminal(self, intent_id: str, *, wal, reservations) -> bool:
        """Release an unused hold after durable, zero-fill terminal proof.

        A partial fill retains its hold until complete inventory/exit accounting;
        merely ending the quote does not settle the economic subsidy.
        """
        from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
        from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

        record = IntentWAL(wal.path).get(intent_id)
        durable = ReservationLedger.restore(reservations.path, limits=reservations.limits)
        reservation = durable.reservation_snapshot().get(intent_id)
        if (record.state != "TERMINAL" or not record.exchange_terminal_observed
                or not record.exchange_order_id or record.reservation_id != intent_id
                or reservation is None or reservation.state != "TERMINAL"
                or reservation.filled_base != 0 or reservation.remaining_base != 0
                or reservation.intent.session_id != record.session_id
                or reservation.intent.epoch != record.epoch):
            raise ValueError("SUBSIDY_RELEASE_UNPROVEN")
        with self._lock, self._file_lock():
            self._assert_disk_matches(required=True)
            entry = self._entries.get(intent_id)
            if (entry is None or entry["session_id"] != record.session_id
                    or Decimal(entry.get("fill_floor", "0")) != 0):
                raise ValueError("SUBSIDY_RELEASE_UNPROVEN")
        return self._reconcile(intent_id, actual_cost_quote=Decimal("0"), release_proven=True)


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
