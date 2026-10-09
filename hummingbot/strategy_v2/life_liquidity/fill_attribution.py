"""Opt-in, replayable attribution of exchange-reconciled LIFE spot fills.

Only USDT-denominated fill fees are accepted in this first offline slice.
Missing independent fill-time values or unconverted LIFE fees retain the fill
as unattributed and block additional risk.
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals
from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@dataclass(frozen=True)
class IndependentFillObservation:
    price_usdt: Decimal
    value_at_ms: int
    observed_at_ms: int
    source_kind: str


@dataclass(frozen=True)
class ReconciledFillRecord:
    trade_id: str
    side: str
    quantity_base: Decimal
    price_usdt: Decimal
    fill_at_ms: int


class ReconciledFillAttributor:
    @staticmethod
    def _approvals_valid(approvals: CashflowApprovals) -> bool:
        anchor = approvals.anchor_bill_id
        if (not isinstance(anchor, str) or not anchor.isascii()
                or not anchor.isdecimal() or int(anchor) <= 0
                or not isinstance(approvals.approved, dict)):
            return False
        for bill_id, event in approvals.approved.items():
            if (not isinstance(bill_id, str) or not bill_id.isascii()
                    or not bill_id.isdecimal() or int(bill_id) <= int(anchor)
                    or not isinstance(event, tuple) or len(event) != 2):
                return False
            currency, amount = event
            if (currency not in ("LIFE", "USDT") or not isinstance(amount, Decimal)
                    or not amount.is_finite() or amount == 0):
                return False
        return True

    def __init__(self, path: Path, *, wal: IntentWAL, reservations: ReservationLedger,
                 loss_budget: LossBudgetLedger, opening_life: Decimal,
                 opening_usdt: Decimal, opening_independent_price_usdt: Decimal,
                 independent_value: Callable[[SpotFill], IndependentFillObservation],
                 max_reference_skew_ms: int, create: bool,
                 cashflow_approvals: CashflowApprovals | None = None):
        if (not isinstance(max_reference_skew_ms, int) or isinstance(max_reference_skew_ms, bool)
                or max_reference_skew_ms < 0 or not callable(independent_value)
                or not isinstance(create, bool)
                or cashflow_approvals is not None
                and (not isinstance(cashflow_approvals, CashflowApprovals)
                     or not self._approvals_valid(cashflow_approvals))):
            raise ValueError("FILL_ATTRIBUTION_POLICY_INVALID")
        # CapitalLedger validates finite opening values and positive NAV.
        CapitalLedger(opening_life=opening_life, opening_usdt=opening_usdt,
                      opening_independent_price_usdt=opening_independent_price_usdt)
        self.path = Path(path)
        self.wal = wal
        self.reservations = reservations
        self.loss_budget = loss_budget
        self.opening_life = opening_life
        self.opening_usdt = opening_usdt
        self.opening_independent_price_usdt = opening_independent_price_usdt
        self.independent_value = independent_value
        self.max_reference_skew_ms = max_reference_skew_ms
        self.cashflow_approvals = (None if cashflow_approvals is None else CashflowApprovals(
            cashflow_approvals.anchor_bill_id, dict(cashflow_approvals.approved)))
        self._events: dict[str, dict[str, str | int]] = {}
        self._cashflows: dict[str, dict[str, str]] = {}
        if create:
            with self._file_lock():
                if self.path.exists() or self.path.is_symlink():
                    raise ValueError("FILL_ATTRIBUTION_EXISTS_USE_RESTORE")
                self._save(self._events)
        else:
            data = self._read()
            self._events = data["events"]
            self._cashflows = data.get("cashflows", {})
            self.capital()  # Reject malformed or conflicting persisted fills.

    def _policy(self) -> dict:
        policy = {"opening_life": str(self.opening_life), "opening_usdt": str(self.opening_usdt),
                  "opening_independent_price_usdt": str(self.opening_independent_price_usdt),
                  "max_reference_skew_ms": self.max_reference_skew_ms,
                  "loss_campaign_id": self.loss_budget.campaign_id}
        if self.cashflow_approvals is not None:
            policy["cashflow_anchor_bill_id"] = self.cashflow_approvals.anchor_bill_id
            policy["approved_cashflows"] = {
                bill_id: [currency, str(amount)]
                for bill_id, (currency, amount) in self.cashflow_approvals.approved.items()}
        return policy

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

    def _read(self) -> dict:
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("FILL_ATTRIBUTION_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (data.get("schema_version") != 1 or data.get("policy") != self._policy()
                or not isinstance(data.get("events"), dict)
                or not isinstance(data.get("cashflows", {}), dict)):
            raise ValueError("FILL_ATTRIBUTION_JOURNAL_INVALID")
        return data

    def _verified(self) -> bool:
        data = self._read()
        return (data["events"] == self._events
                and data.get("cashflows", {}) == self._cashflows)

    def _save(self, events: dict, cashflows: dict | None = None) -> None:
        if cashflows is None:
            cashflows = self._cashflows
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "policy": self._policy(),
                           "events": events, "cashflows": cashflows},
                          handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _at_utc(event: dict) -> datetime:
        return datetime.fromtimestamp(event["fill_at_ms"] / 1000, tz=timezone.utc)

    @staticmethod
    def _loss(event: dict) -> Decimal:
        sign = Decimal("1") if event["side"] == "BUY" else Decimal("-1")
        edge = (sign * (Decimal(event["independent_value_usdt"])
                        - Decimal(event["price_usdt"])) * Decimal(event["quantity_base"])
                - Decimal(event["fee_cost_quote"]))
        return max(Decimal("0"), -edge)

    def capital(self) -> CapitalLedger:
        capital = CapitalLedger(
            opening_life=self.opening_life, opening_usdt=self.opening_usdt,
            opening_independent_price_usdt=self.opening_independent_price_usdt)
        for bill_id, event in sorted(self._cashflows.items()):
            amount = Decimal(event["amount"])
            if event["currency"] != "USDT":
                raise ValueError("FILL_ATTRIBUTION_CASHFLOW_VALUE_UNAVAILABLE")
            if amount > 0:
                capital.record_cashflow(bill_id, amount)
        for trade_id, event in sorted(
                self._events.items(), key=lambda item: (item[1]["fill_at_ms"], item[0])):
            before = capital.execution_loss_quote
            capital.record_fill(
                trade_id, event["side"], Decimal(event["quantity_base"]),
                Decimal(event["price_usdt"]), Decimal(event["fee_cost_quote"]),
                independent_value_usdt=Decimal(event["independent_value_usdt"]))
            if capital.execution_loss_quote - before != Decimal(event["loss_quote"]):
                raise ValueError("FILL_ATTRIBUTION_LOSS_MISMATCH")
        for bill_id, event in sorted(self._cashflows.items()):
            amount = Decimal(event["amount"])
            if amount < 0:
                capital.record_cashflow(bill_id, amount)
        return capital

    def _record_matches(self, trade_id: str, event: dict) -> bool:
        record = self.wal.get(event["intent_id"])
        return (record.client_order_id == event["wire_id"]
                and record.slot_market == "LIFE-USDT"
                and record.slot_side == event["side"]
                and record.session_id == event["session_id"]
                and self.reservations.matches_recorded_fill(
                    event["intent_id"], trade_id, Decimal(event["quantity_base"]),
                    Decimal(event["price_usdt"]), "USDT", -Decimal(event["fee_cost_quote"])))

    def _durable_sources_match(self, *, require_cashflows_attributed: bool = True) -> ReservationLedger | None:
        self.reservations.assert_healthy()
        path = self.reservations.path
        if path is None or not path.is_file() or path.is_symlink():
            return None
        durable = ReservationLedger.restore(path, limits=self.reservations.limits)
        if (durable.trade_ids != self.reservations.trade_ids
                or durable.life_balance != self.reservations.life_balance
                or durable.usdt_balance != self.reservations.usdt_balance
                or durable.cashflow_events != self.reservations.cashflow_events
                or self.wal.path.is_symlink() or not self.wal.path.is_file()):
            return None
        durable_wal = IntentWAL(self.wal.path)
        if ({record.intent_id: record for record in durable_wal.all_records()}
                != {record.intent_id: record for record in self.wal.all_records()}):
            return None
        source_cashflows = durable.cashflow_events
        if (self.cashflow_approvals is None and source_cashflows
                or self.cashflow_approvals is not None
                and source_cashflows != self.cashflow_approvals.approved
                or any(currency != "USDT" for currency, _ in source_cashflows.values())):
            return None
        if not self._verified() or durable.trade_ids != set(self._events):
            return None
        for trade_id, event in self._events.items():
            if (not self._record_matches(trade_id, event)
                    or not durable.matches_recorded_fill(
                        event["intent_id"], trade_id, Decimal(event["quantity_base"]),
                        Decimal(event["price_usdt"]), "USDT",
                        -Decimal(event["fee_cost_quote"]))):
                return None
        if require_cashflows_attributed:
            if (set(source_cashflows) != set(self._cashflows)
                    or any(self._cashflows[bill_id] != {
                        "currency": currency, "amount": str(amount)}
                        for bill_id, (currency, amount) in source_cashflows.items())):
                return None
        return durable

    def apply_approved_cashflows(self) -> bool:
        """Complete a verified bill scan after its reservation checkpoint."""
        try:
            with self._file_lock():
                if self.cashflow_approvals is None:
                    return False
                durable = self._durable_sources_match(require_cashflows_attributed=False)
                if durable is None:
                    return False
                source = durable.cashflow_events
                if (source != self.cashflow_approvals.approved
                        or any(currency != "USDT" for currency, _ in source.values())):
                    return False
                updated = {bill_id: {"currency": currency, "amount": str(amount)}
                           for bill_id, (currency, amount) in source.items()}
                if any(updated.get(bill_id) != event
                       for bill_id, event in self._cashflows.items()):
                    return False
                if updated != self._cashflows:
                    self._save(self._events, updated)
                    self._cashflows = updated
            return self.recover()
        except Exception:
            return False

    def ready(self) -> bool:
        try:
            if self._durable_sources_match() is None:
                return False
            capital = self.capital()
            if (capital.life_balance != self.reservations.life_balance
                    or capital.usdt_balance != self.reservations.usdt_balance):
                return False
            for trade_id, event in self._events.items():
                if not self.loss_budget.matches_event(
                        f"fill:{trade_id}", Decimal(event["loss_quote"]),
                        session_id=event["session_id"], at_utc=self._at_utc(event)):
                    return False
            return True
        except Exception:
            return False

    def verified_fills(self) -> tuple[ReconciledFillRecord, ...] | None:
        """Expose only durable, fully attributed fill identities to diagnostics."""
        if not self.ready():
            return None
        try:
            return tuple(ReconciledFillRecord(
                trade_id, event["side"], Decimal(event["quantity_base"]),
                Decimal(event["price_usdt"]), event["fill_at_ms"])
                for trade_id, event in sorted(self._events.items()))
        except Exception:
            return None

    def recover(self) -> bool:
        """Idempotently finish a loss write interrupted after attribution commit."""
        try:
            if self._durable_sources_match() is None:
                return False
            for trade_id, event in self._events.items():
                self.loss_budget.record(
                    f"fill:{trade_id}", Decimal(event["loss_quote"]),
                    session_id=event["session_id"], at_utc=self._at_utc(event))
            return self.ready()
        except Exception:
            return False

    def apply(self, wire_id: str, fills: tuple[SpotFill, ...]) -> bool:
        try:
            with self._file_lock():
                if not self._verified():
                    return False
                record = self.wal.find_by_client_order_id(wire_id)
                if (record.slot_market != "LIFE-USDT" or record.slot_side not in ("BUY", "SELL")
                        or not isinstance(fills, tuple)):
                    return False
                for fill in fills:
                    if (not isinstance(fill, SpotFill) or not isinstance(fill.fill_at_ms, int)
                            or isinstance(fill.fill_at_ms, bool) or fill.fill_at_ms <= 0
                            or fill.fee_currency != "USDT" or not isinstance(fill.signed_fee, Decimal)
                            or not fill.signed_fee.is_finite()
                            or not self.reservations.matches_recorded_fill(
                                record.intent_id, fill.trade_id, fill.quantity_base,
                                fill.price_usdt, "USDT", fill.signed_fee)):
                        return False
                    stable = {"intent_id": record.intent_id, "wire_id": wire_id,
                              "session_id": record.session_id, "side": record.slot_side,
                              "quantity_base": str(fill.quantity_base),
                              "price_usdt": str(fill.price_usdt),
                              "fee_cost_quote": str(-fill.signed_fee),
                              "fill_at_ms": fill.fill_at_ms}
                    self._at_utc(stable)
                    previous = self._events.get(fill.trade_id)
                    if previous is not None:
                        if any(previous.get(key) != value for key, value in stable.items()):
                            return False
                        continue
                    reference = self.independent_value(fill)
                    if (not isinstance(reference, IndependentFillObservation)
                            or reference.source_kind != "independent_market"
                            or not isinstance(reference.price_usdt, Decimal)
                            or not reference.price_usdt.is_finite() or reference.price_usdt <= 0
                            or not isinstance(reference.value_at_ms, int)
                            or isinstance(reference.value_at_ms, bool)
                            or not isinstance(reference.observed_at_ms, int)
                            or isinstance(reference.observed_at_ms, bool)
                            or reference.observed_at_ms < reference.value_at_ms
                            or abs(reference.value_at_ms - fill.fill_at_ms)
                            > self.max_reference_skew_ms
                            or abs(reference.observed_at_ms - fill.fill_at_ms)
                            > self.max_reference_skew_ms):
                        return False
                    event = {**stable, "independent_value_usdt": str(reference.price_usdt)}
                    event["loss_quote"] = str(self._loss(event))
                    updated = {**self._events, fill.trade_id: event}
                    self._save(updated)
                    self._events = updated
            return self.recover()
        except Exception:
            return False
