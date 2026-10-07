"""Opt-in, host-local request budget shared by LIFE spot create and safety paths.

Values are explicit policy inputs. This journal is supplemental to the OKX
connector throttler and does not coordinate other hosts or API clients.
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable


@dataclass(frozen=True)
class RequestBudgetPolicy:
    window_ms: int
    max_requests: int
    cancel_reserve: int
    min_slot_refresh_interval_ms: int

    def __post_init__(self):
        values = (self.window_ms, self.max_requests, self.cancel_reserve,
                  self.min_slot_refresh_interval_ms)
        if (any(not isinstance(value, int) or isinstance(value, bool) for value in values)
                or self.window_ms <= 0 or self.max_requests <= 1
                or not 0 < self.cancel_reserve < self.max_requests
                or self.min_slot_refresh_interval_ms <= 0):
            raise ValueError("REQUEST_BUDGET_POLICY_INVALID")


class RequestBudgetExceeded(PermissionError):
    pass


class AccountRequestBudget:
    def __init__(self, path: Path, *, account_uid: str, policy: RequestBudgetPolicy,
                 clock: Callable[[], datetime]):
        if (not isinstance(account_uid, str) or not account_uid.isascii()
                or not account_uid.isdecimal()):
            raise ValueError("REQUEST_BUDGET_ACCOUNT_INVALID")
        if not isinstance(policy, RequestBudgetPolicy) or not callable(clock):
            raise ValueError("REQUEST_BUDGET_POLICY_INVALID")
        self.path = Path(path)
        self.account_uid = account_uid
        self.policy = policy
        self.clock = clock
        self._lock = RLock()
        if self.path.exists() or self.path.is_symlink():
            self._read()

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        if lock_path.is_symlink():
            raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self) -> dict:
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
        try:
            with self.path.open(encoding="utf-8") as handle:
                state = json.load(handle)
            if (not isinstance(state, dict) or state.get("schema_version") != 1
                    or state.get("account_uid") != self.account_uid
                    or state.get("policy") != asdict(self.policy)
                    or not isinstance(state.get("events"), list)
                    or not isinstance(state.get("last_create_by_slot"), dict)):
                raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
            last = state.get("last_at_ms")
            if last is not None and (not isinstance(last, int) or isinstance(last, bool) or last < 0):
                raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
            event_ids = set()
            for event in state["events"]:
                if (not isinstance(event, dict) or set(event) != {"id", "kind", "at_ms"}
                        or not isinstance(event["id"], str) or not event["id"]
                        or event["id"] in event_ids
                        or event["kind"] not in ("CREATE", "CANCEL", "STATUS")
                        or not isinstance(event["at_ms"], int)
                        or isinstance(event["at_ms"], bool) or event["at_ms"] < 0
                        or last is None or event["at_ms"] > last):
                    raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
                event_ids.add(event["id"])
            for slot, at_ms in state["last_create_by_slot"].items():
                if (not isinstance(slot, str) or not slot
                        or not isinstance(at_ms, int) or isinstance(at_ms, bool)
                        or at_ms < 0 or last is None or at_ms > last):
                    raise ValueError("REQUEST_BUDGET_UNAVAILABLE")
            return state
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("REQUEST_BUDGET_UNAVAILABLE") from exc

    def _save(self, state: dict) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True)
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

    def initialize_empty(self) -> None:
        with self._lock, self._file_lock():
            if self.path.exists() or self.path.is_symlink():
                raise ValueError("REQUEST_BUDGET_ALREADY_EXISTS")
            self._save({"schema_version": 1, "account_uid": self.account_uid,
                        "policy": asdict(self.policy), "last_at_ms": None,
                        "last_create_by_slot": {}, "events": []})

    def _now_ms(self) -> int:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset().total_seconds() != 0:
            raise ValueError("REQUEST_BUDGET_CLOCK_INVALID")
        delta = now.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        milliseconds = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
        if milliseconds < 0:
            raise ValueError("REQUEST_BUDGET_CLOCK_INVALID")
        return milliseconds

    def charge(self, kind: str, request_id: str, *, slot: str | None = None) -> None:
        if (kind not in ("CREATE", "CANCEL", "STATUS")
                or not isinstance(request_id, str) or not request_id
                or (kind == "CREATE" and (not isinstance(slot, str) or not slot))
                or (kind != "CREATE" and slot is not None)):
            raise ValueError("REQUEST_BUDGET_REQUEST_INVALID")
        with self._lock, self._file_lock():
            state = self._read()
            now_ms = self._now_ms()
            if state["last_at_ms"] is not None and now_ms < state["last_at_ms"]:
                raise ValueError("REQUEST_BUDGET_CLOCK_ROLLBACK")
            events = [event for event in state["events"]
                      if now_ms - event["at_ms"] < self.policy.window_ms]
            if any(event["id"] == request_id for event in events):
                raise ValueError("REQUEST_ALREADY_ACCOUNTED")
            limit = (self.policy.max_requests if kind == "CANCEL"
                     else self.policy.max_requests - self.policy.cancel_reserve)
            if len(events) >= limit:
                raise RequestBudgetExceeded("ACCOUNT_REQUEST_BUDGET_EXHAUSTED")
            recent_slots = {name: at for name, at in state["last_create_by_slot"].items()
                            if now_ms - at < self.policy.min_slot_refresh_interval_ms}
            if kind == "CREATE" and slot in recent_slots:
                raise RequestBudgetExceeded("SLOT_REFRESH_TOO_SOON")
            if kind == "CREATE":
                recent_slots[slot] = now_ms
            self._save({**state, "last_at_ms": now_ms, "events": [
                *events, {"id": request_id, "kind": kind, "at_ms": now_ms}],
                "last_create_by_slot": recent_slots})
