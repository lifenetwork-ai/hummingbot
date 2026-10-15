"""Conservative spot risk and reservation state."""

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode, encode
from hummingbot.strategy_v2.life_liquidity.journal_archive import JournalArchive
from hummingbot.strategy_v2.life_liquidity.lazy_archive import PartitionMap, TransformedMap, scratch_index
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _finite(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


def _joint_cash_key(value):
    return isinstance(value, str) and re.fullmatch(r"JOINT:[0-9]+:(TRADE|FUNDING):[^:]+", value) is not None


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


@dataclass(frozen=True)
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
    def __setattr__(self, name, value):
        if getattr(self, "_sealed_evidence", False):
            raise TypeError("verified reservation evidence is read-only")
        super().__setattr__(name, value)

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
        self._archive = None
        self._archive_authority = None
        self._durable_raw = None
        self._save(self.life_balance, self.usdt_balance, self._reservations, self._trades)

    def _payload(self, life_balance: Decimal, usdt_balance: Decimal,
                 reservations: dict[str, _Reservation],
                 trades: dict[str, tuple[str, Decimal, Decimal]],
                 account_events: dict[str, tuple[str, str, Decimal]] | None = None):
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
        return data

    @contextmanager
    def _file_lock(self):
        if self.path is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_name("." + self.path.name + ".lock"),
                     os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def migrate_archive(self, policy):
        """Explicit quiescent migration, retaining fills, fees and cash dedup."""
        with self._lock, self._file_lock():
            if self.path is None or self._archive is not None:
                raise ValueError("ARCHIVE_MIGRATION_UNSAFE")
            self.verify_memory()
            candidate = JournalArchive(self.path, policy, kind="reservations",
                                       limits={key: str(value) for key, value in self.limits.__dict__.items()})
            try:
                candidate.publish(self._payload(self.life_balance, self.usdt_balance,
                                                self._reservations, self._trades),
                                  expected=self._durable_raw)
            except Exception:
                self._uncertain = candidate.uncertain
                raise
            self._archive = candidate
            self._archive_authority = candidate
            self._durable_raw = candidate.raw
            self._verified_disk_cache = None
            self._load_archive_data(candidate.verify())

    def _save(self, life_balance, usdt_balance, reservations, trades, account_events=None):
        if self.path is None:
            return
        self._ensure_healthy()
        data = self._payload(life_balance, usdt_balance, reservations, trades, account_events)
        with self._file_lock():
            if self.path.is_symlink():
                raise ValueError("RISK_JOURNAL_UNCERTAIN")
            current = self.path.read_bytes() if self.path.exists() else None
            if current != self._durable_raw:
                raise ValueError("RISK_JOURNAL_STALE")
            if self._archive is not None:
                try:
                    self._archive.publish(data, expected=self._durable_raw)
                except Exception:
                    self._uncertain = self._archive.uncertain
                    raise
                self._durable_raw = self._archive.raw
                return
            self._write_payload(data)

    def _write_payload(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._durable_raw = encode(data)
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
        if self._archive is not None:
            self._load_archive_data(self._archive.verify())
        else:
            self._reservations = MappingProxyType(dict(reservations))
            self._trades = MappingProxyType(dict(trades))
            self._account_events = MappingProxyType(dict(account_events))

    def _ensure_healthy(self) -> None:
        if (self._archive is not self._archive_authority
                or self._archive is not None and self._archive.path != self.path):
            raise ValueError("RISK_ARCHIVE_BINDING_CHANGED")
        if self._uncertain:
            raise ValueError("RISK_JOURNAL_UNCERTAIN")

    def assert_healthy(self) -> None:
        """Reject new risk while a checkpoint may be ahead of in-memory state."""
        with self._lock:
            self._ensure_healthy()

    def reopen(self):
        """Cold financial verification with the explicitly selected format."""
        self._ensure_healthy()
        return type(self).restore(self.path, limits=self.limits,
                                  archive=None if self._archive is None else self._archive.archive.policy)

    def verified_disk(self):
        """Reuse decoded evidence only when the complete file bytes are unchanged.

        Callers still compare balances, trades and reservations with memory.
        This does not trust mtime, release a claim, or skip durability checks.
        """
        self.assert_healthy()
        if self.path is None or not self.path.is_file() or self.path.is_symlink():
            raise ValueError("RISK_JOURNAL_INVALID")
        if self._archive is not None:
            self._archive.verify()  # Includes exact segment bytes even on cache hits.
        raw = self.path.read_bytes()
        cached = getattr(self, "_verified_disk_cache", None)
        if cached is None or cached[0] != raw or cached[1].limits != self.limits:
            disk = self.reopen()
            if self.path.read_bytes() != raw:
                raise ValueError("RISK_JOURNAL_CHANGED_DURING_VERIFY")
            disk._sealed_evidence = True
            cached = (raw, disk)
            self._verified_disk_cache = cached
        return cached[1]

    def verify_memory(self):
        """Verify an immutable financial cut without rebuilding historical rows."""
        disk = self.verified_disk()
        maps = (self._reservations, self._trades, self._account_events,
                disk._reservations, disk._trades, disk._account_events)
        totals = ((getattr(self, '_filled_base_total', None), disk._filled_base_total)
                  if self._archive is not None else (None, None))
        key = (tuple(id(m) for m in maps), self.life_balance, self.usdt_balance,
               disk.life_balance, disk.usdt_balance, self.limits, disk.limits, totals)
        cached = getattr(self, "_verified_memory", None)
        if cached is not None and cached[0] == key and all(isinstance(m, (MappingProxyType, PartitionMap)) for m in maps):
            return disk
        if (self.life_balance != disk.life_balance or self.usdt_balance != disk.usdt_balance
                or totals[0] != totals[1]
                or any(left != right for left, right in zip(maps[:3], maps[3:]))):
            raise ValueError("RISK_MEMORY_UNVERIFIED")
        if all(isinstance(m, (MappingProxyType, PartitionMap)) for m in maps):
            self._verified_memory = (key, maps)  # Retain objects to fence Python id reuse.
        return disk

    @classmethod
    def restore(cls, path: Path, *, limits: RiskLimits, archive=None) -> "ReservationLedger":
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("RISK_JOURNAL_INVALID") from exc
        if archive is None:
            return cls._restore_snapshot(raw, path=path, limits=limits)
        journal = JournalArchive(path, archive, kind="reservations",
                                 limits={key: str(value) for key, value in limits.__dict__.items()})
        raw, data = journal.read()
        journal.raw = raw
        book = cls.__new__(cls)
        book.path, book.limits = path, limits
        book._lock, book._uncertain = RLock(), False
        book._load_archive_data(data)
        journal.verify()
        book._archive = journal
        book._archive_authority = journal
        book._durable_raw = raw
        return book

    @staticmethod
    def _decode_reservation(key, raw):
        fields = dict(raw['intent'])
        fields['quantity_base'] = Decimal(fields['quantity_base'])
        fields['limit_price_usdt'] = Decimal(fields['limit_price_usdt'])
        intent = SpotIntent(**fields)
        remaining, filled = Decimal(raw['remaining_base']), Decimal(raw['filled_base'])
        state = raw['state']
        if (key != intent.intent_id or not _finite(remaining) or not _finite(filled)
                or filled > intent.quantity_base or state not in ('OPEN', 'CANCEL_PENDING', 'UNKNOWN', 'TERMINAL')
                or state == 'TERMINAL' and remaining != 0
                or state != 'TERMINAL' and remaining + filled != intent.quantity_base):
            raise ValueError('RISK_JOURNAL_INVALID')
        return _Reservation(intent, remaining, filled, state)

    @staticmethod
    def _decode_trade(key, raw):
        intent_id, quantity, price = raw
        quantity, price = Decimal(quantity), Decimal(price)
        if (not key or not isinstance(intent_id, str) or not intent_id
                or not _finite(quantity, positive=True) or not _finite(price, positive=True)):
            raise ValueError('RISK_JOURNAL_INVALID')
        return intent_id, quantity, price

    @staticmethod
    def _decode_event(key, raw):
        kind, currency, amount = raw
        amount = Decimal(amount)
        if (not key or kind not in ('FEE', 'CASHFLOW', 'JOINT') or currency not in ('LIFE', 'USDT')
                or not amount.is_finite() or kind == 'FEE' and not key.startswith('FEE:')
                or kind == 'CASHFLOW' and (not key.startswith('CASHFLOW:') or not key[9:].isdigit() or amount == 0)
                or kind == 'JOINT' and (currency != 'USDT' or not _joint_cash_key(key))):
            raise ValueError('RISK_JOURNAL_INVALID')
        return kind, currency, amount

    def _load_archive_data(self, data):
        """Stream financial validation; disposable disk sums bound cold RAM."""
        try:
            life, usdt = Decimal(data['life_balance']), Decimal(data['usdt_balance'])
            if not _finite(life) or not _finite(usdt):
                raise ValueError('RISK_JOURNAL_INVALID')
            maps = []
            for key, transform in (('reservations', ReservationLedger._decode_reservation),
                                   ('trades', ReservationLedger._decode_trade),
                                   ('account_events', ReservationLedger._decode_event)):
                source = data[key]
                maps.append(PartitionMap({k: transform(k, row) for k, row in source.active.items()},
                                         TransformedMap(source.settled, transform)))
            reservations, trades, events = maps
            total = Decimal('0')
            with scratch_index() as index:
                index.execute('CREATE TABLE fills (intent TEXT PRIMARY KEY, expected TEXT, quantity TEXT)')
                index.execute('CREATE TABLE trades (trade TEXT PRIMARY KEY)')
                for key, row in reservations.items():
                    index.execute('INSERT INTO fills VALUES (?, ?, ?)', (key, str(row.filled_base), '0'))
                for trade_id, (intent_id, quantity, _) in trades.items():
                    prior = index.execute('SELECT quantity FROM fills WHERE intent=?', (intent_id,)).fetchone()
                    if prior is None:
                        raise ValueError('RISK_JOURNAL_INVALID')
                    aggregate = quantity + Decimal(prior[0])
                    index.execute('UPDATE fills SET quantity=? WHERE intent=?', (str(aggregate), intent_id))
                    index.execute('INSERT INTO trades VALUES (?)', (trade_id,))
                    total += quantity
                for expected, recorded in index.execute('SELECT expected, quantity FROM fills'):
                    if Decimal(expected) != Decimal(recorded):
                        raise ValueError('RISK_JOURNAL_INVALID')
                for key, (kind, _, _) in events.items():
                    if kind == 'FEE' and index.execute('SELECT 1 FROM trades WHERE trade=?', (key[4:],)).fetchone() is None:
                        raise ValueError('RISK_JOURNAL_INVALID')
            self.life_balance, self.usdt_balance = life, usdt
            self._reservations, self._trades, self._account_events = maps
            self._filled_base_total = total
            self._verified_memory = None
            self._verified_disk_cache = None
        except (KeyError, TypeError, ValueError, InvalidOperation, OSError) as exc:
            raise ValueError('RISK_JOURNAL_INVALID') from exc

    def _active_reservations(self):
        return (self._reservations.active if isinstance(self._reservations, PartitionMap)
                else self._reservations)

    @classmethod
    def _restore_snapshot(cls, raw: bytes, *, path: Path, limits: RiskLimits,
                          max_reservations=None, max_trades=None) -> "ReservationLedger":
        """Decode detached evidence without I/O; worker output grants no risk."""
        try:
            source_raw = raw
            data = decode(raw)
            if data["schema_version"] not in (1, 2):
                raise ValueError("RISK_JOURNAL_INVALID")
            if (max_reservations is not None and len(data["reservations"]) > max_reservations
                    or max_trades is not None and len(data["trades"]) > max_trades):
                raise ValueError("RISK_JOURNAL_CAPACITY")
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
            filled_by_intent = {}
            for trade_id, raw in data["trades"].items():
                intent_id, quantity, price = raw
                quantity, price = Decimal(quantity), Decimal(price)
                if (not trade_id or intent_id not in reservations
                        or not _finite(quantity, positive=True)
                        or not _finite(price, positive=True)):
                    raise ValueError("RISK_JOURNAL_INVALID")
                trades[trade_id] = (intent_id, quantity, price)
                filled_by_intent[intent_id] = filled_by_intent.get(intent_id, Decimal("0")) + quantity
            for intent_id, item in reservations.items():
                recorded = filled_by_intent.get(intent_id, Decimal("0"))
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
                            or kind not in ("FEE", "CASHFLOW", "JOINT")
                            or currency not in ("LIFE", "USDT")
                            or not amount.is_finite()
                            or fee_key_invalid or cashflow_key_invalid
                            or kind == "JOINT" and (currency != "USDT" or not _joint_cash_key(event_id))):
                        raise ValueError("RISK_JOURNAL_INVALID")
                    account_events[event_id] = (kind, currency, amount)
            book = cls.__new__(cls)
            book.life_balance = life_balance
            book.usdt_balance = usdt_balance
            book.limits = limits
            book.path = path
            book._reservations = MappingProxyType(reservations)
            book._trades = MappingProxyType(trades)
            book._account_events = MappingProxyType(account_events)
            book._lock = RLock()
            book._uncertain = False
            book._archive = None
            book._archive_authority = None
            book._durable_raw = source_raw
            return book
        except (KeyError, TypeError, ValueError, InvalidOperation,
                json.JSONDecodeError, OSError) as exc:
            if isinstance(exc, ValueError) and str(exc) == "RISK_LIMIT_MISMATCH":
                raise
            raise ValueError("RISK_JOURNAL_INVALID") from exc

    def _accept_verified_snapshot(self, raw, evidence):
        """Publish a worker candidate only against current exact bytes/memory."""
        self.assert_healthy()
        if (self._archive is not None or type(evidence) is not type(self) or not getattr(evidence, "_sealed_evidence", False)
                or getattr(evidence, "_snapshot_raw", None) != raw
                or evidence.path != self.path or evidence.limits != self.limits
                or self.path is None or self.path.is_symlink() or not self.path.is_file()
                or self.path.read_bytes() != raw):
            raise ValueError("RISK_JOURNAL_CHANGED_DURING_VERIFY")
        if (self.life_balance != evidence.life_balance or self.usdt_balance != evidence.usdt_balance
                or self._reservations != evidence._reservations or self._trades != evidence._trades
                or self._account_events != evidence._account_events):
            raise ValueError("RISK_MEMORY_UNVERIFIED")
        self._verified_disk_cache = (raw, evidence)

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
            updated = dict(self._account_events.items())
            updated[event_id] = event
            self._commit(life, usdt, self._reservations, self._trades, updated)
            return True

    @property
    def joint_cash_events(self):
        with self._lock:
            return {key: amount for key, (kind, _, amount) in self._account_events.items() if kind == "JOINT"}

    def record_joint_cash(self, event_id: str, amount: Decimal) -> bool:
        """USDT settled SWAP PnL/fee/funding, never an external cash flow.

        IDs embed the source timestamp for chronological accounting replay.
        The joint recovery authority validates complete source/account evidence
        before invoking this idempotent checkpoint.
        """
        if not _joint_cash_key(event_id):
            raise ValueError("JOINT_CASH_ID_INVALID")
        return self._record_account_event(event_id, "JOINT", "USDT", amount)

    def apply_joint_snapshot(self, spot_snapshots, cash_events, cashflows=()):
        """Atomically checkpoint a qualified joint cut in chronological order.

        Use an isolated in-memory ledger so no cross-product debit is written
        ahead of earlier verified proceeds. Existing trade/fee/terminal rules
        still apply. A failed replay cannot leave a partly adjusted wallet.
        """
        with self._lock:
            self._ensure_healthy()
            staged = ReservationLedger(life_balance=self.life_balance, usdt_balance=self.usdt_balance,
                                       limits=self.limits)
            staged._reservations = dict(self._reservations.items())
            staged._trades = dict(self._trades.items())
            staged._account_events = dict(self._account_events.items())
            timeline = [(at_ms, -1, bill, currency, amount) for bill, currency, amount, at_ms in cashflows]
            if any(type(item[0]) is not int or item[0] < 0 for item in timeline):
                raise ValueError('CASHFLOW_TIME_INVALID')
            for key, fills in spot_snapshots:
                prefix, cumulative = [], Decimal("0")
                for trade_id, quantity, price, currency, fee, at_ms in sorted(fills, key=lambda f: (f[5], f[0])):
                    prefix.append((trade_id, quantity, price, currency, fee))
                    cumulative += quantity
                    timeline.append((at_ms, 0, trade_id, key, tuple(prefix), cumulative))
            for event_id, amount in cash_events.items():
                if not _joint_cash_key(event_id):
                    raise ValueError("JOINT_CASH_ID_INVALID")
                timeline.append((int(event_id.split(":")[1]), 1, event_id, amount))
            for item in sorted(timeline, key=lambda v: v[:3]):
                if item[1] == -1:
                    staged.record_cashflow(item[2], item[3], item[4])
                elif item[1] == 1:
                    staged.record_joint_cash(item[2], item[3])
                else:
                    _, _, _, key, fills, cumulative = item
                    if cumulative >= staged._reservations[key].filled_base:
                        staged.apply_fills_snapshot(key, fills, cumulative, require_fees=True)
            for key, fills in spot_snapshots:
                staged.apply_fills_snapshot(key, tuple(f[:5] for f in fills),
                                            sum((f[1] for f in fills), Decimal("0")), require_fees=True)
            if (staged.life_balance, staged.usdt_balance, staged._reservations, staged._trades, staged._account_events) != (
                    self.life_balance, self.usdt_balance, self._reservations, self._trades, self._account_events):
                self._commit(staged.life_balance, staged.usdt_balance, staged._reservations,
                             staged._trades, staged._account_events)

    def record_fee(self, trade_id: str, currency: str, signed_amount: Decimal) -> bool:
        if not isinstance(trade_id, str) or not trade_id or self.trade_intent_id(trade_id) is None:
            raise ValueError("FEE_TRADE_UNKNOWN")
        return self._record_account_event(f"FEE:{trade_id}", "FEE", currency, signed_amount)

    def record_cashflow(self, bill_id: str, currency: str, signed_amount: Decimal) -> bool:
        return self.record_cashflows_batch(((bill_id, currency, signed_amount),))

    def record_cashflows_batch(self, events: tuple[tuple[str, str, Decimal], ...]) -> bool:
        """Checkpoint verified transfers together, including their dedup identities."""
        with self._lock:
            updated = dict(self._account_events.items())
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
                        for item in self._active_reservations().values()
                        if item.intent.side == "BUY"), Decimal("0"))

    @property
    def reservation_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._reservations)

    def reservation_state(self, intent_id):
        """Point lookup without materializing historical reservations."""
        with self._lock:
            item = self._reservations.get(intent_id)
            return None if item is None else ReservationState(item.intent, item.remaining_base, item.filled_base, item.state)

    def has_reservation(self, intent_id) -> bool:
        """Read one in-memory identity; this grants no journal or send authority."""
        with self._lock:
            return intent_id in self._reservations

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
            updated = dict(self._reservations.items())
            updated[intent_id] = replace(item, remaining_base=Decimal("0"),
                                         state="TERMINAL")
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)
            return True

    @property
    def reserved_life(self) -> Decimal:
        with self._lock:
            return sum((item.remaining_base for item in self._active_reservations().values()
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
        reservations = reservations.active if isinstance(reservations, PartitionMap) else reservations
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
            reservations = dict(self._active_reservations())
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
                                      self._filled_base_total if isinstance(self._trades, PartitionMap)
                                      else sum((trade[1] for trade in self._trades.values()), Decimal('0')),
                                      historical=(self._reservations.settled
                                                  if isinstance(self._reservations, PartitionMap) else None))

    def reserve(self, intent: SpotIntent, *, reference_price: Decimal) -> RiskDecision:
        with self._lock:
            if self._archive is not None:
                self._archive.archive.assert_capacity()
            reason = self._reserve_reason(
                intent, reference_price, self._reservations,
                life_balance=self.life_balance, usdt_balance=self.usdt_balance,
                limits=self.limits)
            if reason is not None:
                return RiskDecision(False, reason, None)
            self._commit_new_reservation(intent)
            return RiskDecision(True, "RESERVED", intent.intent_id)

    def reserve_risk_reduction(self, intent: SpotIntent, *, target_base: Decimal) -> RiskDecision:
        """Hold an isolated SELL that reduces an already held spot position.

        Existing inventory may already breach a new-risk limit. Exposure never
        exceeds its starting value in either the unfilled or fully filled case.
        No outstanding order may race this exit or offset its no-reversal proof.
        """
        with self._lock:
            self._ensure_healthy()
            if self._archive is not None:
                self._archive.archive.assert_capacity()
            if (not isinstance(intent, SpotIntent) or intent.side != "SELL"
                    or not _finite(target_base) or intent.intent_id in self._reservations
                    or any(item.remaining_base > 0 for item in self._active_reservations().values())
                    or intent.quantity_base > self.life_balance - max(target_base, self.limits.min_inventory_base)):
                return RiskDecision(False, "EXIT_WOULD_REVERSE_POSITION", None)
            self._commit_new_reservation(intent)
            return RiskDecision(True, "EXIT_RESERVED", intent.intent_id)

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
            updated_reservations = dict(self._reservations.items())
            updated_reservations[intent_id] = replace(
                item, filled_base=item.filled_base + quantity,
                remaining_base=item.remaining_base - quantity)
            updated_trades = dict(self._trades.items())
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
            trades = dict(self._trades.items())
            events = dict(self._account_events.items())
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
                reservations = dict(self._reservations.items())
                reservations[intent_id] = replace(item, remaining_base=remaining,
                                                  filled_base=filled)
                self._commit(life, usdt, reservations, trades, events)
            return changed

    def _cache_verified_archive(self):
        disk = type(self).__new__(type(self))
        for name in ('path', 'limits', 'life_balance', 'usdt_balance', '_reservations', '_trades',
                     '_account_events', '_filled_base_total', '_uncertain', '_durable_raw'):
            setattr(disk, name, getattr(self, name))
        disk._lock = RLock()
        disk._archive = self._archive.verified_copy()
        disk._archive_authority = disk._archive
        disk._sealed_evidence = True
        self._verified_disk_cache = (self._durable_raw, disk)

    def _commit_new_reservation(self, intent):
        """Add a zero-fill hold without changing any verified financial history.

        Public callers hold the memory lock and apply their admission policy.
        The writer lock, complete disk/memory verification and root CAS fence
        the active update. Balances, trades, fees and filled totals stay fixed.
        """
        item = _Reservation(intent, intent.quantity_base)
        if self._archive is None:
            updated = dict(self._reservations.items())
            updated[intent.intent_id] = item
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)
            return
        with self._file_lock():
            self.verify_memory()
            self._archive.archive.assert_capacity()
            if intent.intent_id in self._reservations:
                raise ValueError('DUPLICATE_INTENT')
            active = dict(self._reservations.active)
            active[intent.intent_id] = item
            self._publish_active_reservations(active)

    def _publish_active_reservations(self, active):
        """Caller verified memory and holds both memory and writer locks."""
        self._archive.verify()
        raw_active = dict(self._archive._lazy_cut[2]['active'])
        raw_active['reservations'] = self._payload(self.life_balance, self.usdt_balance, active, {}, {})['reservations']
        try:
            self._archive.publish_active(raw_active, expected=self._durable_raw)
        except Exception:
            self._uncertain = self._archive.uncertain
            raise
        self._durable_raw = self._archive.raw
        self._reservations = PartitionMap(active, self._reservations.settled)
        self._verified_memory = None
        self._cache_verified_archive()

    def _commit_pending_reservation(self, intent_id, item):
        if self._archive is None:
            updated = dict(self._reservations.items())
            updated[intent_id] = item
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)
            return
        with self._file_lock():
            self.verify_memory()
            previous = self._reservations.active[intent_id]
            if replace(previous, state=item.state) != item or item.state == 'TERMINAL':
                raise ValueError('RISK_JOURNAL_INVALID')
            active = dict(self._reservations.active)
            active[intent_id] = item
            self._publish_active_reservations(active)

    def request_cancel(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                self._commit_pending_reservation(intent_id, replace(item, state="CANCEL_PENDING"))

    def mark_unknown(self, intent_id: str) -> None:
        with self._lock:
            item = self._reservations[intent_id]
            if item.state != "TERMINAL":
                self._commit_pending_reservation(intent_id, replace(item, state="UNKNOWN"))

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
            updated = dict(self._reservations.items())
            updated[intent_id] = replace(item, remaining_base=Decimal("0"), state="TERMINAL")
            self._commit(self.life_balance, self.usdt_balance, updated, self._trades)


class ReservationPreview:
    """An isolated snapshot for planning; accepted previews do not reserve funds."""

    def __init__(self, life_balance: Decimal, usdt_balance: Decimal,
                 limits: RiskLimits, reservations: dict[str, _Reservation],
                 filled_base_total: Decimal, *, historical=None):
        self.life_balance = life_balance
        self.usdt_balance = usdt_balance
        self.limits = limits
        self._reservations = reservations
        self.filled_base_total = filled_base_total
        self._historical = historical

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

    def pending_orders(self) -> tuple[tuple[str, Decimal, Decimal], ...]:
        return tuple((item.intent.side, item.remaining_base, item.intent.limit_price_usdt)
                     for item in self._reservations.values() if item.remaining_base > 0)

    def check_and_hold(self, intent: SpotIntent, *, reference_price: Decimal) -> RiskDecision:
        if self._historical is not None and intent.intent_id in self._historical:
            return RiskDecision(False, 'DUPLICATE_INTENT', None)
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
