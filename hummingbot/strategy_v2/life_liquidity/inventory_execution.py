"""Conservative offline child planning for a separate LIFE inventory session.

The output is advisory. A caller must persist the child intent, reserve the
account balance, and pass the existing protected final-send and reconciliation
gates before any exchange request. Missing evidence produces no child order.
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from threading import RLock


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _positive(value: Decimal) -> bool:
    return _nonnegative(value) and value > 0


def _utc(value: datetime) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == timedelta(0))


@dataclass(frozen=True)
class InventoryExecutionPolicy:
    side: str
    target_base: Decimal
    benchmark_price_usdt: Decimal
    deadline_utc: datetime
    max_child_base: Decimal
    max_total_notional_quote: Decimal
    max_slippage_bps: Decimal
    max_impact_quote: Decimal
    max_exit_loss_quote: Decimal
    participation_fraction: Decimal
    min_independent_volume_base: Decimal
    tick_size: Decimal
    lot_size: Decimal
    min_size_base: Decimal

    def __post_init__(self):
        if (self.side not in ("BUY", "SELL") or not _utc(self.deadline_utc)
                or not all(_positive(value) for value in (
                    self.target_base, self.benchmark_price_usdt, self.max_child_base,
                    self.max_total_notional_quote, self.participation_fraction,
                    self.min_independent_volume_base, self.tick_size,
                    self.lot_size, self.min_size_base))
                or self.participation_fraction > 1
                or not all(_nonnegative(value) for value in (
                    self.max_slippage_bps, self.max_impact_quote,
                    self.max_exit_loss_quote))):
            raise ValueError("INVENTORY_EXECUTION_POLICY_INVALID")


@dataclass(frozen=True)
class InventoryExecutionSnapshot:
    observed_utc: datetime
    best_bid_usdt: Decimal
    best_ask_usdt: Decimal
    independent_volume_base: Decimal
    known_own_volume_base: Decimal
    independent_depth_base: Decimal
    expected_impact_quote: Decimal
    life_balance: Decimal
    usdt_balance: Decimal
    filled_base: Decimal
    pending_base: Decimal
    filled_notional_quote: Decimal
    pending_notional_quote: Decimal
    exit_loss_used_quote: Decimal
    mm_orders_reconciled: bool
    account_scope_ready: bool


@dataclass(frozen=True)
class InventoryChildDecision:
    allowed: bool
    reason_code: str
    order_semantics: str | None
    price_usdt: Decimal
    quantity_base: Decimal
    remaining_base: Decimal
    estimated_exit_loss_quote: Decimal


def plan_inventory_child(policy: InventoryExecutionPolicy,
                         snapshot: InventoryExecutionSnapshot) -> InventoryChildDecision:
    if not isinstance(policy, InventoryExecutionPolicy):
        raise ValueError("INVENTORY_EXECUTION_POLICY_INVALID")
    remaining = Decimal("0")
    if isinstance(snapshot, InventoryExecutionSnapshot):
        try:
            remaining = max(Decimal("0"), policy.target_base
                            - snapshot.filled_base - snapshot.pending_base)
        except (TypeError, ValueError):
            pass

    def blocked(reason: str) -> InventoryChildDecision:
        return InventoryChildDecision(False, reason, None, Decimal("0"),
                                      Decimal("0"), remaining, Decimal("0"))

    if (not isinstance(snapshot, InventoryExecutionSnapshot)
            or not _utc(snapshot.observed_utc)
            or not all(_positive(value) for value in (
                snapshot.best_bid_usdt, snapshot.best_ask_usdt))
            or snapshot.best_bid_usdt >= snapshot.best_ask_usdt
            or not all(_nonnegative(value) for value in (
                snapshot.independent_volume_base, snapshot.known_own_volume_base,
                snapshot.independent_depth_base, snapshot.expected_impact_quote,
                snapshot.life_balance, snapshot.usdt_balance, snapshot.filled_base,
                snapshot.pending_base, snapshot.filled_notional_quote,
                snapshot.pending_notional_quote, snapshot.exit_loss_used_quote))
            or snapshot.known_own_volume_base > snapshot.independent_volume_base
            or not isinstance(snapshot.mm_orders_reconciled, bool)
            or not isinstance(snapshot.account_scope_ready, bool)):
        return blocked("INVENTORY_SNAPSHOT_INVALID")
    if snapshot.observed_utc >= policy.deadline_utc:
        return blocked("INVENTORY_DEADLINE_EXPIRED")
    if not snapshot.account_scope_ready:
        return blocked("ACCOUNT_SCOPE_UNAVAILABLE")
    if not snapshot.mm_orders_reconciled:
        return blocked("MM_RECONCILIATION_REQUIRED")
    if remaining <= 0:
        return blocked("INVENTORY_TARGET_FILLED_OR_PENDING")
    if snapshot.independent_volume_base < policy.min_independent_volume_base:
        return blocked("INDEPENDENT_VOLUME_INSUFFICIENT")
    if snapshot.independent_depth_base <= 0:
        return blocked("INDEPENDENT_DEPTH_UNAVAILABLE")
    if snapshot.expected_impact_quote > policy.max_impact_quote:
        return blocked("IMPACT_LIMIT_BREACHED")
    if snapshot.exit_loss_used_quote >= policy.max_exit_loss_quote:
        return blocked("EXIT_BUDGET_EXHAUSTED")

    # A maker child rests at the existing near side. It cannot sweep to meet
    # the deadline, and the fixed pre-session benchmark cannot be rebased.
    raw_price = (snapshot.best_bid_usdt if policy.side == "BUY"
                 else snapshot.best_ask_usdt)
    price_round = ROUND_FLOOR if policy.side == "BUY" else ROUND_CEILING
    price = (raw_price / policy.tick_size).to_integral_value(rounding=price_round) * policy.tick_size
    deviation_bps = ((price / policy.benchmark_price_usdt - 1) * Decimal("10000")
                     if policy.side == "BUY" else
                     (1 - price / policy.benchmark_price_usdt) * Decimal("10000"))
    if deviation_bps > policy.max_slippage_bps:
        return blocked("PRICE_LIMIT_BREACHED")

    participation_room = ((snapshot.independent_volume_base
                           - snapshot.known_own_volume_base)
                          * policy.participation_fraction
                          - snapshot.filled_base - snapshot.pending_base)
    if participation_room <= 0:
        return blocked("PARTICIPATION_CAP_REACHED")
    notional_room = (policy.max_total_notional_quote
                     - snapshot.filled_notional_quote - snapshot.pending_notional_quote)
    if notional_room <= 0:
        return blocked("INVENTORY_NOTIONAL_CAP_REACHED")
    balance_room = (snapshot.life_balance if policy.side == "SELL"
                    else snapshot.usdt_balance / price)
    if balance_room <= 0:
        return blocked("INVENTORY_BALANCE_UNAVAILABLE")
    quantity = min(remaining, policy.max_child_base, participation_room,
                   snapshot.independent_depth_base, notional_room / price, balance_room)
    quantity = (quantity / policy.lot_size).to_integral_value(rounding=ROUND_FLOOR) * policy.lot_size
    if quantity < policy.min_size_base:
        return blocked("INVENTORY_CHILD_BELOW_MINIMUM")
    adverse_price_loss = max(Decimal("0"), (
        (price - policy.benchmark_price_usdt) if policy.side == "BUY"
        else (policy.benchmark_price_usdt - price)) * quantity)
    estimated_loss = adverse_price_loss + snapshot.expected_impact_quote
    if snapshot.exit_loss_used_quote + estimated_loss > policy.max_exit_loss_quote:
        return blocked("EXIT_BUDGET_EXHAUSTED")
    return InventoryChildDecision(True, "INVENTORY_CHILD_READY", "LIMIT_MAKER",
                                  price, quantity, remaining, estimated_loss)


@dataclass(frozen=True)
class InventoryExecutionReport:
    state: str
    filled_base: Decimal
    pending_base: Decimal
    remaining_base: Decimal
    implementation_shortfall_quote: Decimal
    fee_quote: Decimal


class InventoryExecutionLedger:
    """Local journal for one inventory target; exchange status remains external."""

    def __init__(self, path: Path, *, session_id: str,
                 policy: InventoryExecutionPolicy, create: bool):
        if (not isinstance(path, (str, Path)) or not isinstance(session_id, str)
                or not session_id or not isinstance(policy, InventoryExecutionPolicy)
                or not isinstance(create, bool)):
            raise ValueError("INVENTORY_JOURNAL_POLICY_INVALID")
        self.path = Path(path)
        self.session_id = session_id
        self.policy = policy
        self._lock = RLock()
        self._entries: dict[str, dict] = {}
        self._last_observed_utc: str | None = None
        self._uncertain = False
        if create:
            with self._file_lock():
                if self.path.exists() or self.path.is_symlink():
                    raise ValueError("INVENTORY_JOURNAL_EXISTS")
                self._save(self._entries, self._last_observed_utc)
        else:
            data = self._read()
            self._entries = data["entries"]
            self._last_observed_utc = data["last_observed_utc"]

    def _policy(self) -> dict[str, str]:
        return {key: value.isoformat() if isinstance(value, datetime) else str(value)
                for key, value in asdict(self.policy).items()}

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_name(f"{self.path.name}.lock"),
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self) -> dict:
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (data.get("schema_version") != 2
                or data.get("session_id") != self.session_id
                or data.get("policy") != self._policy()
                or not isinstance(data.get("entries"), dict)
                or data.get("last_observed_utc") is not None
                and not isinstance(data.get("last_observed_utc"), str)):
            raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
        if data["last_observed_utc"] is not None:
            try:
                if not _utc(datetime.fromisoformat(data["last_observed_utc"])):
                    raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
            except (TypeError, ValueError) as exc:
                raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE") from exc
        seen_trades = set()
        for intent_id, item in data["entries"].items():
            if (not isinstance(intent_id, str) or not intent_id
                    or not isinstance(item, dict)
                    or set(item) != {"quantity", "price", "estimated_exit_loss", "trades"}
                    or not isinstance(item["trades"], dict)):
                raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
            try:
                quantity, price = Decimal(item["quantity"]), Decimal(item["price"])
                estimated_exit_loss = Decimal(item["estimated_exit_loss"])
                if (not _positive(quantity) or not _positive(price)
                        or not _nonnegative(estimated_exit_loss)):
                    raise ValueError
                filled = Decimal("0")
                for trade_id, trade in item["trades"].items():
                    if (not isinstance(trade_id, str) or not trade_id
                            or trade_id in seen_trades
                            or not isinstance(trade, dict)
                            or set(trade) != {"quantity", "price", "fee_quote"}):
                        raise ValueError
                    seen_trades.add(trade_id)
                    fill_qty, fill_price = Decimal(trade["quantity"]), Decimal(trade["price"])
                    fee_quote = Decimal(trade["fee_quote"])
                    if (not _positive(fill_qty) or not _positive(fill_price)
                            or not _nonnegative(fee_quote)
                            or self.policy.side == "SELL" and fill_price < price
                            or self.policy.side == "BUY" and fill_price > price):
                        raise ValueError
                    filled += fill_qty
                if filled > quantity:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE") from exc
        if sum((Decimal(item["quantity"]) for item in data["entries"].values()),
               Decimal("0")) > self.policy.target_base:
            raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
        return data

    def _verified(self) -> None:
        if self._uncertain:
            raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")
        data = self._read()
        if (data["entries"] != self._entries
                or data["last_observed_utc"] != self._last_observed_utc):
            raise ValueError("INVENTORY_JOURNAL_UNAVAILABLE")

    def _save(self, entries: dict, last_observed_utc: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 2, "session_id": self.session_id,
                           "policy": self._policy(), "entries": entries,
                           "last_observed_utc": last_observed_utc}, handle, sort_keys=True)
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

    def _totals(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        filled = pending = filled_notional = pending_notional = Decimal("0")
        for item in self._entries.values():
            quantity = Decimal(item["quantity"])
            item_filled = sum((Decimal(trade["quantity"])
                               for trade in item["trades"].values()), Decimal("0"))
            filled += item_filled
            pending += quantity - item_filled
            filled_notional += sum((Decimal(trade["quantity"]) * Decimal(trade["price"])
                                    for trade in item["trades"].values()), Decimal("0"))
            pending_notional += (quantity - item_filled) * Decimal(item["price"])
        return filled, pending, filled_notional, pending_notional

    def _exit_costs(self) -> tuple[Decimal, Decimal, Decimal]:
        """Signed realized shortfall, explicit fees, and pending loss holds."""
        shortfall = fee_total = pending_hold = Decimal("0")
        for item in self._entries.values():
            quantity = Decimal(item["quantity"])
            item_filled = Decimal("0")
            for trade in item["trades"].values():
                fill_qty = Decimal(trade["quantity"])
                fill_price = Decimal(trade["price"])
                fee = Decimal(trade["fee_quote"])
                side_sign = Decimal("1") if self.policy.side == "BUY" else Decimal("-1")
                shortfall += (fill_price - self.policy.benchmark_price_usdt) * fill_qty * side_sign
                fee_total += fee
                item_filled += fill_qty
            pending_hold += (Decimal(item["estimated_exit_loss"])
                             * (quantity - item_filled) / quantity)
        return shortfall, fee_total, pending_hold

    @property
    def filled_base(self) -> Decimal:
        with self._lock, self._file_lock():
            self._verified()
            return self._totals()[0]

    @property
    def pending_base(self) -> Decimal:
        with self._lock, self._file_lock():
            self._verified()
            return self._totals()[1]

    def reserve_child(self, intent_id: str,
                      snapshot: InventoryExecutionSnapshot) -> InventoryChildDecision:
        with self._lock, self._file_lock():
            self._verified()
            if not isinstance(intent_id, str) or not intent_id or intent_id in self._entries:
                return InventoryChildDecision(False, "INVENTORY_INTENT_INVALID", None,
                                              Decimal("0"), Decimal("0"),
                                              self.policy.target_base - self._totals()[0], Decimal("0"))
            if not isinstance(snapshot, InventoryExecutionSnapshot) or not _utc(snapshot.observed_utc):
                raise ValueError("INVENTORY_SNAPSHOT_INVALID")
            if (self._last_observed_utc is not None
                    and snapshot.observed_utc < datetime.fromisoformat(self._last_observed_utc)):
                return InventoryChildDecision(False, "INVENTORY_CLOCK_ROLLBACK", None,
                                              Decimal("0"), Decimal("0"),
                                              self.policy.target_base - self._totals()[0], Decimal("0"))
            filled, pending, filled_notional, pending_notional = self._totals()
            shortfall, fee, pending_hold = self._exit_costs()
            adjusted = replace(
                snapshot, filled_base=filled, pending_base=pending,
                filled_notional_quote=filled_notional,
                pending_notional_quote=pending_notional,
                exit_loss_used_quote=max(snapshot.exit_loss_used_quote,
                                         max(Decimal("0"), shortfall + fee) + pending_hold),
                life_balance=max(Decimal("0"), snapshot.life_balance
                                 - (pending if self.policy.side == "SELL" else Decimal("0"))),
                usdt_balance=max(Decimal("0"), snapshot.usdt_balance
                                 - (pending_notional if self.policy.side == "BUY" else Decimal("0"))))
            decision = plan_inventory_child(self.policy, adjusted)
            next_observed = snapshot.observed_utc.isoformat()
            updated = dict(self._entries)
            if decision.allowed:
                updated[intent_id] = {"quantity": str(decision.quantity_base),
                                      "price": str(decision.price_usdt),
                                      "estimated_exit_loss": str(decision.estimated_exit_loss_quote),
                                      "trades": {}}
            if updated != self._entries or next_observed != self._last_observed_utc:
                self._save(updated, next_observed)
                self._entries = updated
                self._last_observed_utc = next_observed
            return decision

    def record_fill(self, intent_id: str, trade_id: str,
                    quantity_base: Decimal, price_usdt: Decimal,
                    fee_quote: Decimal) -> bool:
        if (not isinstance(trade_id, str) or not trade_id
                or not _positive(quantity_base) or not _positive(price_usdt)
                or not _nonnegative(fee_quote)):
            raise ValueError("INVENTORY_FILL_INVALID")
        with self._lock, self._file_lock():
            self._verified()
            item = self._entries[intent_id]
            if (self.policy.side == "SELL" and price_usdt < Decimal(item["price"])
                    or self.policy.side == "BUY" and price_usdt > Decimal(item["price"])):
                raise ValueError("INVENTORY_FILL_PRICE_INVALID")
            if any(trade_id in other["trades"] for key, other in self._entries.items()
                   if key != intent_id):
                raise ValueError("INVENTORY_FILL_CONFLICT")
            trade = {"quantity": str(quantity_base), "price": str(price_usdt),
                     "fee_quote": str(fee_quote)}
            previous = item["trades"].get(trade_id)
            if previous is not None:
                if previous != trade:
                    raise ValueError("INVENTORY_FILL_CONFLICT")
                return False
            filled = sum((Decimal(value["quantity"]) for value in item["trades"].values()),
                         Decimal("0"))
            if filled + quantity_base > Decimal(item["quantity"]):
                raise ValueError("INVENTORY_FILL_EXCEEDS_CHILD")
            updated = dict(self._entries)
            updated[intent_id] = {**item, "trades": {**item["trades"], trade_id: trade}}
            self._save(updated, self._last_observed_utc)
            self._entries = updated
            return True

    def report(self, at_utc: datetime) -> InventoryExecutionReport:
        if not _utc(at_utc):
            raise ValueError("INVENTORY_CLOCK_INVALID")
        with self._lock, self._file_lock():
            self._verified()
            filled, pending, _, _ = self._totals()
            shortfall, fee, _ = self._exit_costs()
            remaining = max(Decimal("0"), self.policy.target_base - filled)
            if remaining == 0:
                state = "COMPLETED"
            elif at_utc >= self.policy.deadline_utc:
                state = "EXPIRED_PARTIAL" if filled > 0 else "EXPIRED_UNFILLED"
            else:
                state = "ACTIVE"
            return InventoryExecutionReport(state, filled, pending, remaining,
                                            shortfall + fee, fee)
