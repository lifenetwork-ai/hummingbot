"""Conservative spot risk and reservation state."""

import json
import os
import tempfile
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock

from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


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


@dataclass(frozen=True)
class ReservationState:
    intent: SpotIntent
    remaining_base: Decimal
    filled_base: Decimal
    state: str


class ReservationLedger:
    def __init__(self, *, life_balance: Decimal, usdt_balance: Decimal, limits: RiskLimits,
                 path: Path | None = None):
        if not _finite(life_balance) or not _finite(usdt_balance):
            raise ValueError("balances must be finite and nonnegative")
        self.life_balance = life_balance
        self.usdt_balance = usdt_balance
        self.limits = limits
        self.path = Path(path) if path is not None else None
        if self.path is not None and self.path.exists():
            raise ValueError("RISK_JOURNAL_EXISTS_USE_RESTORE")
        self._reservations: dict[str, _Reservation] = {}
        self._trades: dict[str, tuple[str, Decimal, Decimal]] = {}
        self._account_events: dict[str, tuple[str, str, Decimal]] = {}
        self._lock = RLock()
        self._uncertain = False
        self._save(self.life_balance, self.usdt_balance, self._reservations, self._trades)

    def _save(self, life_balance: Decimal, usdt_balance: Decimal,
              reservations: dict[str, _Reservation],
              trades: dict[str, tuple[str, Decimal, Decimal]],
              account_events: dict[str, tuple[str, str, Decimal]] | None = None) -> None:
        if self.path is None:
            return
        data = {
            "schema_version": 2,
            "limits": {key: str(value) for key, value in self.limits.__dict__.items()},
            "life_balance": str(life_balance), "usdt_balance": str(usdt_balance),
            "reservations": {
                key: {"intent": {field: (str(value) if isinstance(value, Decimal) else value)
                                 for field, value in item.intent.__dict__.items()},
                      "remaining_base": str(item.remaining_base),
                      "filled_base": str(item.filled_base), "state": item.state}
                for key, item in reservations.items()},
            "trades": {key: [intent_id, str(quantity), str(price)]
                       for key, (intent_id, quantity, price) in trades.items()},
            "account_events": {
                key: [kind, currency, str(amount)]
                for key, (kind, currency, amount) in (
                    self._account_events if account_events is None else account_events).items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True)
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
                # The disk may contain the new state while this instance still
                # holds the old state. A later commit must not overwrite it.
                self._uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _commit(self, life_balance: Decimal, usdt_balance: Decimal,
                reservations: dict[str, _Reservation],
                trades: dict[str, tuple[str, Decimal, Decimal]],
                account_events: dict[str, tuple[str, str, Decimal]] | None = None) -> None:
        self._ensure_healthy()
        if account_events is None:
            account_events = self._account_events
        self._save(life_balance, usdt_balance, reservations, trades, account_events)
        self.life_balance = life_balance
        self.usdt_balance = usdt_balance
        self._reservations = reservations
        self._trades = trades
        self._account_events = account_events

    def _ensure_healthy(self) -> None:
        if self._uncertain:
            raise ValueError("RISK_JOURNAL_UNCERTAIN")

    def assert_healthy(self) -> None:
        """Reject new risk while a checkpoint may be ahead of in-memory state."""
        with self._lock:
            self._ensure_healthy()

    @classmethod
    def restore(cls, path: Path, *, limits: RiskLimits) -> "ReservationLedger":
        path = Path(path)
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data["schema_version"] not in (1, 2):
                raise ValueError("RISK_JOURNAL_INVALID")
            saved_limits = RiskLimits(**{key: Decimal(value)
                                         for key, value in data["limits"].items()})
            if saved_limits != limits:
                raise ValueError("RISK_LIMIT_MISMATCH")
            life_balance = Decimal(data["life_balance"])
            usdt_balance = Decimal(data["usdt_balance"])
            if not _finite(life_balance) or not _finite(usdt_balance):
                raise ValueError("RISK_JOURNAL_INVALID")
            reservations = {}
            for key, raw in data["reservations"].items():
                fields = dict(raw["intent"])
                fields["quantity_base"] = Decimal(fields["quantity_base"])
                fields["limit_price_usdt"] = Decimal(fields["limit_price_usdt"])
                intent = SpotIntent(**fields)
                remaining = Decimal(raw["remaining_base"])
                filled = Decimal(raw["filled_base"])
                state = raw["state"]
                if (key != intent.intent_id or not _finite(remaining)
                        or not _finite(filled) or filled > intent.quantity_base
                        or state not in ("OPEN", "CANCEL_PENDING", "UNKNOWN", "TERMINAL")
                        or state == "TERMINAL" and remaining != 0
                        or state != "TERMINAL" and remaining + filled != intent.quantity_base):
                    raise ValueError("RISK_JOURNAL_INVALID")
                reservations[key] = _Reservation(intent, remaining, filled, state)
            trades = {}
            for trade_id, raw in data["trades"].items():
                intent_id, quantity, price = raw
                quantity, price = Decimal(quantity), Decimal(price)
                if (not trade_id or intent_id not in reservations
                        or not _finite(quantity, positive=True)
                        or not _finite(price, positive=True)):
                    raise ValueError("RISK_JOURNAL_INVALID")
                trades[trade_id] = (intent_id, quantity, price)
            for intent_id, item in reservations.items():
                recorded = sum((quantity for trade_intent, quantity, _ in trades.values()
                                if trade_intent == intent_id), Decimal("0"))
                if recorded != item.filled_base:
                    raise ValueError("RISK_JOURNAL_INVALID")
            account_events = {}
            if data["schema_version"] == 2:
                for event_id, raw in data["account_events"].items():
                    kind, currency, amount = raw
                    amount = Decimal(amount)
                    fee_key_invalid = (kind == "FEE" and
                                       (not event_id.startswith("FEE:")
                                        or event_id[4:] not in trades))
                    cashflow_key_invalid = (kind == "CASHFLOW" and
                                            (not event_id.startswith("CASHFLOW:")
                                             or not event_id[9:].isdigit() or amount == 0))
                    if (not isinstance(event_id, str) or not event_id
                            or kind not in ("FEE", "CASHFLOW")
                            or currency not in ("LIFE", "USDT")
                            or not amount.is_finite()
                            or fee_key_invalid or cashflow_key_invalid):
                        raise ValueError("RISK_JOURNAL_INVALID")
                    account_events[event_id] = (kind, currency, amount)
            book = cls.__new__(cls)
            book.life_balance = life_balance
            book.usdt_balance = usdt_balance
            book.limits = limits
            book.path = path
            book._reservations = reservations
            book._trades = trades
            book._account_events = account_events
            book._lock = RLock()
            book._uncertain = False
            return book
        except (KeyError, TypeError, ValueError, InvalidOperation,
                json.JSONDecodeError, OSError) as exc:
            if isinstance(exc, ValueError) and str(exc) == "RISK_LIMIT_MISMATCH":
                raise
            raise ValueError("RISK_JOURNAL_INVALID") from exc

    def trade_intent_id(self, trade_id: str) -> str | None:
        with self._lock:
            trade = self._trades.get(trade_id)
            return None if trade is None else trade[0]

    def fee_for_trade(self, trade_id: str) -> tuple[str, Decimal] | None:
        with self._lock:
            event = self._account_events.get(f"FEE:{trade_id}")
            return None if event is None else (event[1], event[2])

    def matches_recorded_fill(self, intent_id: str, trade_id: str,
                              quantity: Decimal, price: Decimal,
                              fee_currency: str, signed_fee: Decimal) -> bool:
        """Compare a runner hint with the durable exchange-reconciled record."""
        with self._lock:
            return (self._trades.get(trade_id) == (intent_id, quantity, price)
                    and self._account_events.get(f"FEE:{trade_id}") == (
                        "FEE", fee_currency, signed_fee))

    @property
    def trade_ids(self) -> set[str]:
        with self._lock:
            return set(self._trades)

    @property
    def cashflow_events(self) -> dict[str, tuple[str, Decimal]]:
        """Return bill-ID keyed transfers for independent accounting reconciliation."""
        with self._lock:
            return {event_id[9:]: (currency, amount)
                    for event_id, (kind, currency, amount) in self._account_events.items()
                    if kind == "CASHFLOW"}

    def _record_account_event(self, event_id: str, kind: str, currency: str,
                              amount: Decimal) -> bool:
        if (currency not in ("LIFE", "USDT") or not isinstance(amount, Decimal)
                or not amount.is_finite()):
            raise ValueError("ACCOUNT_EVENT_INVALID")
        with self._lock:
            event = (kind, currency, amount)
            previous = self._account_events.get(event_id)
            if previous is not None:
                if previous != event:
                    raise ValueError("ACCOUNT_EVENT_CONFLICT")
                return False
            life = self.life_balance + (amount if currency == "LIFE" else Decimal("0"))
            usdt = self.usdt_balance + (amount if currency == "USDT" else Decimal("0"))
            if life < 0 or usdt < 0:
                raise ValueError("ACCOUNT_EVENT_EXCEEDS_BALANCE")
            updated = dict(self._account_events)
            updated[event_id] = event
            self._commit(life, usdt, self._reservations, self._trades, updated)
            return True

    def record_fee(self, trade_id: str, currency: str, signed_amount: Decimal) -> bool:
        if not isinstance(trade_id, str) or not trade_id or self.trade_intent_id(trade_id) is None:
            raise ValueError("FEE_TRADE_UNKNOWN")
        return self._record_account_event(f"FEE:{trade_id}", "FEE", currency, signed_amount)

    def record_cashflow(self, bill_id: str, currency: str, signed_amount: Decimal) -> bool:
        return self.record_cashflows_batch(((bill_id, currency, signed_amount),))

    def record_cashflows_batch(self, events: tuple[tuple[str, str, Decimal], ...]) -> bool:
        """Checkpoint verified transfers together, including their dedup identities."""
        with self._lock:
            updated = dict(self._account_events)
            life, usdt = self.life_balance, self.usdt_balance
            changed = False
            for bill_id, currency, amount in events:
                if (not isinstance(bill_id, str) or not bill_id.isascii()
                        or not bill_id.isdecimal() or currency not in ("LIFE", "USDT")
                        or not isinstance(amount, Decimal) or not amount.is_finite()
                        or amount == 0):
                    raise ValueError("CASHFLOW_INVALID")
                event_id = f"CASHFLOW:{bill_id}"
                event = ("CASHFLOW", currency, amount)
                previous = updated.get(event_id)
                if previous is not None:
                    if previous != event:
                        raise ValueError("ACCOUNT_EVENT_CONFLICT")
                    continue
                life += amount if currency == "LIFE" else Decimal("0")
                usdt += amount if currency == "USDT" else Decimal("0")
                if life < 0 or usdt < 0:
                    raise ValueError("ACCOUNT_EVENT_EXCEEDS_BALANCE")
                updated[event_id] = event
                changed = True
            if changed:
                self._commit(life, usdt, self._reservations, self._trades, updated)
            return changed

    @property
    def reserved_usdt(self) -> Decimal:
        with self._lock:
            return sum((item.remaining_base * item.intent.limit_price_usdt
                        for item in self._reservations.values()
                        if item.intent.side == "BUY"), Decimal("0"))

    @property
    def reservation_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._reservations)

    def reservation_snapshot(self) -> dict[str, ReservationState]:
        """Return immutable values for a cross-journal read; no send permission follows."""
        with self._lock:
            return {
                key: ReservationState(item.intent, item.remaining_base,
                                      item.filled_base, item.state)
                for key, item in self._reservations.items()}

    def has_open_intent(self, intent_id: str) -> bool:
        with self._lock:
            item = self._reservations.get(intent_id)
            return item is not None and item.state == "OPEN"

    def matches_open_intent(self, intent: SpotIntent) -> bool:
        with self._lock:
            self._ensure_healthy()
            item = self._reservations.get(intent.intent_id)
            return (item is not None and item.state == "OPEN" and item.intent == intent
                    and item.remaining_base == intent.quantity_base
                    and item.filled_base == 0)

    def matches_identity(self, intent_id: str, session_id: str, epoch: int) -> bool:
        with self._lock:
            item = self._reservations.get(intent_id)
            return (item is not None and item.intent.session_id == session_id
                    and item.intent.epoch == epoch)

    def is_terminal_intent(self, intent_id: str) -> bool:
        with self._lock:
            item = self._reservations.get(intent_id)
            return item is not None and item.state == "TERMINAL"

    def can_abort_unsent(self, intent_id: str, session_id: str, epoch: int) -> bool:
        with self._lock:
            item = self._reservations.get(intent_id)
            return (item is not None and item.intent.session_id == session_id
                    and item.intent.epoch == epoch and item.filled_base == 0
                    and item.state in ("OPEN", "TERMINAL")
                    and (item.remaining_base == item.intent.quantity_base
                         if item.state == "OPEN" else item.remaining_base == 0))

    def abort_unsent(self, intent_id: str, *, session_id: str, epoch: int,
                     wal: IntentWAL) -> bool:
        """Release only a full, unfilled reservation whose WAL never armed a send."""
        record = wal.get(intent_id)
        if (record.state != "ABORTED_BEFORE_SEND" or record.reservation_id != intent_id
                or record.session_id != session_id or record.epoch != epoch):
            raise ValueError("UNSENT_WAL_PROOF_INVALID")
        with self._lock:
            if not self.can_abort_unsent(intent_id, session_id, epoch):
                raise ValueError("UNSENT_RESERVATION_UNSAFE")
            item = self._reservations[intent_id]
            if item.state == "TERMINAL":
                return False
            updated = dict(self._reservations)
            updated[intent_id] = replace(item, remaining_base=Decimal("0"),
                                         state="TERMINAL")
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)
            return True

    @property
    def reserved_life(self) -> Decimal:
        with self._lock:
            return sum((item.remaining_base for item in self._reservations.values()
                        if item.intent.side == "SELL"), Decimal("0"))

    @staticmethod
    def _reserve_reason(intent: SpotIntent, reference_price: Decimal,
                        reservations: dict[str, _Reservation], *,
                        life_balance: Decimal, usdt_balance: Decimal,
                        limits: RiskLimits) -> str | None:
        if not _finite(reference_price, positive=True):
            return "REFERENCE_UNAVAILABLE"
        if intent.intent_id in reservations:
            return "DUPLICATE_INTENT"
        buy_base = sum((item.remaining_base for item in reservations.values()
                        if item.intent.side == "BUY"), Decimal("0"))
        sell_base = sum((item.remaining_base for item in reservations.values()
                         if item.intent.side == "SELL"), Decimal("0"))
        new_buy_base = buy_base + (intent.quantity_base if intent.side == "BUY" else 0)
        new_sell_base = sell_base + (intent.quantity_base if intent.side == "SELL" else 0)
        reserved_usdt = sum((item.remaining_base * item.intent.limit_price_usdt
                             for item in reservations.values() if item.intent.side == "BUY"), Decimal("0"))
        if intent.side == "BUY" and reserved_usdt + intent.quantity_base * intent.limit_price_usdt > usdt_balance:
            return "INSUFFICIENT_USDT"
        if intent.side == "SELL" and new_sell_base > life_balance:
            return "INSUFFICIENT_LIFE"
        if life_balance + new_buy_base > limits.max_inventory_base:
            return "INVENTORY_MAX"
        if life_balance - new_sell_base < limits.min_inventory_base:
            return "INVENTORY_MIN"
        if (life_balance * reference_price
                + sum((item.remaining_base * item.intent.limit_price_usdt
                       for item in reservations.values()), Decimal("0"))
                + intent.quantity_base * intent.limit_price_usdt > limits.max_gross_quote):
            return "GROSS_EXPOSURE_LIMIT"
        if max(abs(life_balance + new_buy_base),
               abs(life_balance - new_sell_base)) > limits.max_net_base:
            return "NET_EXPOSURE_LIMIT"
        return None

    def preview(self, *, exclude_open_intent: SpotIntent | None = None) -> "ReservationPreview":
        with self._lock:
            self._ensure_healthy()
            reservations = dict(self._reservations)
            if exclude_open_intent is not None:
                if not isinstance(exclude_open_intent, SpotIntent):
                    raise ValueError("RESERVATION_EXCLUSION_UNSAFE")
                item = reservations.get(exclude_open_intent.intent_id)
                if (item is None or item.state != "OPEN"
                        or item.intent != exclude_open_intent
                        or item.remaining_base != item.intent.quantity_base
                        or item.filled_base != 0):
                    raise ValueError("RESERVATION_EXCLUSION_UNSAFE")
                del reservations[exclude_open_intent.intent_id]
            return ReservationPreview(self.life_balance, self.usdt_balance,
                                      self.limits, reservations,
                                      sum((trade[1] for trade in self._trades.values()),
                                          Decimal("0")))

    def reserve(self, intent: SpotIntent, *, reference_price: Decimal) -> RiskDecision:
        with self._lock:
            reason = self._reserve_reason(
                intent, reference_price, self._reservations,
                life_balance=self.life_balance, usdt_balance=self.usdt_balance,
                limits=self.limits)
            if reason is not None:
                return RiskDecision(False, reason, None)
            updated = dict(self._reservations)
            updated[intent.intent_id] = _Reservation(intent, intent.quantity_base)
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)
            return RiskDecision(True, "RESERVED", intent.intent_id)

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
                usdt_balance = self.usdt_balance - quantity * price
                life_balance = self.life_balance + quantity
            else:
                life_balance = self.life_balance - quantity
                usdt_balance = self.usdt_balance + quantity * price
            updated_reservations = dict(self._reservations)
            updated_reservations[intent_id] = replace(
                item, filled_base=item.filled_base + quantity,
                remaining_base=item.remaining_base - quantity)
            updated_trades = dict(self._trades)
            updated_trades[trade_id] = trade
            self._commit(life_balance, usdt_balance, updated_reservations, updated_trades)
            return True

    def apply_fills_snapshot(
            self, intent_id: str,
            fills: tuple[tuple[str, Decimal, Decimal, str | None, Decimal | None], ...],
            cumulative: Decimal, *, require_fees: bool = False) -> bool:
        """Checkpoint a complete exchange fill snapshot and its fees together."""
        if not _finite(cumulative):
            raise ValueError("FILL_CUMULATIVE_INVALID")
        with self._lock:
            item = self._reservations[intent_id]
            if cumulative < item.filled_base or cumulative > item.intent.quantity_base:
                raise ValueError("FILL_CUMULATIVE_REGRESSION")
            life, usdt = self.life_balance, self.usdt_balance
            remaining, filled = item.remaining_base, item.filled_base
            trades = dict(self._trades)
            events = dict(self._account_events)
            seen = set()
            changed = False
            snapshot_total = Decimal("0")
            for trade_id, quantity, price, fee_currency, signed_fee in fills:
                if (not isinstance(trade_id, str) or not trade_id or trade_id in seen
                        or not _finite(quantity, positive=True)
                        or not _finite(price, positive=True)):
                    raise ValueError("FILL_INVALID")
                seen.add(trade_id)
                snapshot_total += quantity
                trade = (intent_id, quantity, price)
                previous = trades.get(trade_id)
                if previous is not None and previous != trade:
                    raise ValueError("FILL_ID_CONFLICT")
                if previous is None:
                    if (item.state == "TERMINAL" or quantity > remaining
                            or item.intent.side == "BUY" and price > item.intent.limit_price_usdt
                            or item.intent.side == "SELL" and price < item.intent.limit_price_usdt):
                        raise ValueError("FILL_EXCEEDS_INTENT")
                    if item.intent.side == "BUY":
                        life += quantity
                        usdt -= quantity * price
                    else:
                        life -= quantity
                        usdt += quantity * price
                    remaining -= quantity
                    filled += quantity
                    trades[trade_id] = trade
                    changed = True
                if fee_currency is None and signed_fee is None:
                    if require_fees:
                        raise ValueError("ORDER_FEE_UNTRUSTED")
                else:
                    if (fee_currency not in ("LIFE", "USDT")
                            or not isinstance(signed_fee, Decimal)
                            or not signed_fee.is_finite()):
                        raise ValueError("ORDER_FEE_UNTRUSTED")
                    event_id = f"FEE:{trade_id}"
                    event = ("FEE", fee_currency, signed_fee)
                    prior_fee = events.get(event_id)
                    if prior_fee is not None and prior_fee != event:
                        raise ValueError("ACCOUNT_EVENT_CONFLICT")
                    if prior_fee is None:
                        life += signed_fee if fee_currency == "LIFE" else Decimal("0")
                        usdt += signed_fee if fee_currency == "USDT" else Decimal("0")
                        events[event_id] = event
                        changed = True
            if snapshot_total != cumulative or filled != cumulative:
                raise ValueError("FILL_CUMULATIVE_MISMATCH")
            if life < 0 or usdt < 0:
                raise ValueError("ACCOUNT_EVENT_EXCEEDS_BALANCE")
            if changed:
                reservations = dict(self._reservations)
                reservations[intent_id] = replace(item, remaining_base=remaining,
                                                  filled_base=filled)
                self._commit(life, usdt, reservations, trades, events)
            return changed

    def request_cancel(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                updated = dict(self._reservations)
                updated[intent_id] = replace(item, state="CANCEL_PENDING")
                self._commit(self.life_balance, self.usdt_balance, updated, self._trades)

    def mark_unknown(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                updated = dict(self._reservations)
                updated[intent_id] = replace(item, state="UNKNOWN")
                self._commit(self.life_balance, self.usdt_balance, updated, self._trades)

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
            updated = dict(self._reservations)
            updated[intent_id] = replace(item, remaining_base=Decimal("0"), state="TERMINAL")
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)


class ReservationPreview:
    """An isolated snapshot for planning; accepted previews do not reserve funds."""

    def __init__(self, life_balance: Decimal, usdt_balance: Decimal,
                 limits: RiskLimits, reservations: dict[str, _Reservation],
                 filled_base_total: Decimal):
        self.life_balance = life_balance
        self.usdt_balance = usdt_balance
        self.limits = limits
        self._reservations = reservations
        self.filled_base_total = filled_base_total

    @property
    def projected_inventory_after_buys(self) -> Decimal:
        return self.life_balance + sum(
            (item.remaining_base for item in self._reservations.values()
             if item.intent.side == "BUY"), Decimal("0"))

    @property
    def projected_inventory_after_sells(self) -> Decimal:
        return self.life_balance - sum(
            (item.remaining_base for item in self._reservations.values()
             if item.intent.side == "SELL"), Decimal("0"))

    def unresolved_quantity_base(self, side: str) -> Decimal:
        if side not in ("BUY", "SELL"):
            raise ValueError("RESERVATION_SIDE_INVALID")
        return sum((item.remaining_base for item in self._reservations.values()
                    if item.intent.side == side), Decimal("0"))

    def check_and_hold(self, intent: SpotIntent, *, reference_price: Decimal) -> RiskDecision:
        reason = ReservationLedger._reserve_reason(
            intent, reference_price, self._reservations,
            life_balance=self.life_balance, usdt_balance=self.usdt_balance,
            limits=self.limits)
        if reason is not None:
            return RiskDecision(False, reason, None)
        self._reservations[intent.intent_id] = _Reservation(intent, intent.quantity_base)
        return RiskDecision(True, "PREVIEW_READY", None)


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
