"""Durable, fail-closed LIFE reference sessions with absolute UTC deadlines.

The journal revokes the old epoch before cancellation is requested. A scoped
reconciliation receipt is required before a successor can become active.
"""

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from hummingbot.strategy_v2.life_liquidity.config import SessionConfig, parse_duration_seconds


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("session clock requires timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _deadline(start: datetime, seconds: Decimal) -> datetime:
    microseconds = int(seconds * Decimal("1000000"))
    if microseconds <= 0:
        raise ValueError("session duration must be at least one microsecond")
    return start + timedelta(microseconds=microseconds)


def _anchors(values: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or "LIFE-USDT" not in values:
        raise ValueError("LIFE anchor is required")
    result = {}
    for name, price in values.items():
        if (not isinstance(name, str) or not name or not isinstance(price, Decimal)
                or not price.is_finite() or price <= 0):
            raise ValueError("session anchors must be positive finite Decimals")
        result[name] = price
    return MappingProxyType(result)


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    epoch: int
    started_at: datetime
    expires_at: datetime
    anchors: Mapping[str, Decimal]
    model_version: str
    config_version: int
    reference_mode: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "epoch": self.epoch,
            "started_at": self.started_at.isoformat(), "expires_at": self.expires_at.isoformat(),
            "anchors": {key: str(value) for key, value in self.anchors.items()},
            "model_version": self.model_version, "config_version": self.config_version,
            "reference_mode": self.reference_mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        if not isinstance(data, dict):
            raise ValueError("session journal record invalid")
        record = cls(
            session_id=data["session_id"], epoch=data["epoch"],
            started_at=_utc(datetime.fromisoformat(data["started_at"])),
            expires_at=_utc(datetime.fromisoformat(data["expires_at"])),
            anchors=_anchors({key: Decimal(value) for key, value in data["anchors"].items()}),
            model_version=data["model_version"], config_version=data["config_version"],
            reference_mode=data["reference_mode"],
        )
        if (not isinstance(record.session_id, str) or not record.session_id
                or not isinstance(record.epoch, int) or record.epoch < 1
                or not isinstance(record.config_version, int) or record.config_version < 1
                or not record.model_version or record.reference_mode not in (
                    "market", "bounded_benchmark", "bootstrap_simulation")
                or record.expires_at <= record.started_at):
            raise ValueError("session journal record invalid")
        return record


@dataclass(frozen=True)
class OrderReconciliation:
    """Fresh, full-scope result from the order gateway, not a cancel-request ack."""

    session_id: str
    epoch: int
    observed_at: datetime
    scope_complete: bool
    open_order_ids: tuple[str, ...]
    pending_cancel_ids: tuple[str, ...]
    unknown_order_ids: tuple[str, ...]
    trade_events_reconciled: bool

    def __post_init__(self):
        _utc(self.observed_at)
        if (not self.session_id or not isinstance(self.epoch, int) or self.epoch < 1
                or not isinstance(self.scope_complete, bool)
                or not isinstance(self.trade_events_reconciled, bool)
                or any(not isinstance(value, tuple) for value in (
                    self.open_order_ids, self.pending_cancel_ids, self.unknown_order_ids))):
            raise ValueError("order reconciliation evidence invalid")
        identifiers = self.open_order_ids + self.pending_cancel_ids + self.unknown_order_ids
        if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
            raise ValueError("order reconciliation IDs invalid")


@dataclass(frozen=True)
class SessionJournal:
    primary: SessionRecord
    successor: SessionRecord | None
    state: str
    reason_code: str
    last_seen_at: datetime
    on_expiry: str
    successor_duration_seconds: Decimal | None
    clock_rollback_latched: bool = False
    transition_started_at: datetime | None = None
    reconciled_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": 2, "primary": self.primary.to_dict(),
            "successor": self.successor.to_dict() if self.successor else None,
            "state": self.state, "reason_code": self.reason_code,
            "last_seen_at": self.last_seen_at.isoformat(), "on_expiry": self.on_expiry,
            "successor_duration_seconds": (str(self.successor_duration_seconds)
                                           if self.successor_duration_seconds is not None else None),
            "clock_rollback_latched": self.clock_rollback_latched,
            "transition_started_at": (self.transition_started_at.isoformat()
                                      if self.transition_started_at else None),
            "reconciled_at": self.reconciled_at.isoformat() if self.reconciled_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionJournal":
        if not isinstance(data, dict) or data.get("schema_version") not in (1, 2):
            raise ValueError("session journal schema invalid")
        successor = data.get("successor")
        result = cls(
            primary=SessionRecord.from_dict(data["primary"]),
            successor=SessionRecord.from_dict(successor) if successor else None,
            state=data["state"], reason_code=data["reason_code"],
            last_seen_at=_utc(datetime.fromisoformat(data["last_seen_at"])),
            on_expiry=data["on_expiry"],
            successor_duration_seconds=(Decimal(data["successor_duration_seconds"])
                                        if data["successor_duration_seconds"] is not None else None),
            clock_rollback_latched=data.get("clock_rollback_latched", False),
            transition_started_at=(_utc(datetime.fromisoformat(data["transition_started_at"]))
                                   if data.get("transition_started_at") else None),
            reconciled_at=(_utc(datetime.fromisoformat(data["reconciled_at"]))
                           if data.get("reconciled_at") else None),
        )
        if (result.state not in ("ACTIVE", "PAUSED", "EXPIRED", "TRANSITIONING")
                or result.on_expiry not in ("pause_quotes", "switch_to_market_reference")
                or result.on_expiry == "switch_to_market_reference"
                and (result.successor_duration_seconds is None
                     or not result.successor_duration_seconds.is_finite()
                     or result.successor_duration_seconds <= 0)
                or result.transition_started_at is not None
                and (result.transition_started_at < result.primary.expires_at
                     or result.transition_started_at > result.last_seen_at)
                or result.successor is not None
                and (result.on_expiry != "switch_to_market_reference"
                     or result.transition_started_at is None
                     or result.successor.epoch != result.primary.epoch + 1
                     or result.successor.started_at != result.primary.expires_at
                     or result.successor.expires_at != _deadline(
                         result.primary.expires_at, result.successor_duration_seconds)
                     or result.successor.session_id != str(uuid.uuid5(
                         uuid.NAMESPACE_URL, result.primary.session_id + ":market-successor"))
                     or result.successor.reference_mode != "market"
                     or result.successor.model_version != result.primary.model_version
                     or result.successor.config_version != result.primary.config_version
                     or set(result.successor.anchors) != {"LIFE-USDT"}
                     or result.reconciled_at is None
                     or result.reconciled_at < result.transition_started_at
                     or result.reconciled_at > result.last_seen_at)
                or result.state == "TRANSITIONING" and result.transition_started_at is None):
            raise ValueError("session journal state invalid")
        return result


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> SessionJournal | None:
        if not self.path.exists():
            return None
        with self.path.open(encoding="utf-8") as handle:
            return SessionJournal.from_dict(json.load(handle))

    def save(self, journal: SessionJournal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(journal.to_dict(), handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class SessionManager:
    def __init__(self, store: SessionStore, *, wall_clock: Callable[[], datetime],
                 max_reconciliation_age_ms: int,
                 monotonic_clock: Callable[[], float] = time.monotonic):
        if (not isinstance(max_reconciliation_age_ms, int)
                or isinstance(max_reconciliation_age_ms, bool)
                or max_reconciliation_age_ms <= 0):
            raise ValueError("reconciliation age limit must be explicitly positive")
        self.store = store
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.max_reconciliation_age_ms = max_reconciliation_age_ms
        self._joint_reconciliation = None
        self._journal = store.load()
        self._monotonic_deadline: float | None = None
        if self._journal is not None:
            self._reset_monotonic_deadline()

    @property
    def state(self) -> str:
        return self._journal.state if self._journal else "WAITING_READY"

    @property
    def reason_code(self) -> str:
        return self._journal.reason_code if self._journal else "SESSION_NOT_STARTED"

    @property
    def current_session(self) -> SessionRecord | None:
        if self._journal is None:
            return None
        return self._journal.successor or self._journal.primary

    def _reset_monotonic_deadline(self):
        remaining = max(0, (self.current_session.expires_at - _utc(self.wall_clock())).total_seconds())
        self._monotonic_deadline = self.monotonic_clock() + remaining

    def _commit(self, journal: SessionJournal, *, reset_deadline: bool = False):
        self.store.save(journal)
        self._journal = journal
        if reset_deadline:
            self._reset_monotonic_deadline()

    def _deadline_reached(self) -> bool:
        session = self.current_session
        return (session is not None and (_utc(self.wall_clock()) >= session.expires_at
                                         or self.monotonic_clock() >= self._monotonic_deadline))

    def begin(self, config: SessionConfig, *, anchors: Mapping[str, Decimal], model_version: str,
              config_version: int, reference_mode: str, ready: bool) -> SessionRecord | None:
        if self._journal is not None:
            return self.current_session
        now = _utc(self.wall_clock())
        if not ready:
            return None
        if config.start_policy == "when_ready":
            start = now
        else:
            start = _utc(datetime.fromisoformat(config.start_policy.replace("Z", "+00:00")))
            if now < start:
                return None
        record = SessionRecord(str(uuid.uuid4()), 1, start,
                               _deadline(start, config.duration_seconds),
                               _anchors(anchors), model_version, config_version, reference_mode)
        if (not isinstance(config_version, int) or config_version < 1 or not model_version
                or reference_mode not in ("market", "bounded_benchmark", "bootstrap_simulation")):
            raise ValueError("session model/config version invalid")
        expired = now >= record.expires_at
        journal = SessionJournal(
            record, None, "EXPIRED" if expired else "ACTIVE",
            "SESSION_DEADLINE_REACHED" if expired else "SESSION_ACTIVE",
            now, config.on_expiry, config.successor_duration_seconds)
        self._commit(journal, reset_deadline=True)
        return record

    def can_reduce(self) -> bool:
        """Permit an explicitly bounded exit during a reconciled economic pause.

        Expiry, transition, rollback and absent sessions revoke this permission.
        The controller separately enforces HALT, feeds and account proof.
        """
        return bool(self._journal is not None and self.state in ("ACTIVE", "PAUSED")
                    and not self._journal.clock_rollback_latched
                    and _utc(self.wall_clock()) >= self._journal.last_seen_at
                    and not self._deadline_reached())

    def can_quote(self, *, reference_ready: bool, all_gates_ready: bool,
                  market_reference_ready: bool = False) -> bool:
        if (self._journal is None or self.state != "ACTIVE" or self._journal.clock_rollback_latched
                or _utc(self.wall_clock()) < self._journal.last_seen_at):
            return False
        if self._journal.successor is not None and not market_reference_ready:
            return False
        return bool(reference_ready and all_gates_ready and not self._deadline_reached())

    def tick(self, *, reference_ready: bool, all_gates_ready: bool,
             reconciliation: OrderReconciliation | None = None, market_reference_ready: bool = False,
             market_anchor_usdt: Decimal | None = None) -> str:
        if self._journal is None:
            return "WAITING_READY"
        now = _utc(self.wall_clock())
        current = self._journal
        if now < current.last_seen_at:
            self._commit(replace(current, state="PAUSED", reason_code="CLOCK_ROLLBACK",
                                 clock_rollback_latched=True))
            return self.state
        if current.clock_rollback_latched:
            return self.state
        if current.successor is not None and self._deadline_reached():
            self._commit(replace(current, state="EXPIRED", reason_code="SUCCESSOR_DEADLINE_REACHED",
                                 last_seen_at=now))
            return self.state
        if current.successor is None and self._deadline_reached():
            if current.on_expiry == "pause_quotes":
                self._commit(replace(current, state="EXPIRED", reason_code="SESSION_DEADLINE_REACHED",
                                     last_seen_at=now))
                return self.state
            if current.state != "TRANSITIONING":
                self._commit(replace(current, state="TRANSITIONING",
                                     reason_code="OLD_ORDERS_UNRESOLVED",
                                     last_seen_at=now, transition_started_at=now))
                return self.state
            reason = self._reconciliation_reason(reconciliation, now)
            if reason is not None:
                self._commit(replace(current, reason_code=reason, last_seen_at=now))
                return self.state
            if (not market_reference_ready or not reference_ready or not all_gates_ready
                    or not isinstance(market_anchor_usdt, Decimal)
                    or not market_anchor_usdt.is_finite() or market_anchor_usdt <= 0):
                self._commit(replace(current, reason_code="MARKET_REFERENCE_UNAVAILABLE",
                                     last_seen_at=now))
                return self.state
            parent = current.primary
            start = parent.expires_at
            successor = SessionRecord(
                str(uuid.uuid5(uuid.NAMESPACE_URL, parent.session_id + ":market-successor")),
                parent.epoch + 1, start,
                _deadline(start, current.successor_duration_seconds),
                _anchors({"LIFE-USDT": market_anchor_usdt}),
                parent.model_version, parent.config_version, "market")
            expired = now >= successor.expires_at
            self._commit(replace(current, successor=successor,
                                 state="EXPIRED" if expired else "ACTIVE",
                                 reason_code=("SUCCESSOR_DEADLINE_REACHED" if expired
                                              else "SUCCESSOR_ACTIVE"),
                                 last_seen_at=now, reconciled_at=reconciliation.observed_at),
                         reset_deadline=True)
            return self.state
        if current.state == "EXPIRED":
            return self.state
        if current.successor is not None and not market_reference_ready:
            self._commit(replace(current, state="PAUSED", reason_code="MARKET_REFERENCE_UNAVAILABLE",
                                 last_seen_at=now))
            return self.state
        if not reference_ready or not all_gates_ready:
            self._commit(replace(current, state="PAUSED", reason_code="SESSION_GATE_UNAVAILABLE",
                                 last_seen_at=now))
        else:
            self._commit(replace(current, state="ACTIVE", reason_code="SESSION_ACTIVE",
                                 last_seen_at=now))
        return self.state

    def _reconciliation_reason(self, evidence: OrderReconciliation | None,
                               now: datetime) -> str | None:
        if evidence is None:
            return "OLD_ORDERS_UNRESOLVED"
        if not isinstance(evidence, OrderReconciliation):
            return "RECONCILIATION_INVALID"
        parent = self._journal.primary
        if evidence.session_id != parent.session_id or evidence.epoch != parent.epoch:
            return "RECONCILIATION_SCOPE_MISMATCH"
        if (evidence.observed_at < self._journal.transition_started_at
                or evidence.observed_at < self._journal.last_seen_at
                or (now - evidence.observed_at).total_seconds() * 1000
                > self.max_reconciliation_age_ms):
            return "RECONCILIATION_STALE"
        if evidence.observed_at > now:
            return "RECONCILIATION_FUTURE"
        if not evidence.scope_complete:
            return "RECONCILIATION_INCOMPLETE"
        if evidence.open_order_ids or evidence.pending_cancel_ids or evidence.unknown_order_ids:
            return "OLD_ORDERS_UNRESOLVED"
        if not evidence.trade_events_reconciled:
            return "OLD_FILLS_UNRECONCILED"
        if not self._joint_scope_ready(parent.session_id, parent.epoch):
            return "JOINT_ORDERS_UNRECONCILED"
        return None

    def bind_joint_reconciliation(self, guard) -> None:
        if self._joint_reconciliation is not None or not callable(guard):
            raise ValueError("JOINT_RECONCILIATION_BINDING_INVALID")
        self._joint_reconciliation = guard

    def _joint_scope_ready(self, session_id, epoch):
        try:
            return self._joint_reconciliation is None or self._joint_reconciliation(session_id, epoch) is True
        except Exception:
            return False

    def record_transition_failure(self, reason_code: str) -> None:
        if self._journal is None or self.state != "TRANSITIONING":
            raise ValueError("TRANSITION_NOT_ACTIVE")
        if reason_code not in ("CANCEL_REQUEST_FAILED", "RECONCILIATION_FETCH_FAILED"):
            raise ValueError("TRANSITION_REASON_INVALID")
        self._commit(replace(self._journal, reason_code=reason_code,
                             last_seen_at=_utc(self.wall_clock())))

    def pause(self, reason_code: str) -> None:
        if self._journal is None or self.state == "EXPIRED":
            raise ValueError("SESSION_NOT_ACTIVE")
        self._commit(replace(self._journal, state="PAUSED", reason_code=reason_code,
                             last_seen_at=_utc(self.wall_clock())))

    def update_duration(self, duration: str, *, config_version: int) -> SessionRecord:
        if self._journal is None or self._journal.successor is not None:
            raise ValueError("SESSION_UPDATE_UNAVAILABLE")
        if self.state == "EXPIRED" or self._deadline_reached():
            raise ValueError("SESSION_ALREADY_EXPIRED")
        current = self._journal
        if config_version <= current.primary.config_version:
            raise ValueError("CONFIG_VERSION_NOT_ADVANCED")
        new_primary = replace(current.primary,
                              expires_at=_deadline(current.primary.started_at, parse_duration_seconds(duration)),
                              config_version=config_version)
        expired = self.state == "EXPIRED" or _utc(self.wall_clock()) >= new_primary.expires_at
        updated = replace(current, primary=new_primary,
                          state="EXPIRED" if expired else current.state,
                          reason_code="SESSION_DEADLINE_REACHED" if expired else current.reason_code,
                          last_seen_at=_utc(self.wall_clock()))
        self._commit(updated, reset_deadline=True)
        return new_primary

    def transition_reference(self, *, anchors: Mapping[str, Decimal], model_version: str,
                             config_version: int, old_orders_reconciled: bool) -> SessionRecord:
        if self._journal is None or self.state != "PAUSED" or self._journal.successor is not None:
            raise ValueError("SESSION_NOT_PAUSED")
        if self._deadline_reached():
            raise ValueError("SESSION_DEADLINE_REACHED")
        if not old_orders_reconciled:
            raise ValueError("OLD_ORDERS_UNRESOLVED")
        if not self._joint_scope_ready(self._journal.primary.session_id, self._journal.primary.epoch):
            raise ValueError("JOINT_ORDERS_UNRECONCILED")
        if config_version <= self._journal.primary.config_version or not model_version:
            raise ValueError("CONFIG_VERSION_NOT_ADVANCED")
        changed = replace(self._journal.primary, anchors=_anchors(anchors),
                          model_version=model_version, config_version=config_version,
                          epoch=self._journal.primary.epoch + 1)
        self._commit(replace(self._journal, primary=changed,
                             reason_code="REFERENCE_TRANSITION_PERSISTED",
                             last_seen_at=_utc(self.wall_clock())))
        return changed
