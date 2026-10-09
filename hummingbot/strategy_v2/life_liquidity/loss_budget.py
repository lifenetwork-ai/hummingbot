"""Persisted execution-loss limits with explicit session, UTC day, and campaign keys."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import RLock


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


@dataclass(frozen=True)
class LossBudgetStatus:
    session_loss_quote: Decimal
    day_loss_quote: Decimal
    campaign_loss_quote: Decimal
    exhausted: bool


class LossBudgetLedger:
    def __init__(self, path: Path, *, campaign_id: str, campaign_limit_quote: Decimal,
                 day_limit_quote: Decimal, session_limit_quote: Decimal):
        if (not campaign_id or not all(_nonnegative(value) and value > 0 for value in (
                campaign_limit_quote, day_limit_quote, session_limit_quote))
                or session_limit_quote > day_limit_quote
                or day_limit_quote > campaign_limit_quote):
            raise ValueError("loss limits must be explicit, positive, and nested")
        self.path = Path(path)
        self.campaign_id = campaign_id
        self.campaign_limit_quote = campaign_limit_quote
        self.day_limit_quote = day_limit_quote
        self.session_limit_quote = session_limit_quote
        self._lock = RLock()
        self._events: dict[str, dict[str, str]] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") == 1:
                raise ValueError("LOSS_BUDGET_MIGRATION_REQUIRED")
            if (data.get("schema_version") != 2 or data.get("campaign_id") != campaign_id
                    or not isinstance(data.get("events"), dict)):
                raise ValueError("loss journal invalid")
            if data.get("policy") != self._policy():
                raise ValueError("LOSS_BUDGET_POLICY_MISMATCH")
            self._events = data["events"]

    def _policy(self) -> dict[str, str]:
        return {"campaign_limit_quote": str(self.campaign_limit_quote),
                "day_limit_quote": str(self.day_limit_quote),
                "session_limit_quote": str(self.session_limit_quote)}

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
        if self.path.is_symlink() or not self.path.is_file():
            if not required and not self.path.exists() and not self._events:
                return
            raise ValueError("LOSS_BUDGET_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (data.get("schema_version") != 2
                or data.get("campaign_id") != self.campaign_id
                or data.get("policy") != self._policy()
                or data.get("events") != self._events):
            raise ValueError("LOSS_BUDGET_JOURNAL_UNAVAILABLE")

    @staticmethod
    def _day(at_utc: datetime) -> str:
        if (not isinstance(at_utc, datetime) or at_utc.tzinfo is None
                or at_utc.utcoffset() != timedelta(0)):
            raise ValueError("UTC timestamp required")
        return at_utc.date().isoformat()

    def _save(self, events: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 2, "campaign_id": self.campaign_id,
                           "policy": self._policy(), "events": events}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def record(self, event_id: str, loss_quote: Decimal, *, session_id: str,
               at_utc: datetime) -> bool:
        if not event_id or not session_id or not _nonnegative(loss_quote):
            raise ValueError("execution loss event invalid")
        event = {"session_id": session_id, "day": self._day(at_utc),
                 "loss_quote": str(loss_quote)}
        with self._lock:
            with self._file_lock():
                self._assert_disk_matches(required=False)
                previous = self._events.get(event_id)
                if previous is not None:
                    if previous != event:
                        raise ValueError("LOSS_EVENT_CONFLICT")
                    return False
                updated = dict(self._events)
                updated[event_id] = event
                self._save(updated)
                self._events = updated
                return True

    def status(self, *, session_id: str, at_utc: datetime) -> LossBudgetStatus:
        if not session_id:
            raise ValueError("session ID required")
        day = self._day(at_utc)
        with self._lock:
            campaign = sum((Decimal(event["loss_quote"]) for event in self._events.values()), Decimal("0"))
            daily = sum((Decimal(event["loss_quote"]) for event in self._events.values()
                         if event["day"] == day), Decimal("0"))
            session = sum((Decimal(event["loss_quote"]) for event in self._events.values()
                           if event["session_id"] == session_id), Decimal("0"))
        exhausted = (campaign >= self.campaign_limit_quote or daily >= self.day_limit_quote
                     or session >= self.session_limit_quote)
        return LossBudgetStatus(session, daily, campaign, exhausted)

    def verified_status(self, *, session_id: str, at_utc: datetime) -> LossBudgetStatus:
        """Read the durable journal again before granting new risk."""
        with self._lock:
            with self._file_lock():
                self._assert_disk_matches(required=True)
                return self.status(session_id=session_id, at_utc=at_utc)

    def can_add_risk(self, *, session_id: str, at_utc: datetime) -> bool:
        return not self.status(session_id=session_id, at_utc=at_utc).exhausted
