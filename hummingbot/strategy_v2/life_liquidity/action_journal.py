"""Host-local durable claims for LIFE quote actions before runner dispatch.

An unresolved action remains claimed after a crash. Only an explicit runner
rejection before executor creation, or reconciled WAL and reservation evidence,
can free its slot. This journal does not provide cross-host ownership.
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class QuoteActionRecord:
    intent_id: str
    controller_id: str
    session_id: str
    epoch: int
    config_version: int
    market: str
    side: str
    level: int
    state: str = "PROPOSED"

    @property
    def slot(self) -> tuple[str, str, int]:
        return self.market, self.side, self.level


class QuoteActionJournal:
    def __init__(self, path: Path, *, account_uid: str | None = None):
        if (account_uid is not None
                and (not isinstance(account_uid, str) or not account_uid.isascii()
                     or not account_uid.isdecimal())):
            raise ValueError("ACTION_JOURNAL_ACCOUNT_INVALID")
        self.path = Path(path)
        self.account_uid = account_uid
        self._lock = RLock()
        self._must_exist = self.path.exists() or self.path.is_symlink()
        self._records = self._read()

    def _read(self) -> dict[str, QuoteActionRecord]:
        if self.path.is_symlink():
            raise ValueError("ACTION_JOURNAL_INVALID")
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if (not isinstance(data, dict) or data.get("schema_version") != 1
                    or not isinstance(data.get("records"), dict)
                    or data.get("account_uid") != self.account_uid):
                raise ValueError("ACTION_JOURNAL_INVALID")
            records = {}
            for key, value in data["records"].items():
                record = QuoteActionRecord(**value)
                if (not isinstance(key, str) or key != record.intent_id or not key
                        or not isinstance(record.controller_id, str) or not record.controller_id
                        or not isinstance(record.session_id, str) or not record.session_id
                        or not isinstance(record.epoch, int) or isinstance(record.epoch, bool)
                        or record.epoch < 1
                        or not isinstance(record.config_version, int)
                        or isinstance(record.config_version, bool) or record.config_version < 1
                        or not isinstance(record.market, str) or not record.market
                        or record.side not in ("BUY", "SELL")
                        or not isinstance(record.level, int) or isinstance(record.level, bool)
                        or record.level < 0
                        or record.state not in ("PROPOSED", "DISPATCHED", "REJECTED", "RECONCILED")):
                    raise ValueError("ACTION_JOURNAL_INVALID")
                records[key] = record
            return records
        except (OSError, TypeError, KeyError, ValueError) as exc:
            raise ValueError("ACTION_JOURNAL_INVALID") from exc

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        if lock_path.is_symlink():
            raise ValueError("ACTION_JOURNAL_INVALID")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _save(self, records: dict[str, QuoteActionRecord]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1,
                           "account_uid": self.account_uid,
                           "records": {key: value.__dict__ for key, value in records.items()}},
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

    def _commit(self, records: dict[str, QuoteActionRecord]) -> None:
        with self._lock, self._file_lock():
            try:
                if self._must_exist and not self.path.is_file():
                    raise ValueError("ACTION_JOURNAL_UNCERTAIN")
                durable = self._read()
            except ValueError as exc:
                raise ValueError("ACTION_JOURNAL_UNCERTAIN") from exc
            if durable != self._records:
                raise ValueError("ACTION_JOURNAL_UNCERTAIN")
            self._save(records)
            self._records = records
            self._must_exist = True

    def all_records(self) -> tuple[QuoteActionRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def verified_records(self) -> tuple[QuoteActionRecord, ...]:
        """Reject a stale in-memory view before authorizing a queued action."""
        with self._lock, self._file_lock():
            try:
                if self._must_exist and not self.path.is_file():
                    raise ValueError("ACTION_JOURNAL_UNCERTAIN")
                if self._read() != self._records:
                    raise ValueError("ACTION_JOURNAL_UNCERTAIN")
            except ValueError as exc:
                raise ValueError("ACTION_JOURNAL_UNCERTAIN") from exc
            return tuple(self._records.values())

    def verified_get(self, intent_id: str) -> QuoteActionRecord:
        for record in self.verified_records():
            if record.intent_id == intent_id:
                return record
        raise KeyError(intent_id)

    def active_records(self) -> tuple[QuoteActionRecord, ...]:
        return tuple(record for record in self.all_records()
                     if record.state in ("PROPOSED", "DISPATCHED"))

    def get(self, intent_id: str) -> QuoteActionRecord:
        with self._lock:
            return self._records[intent_id]

    def claim_batch(self, claims: list[QuoteActionRecord]) -> None:
        if not claims or any(not isinstance(item, QuoteActionRecord)
                             or item.state != "PROPOSED" for item in claims):
            raise ValueError("ACTION_CLAIM_INVALID")
        with self._lock:
            updated = dict(self._records)
            active_slots = {item.slot for item in self.active_records()}
            for item in claims:
                if (item.intent_id in updated or item.slot in active_slots):
                    raise ValueError("ACTION_CLAIM_CONFLICT")
                updated[item.intent_id] = item
                active_slots.add(item.slot)
            self._commit(updated)

    def initialize_empty(self) -> None:
        """Explicitly create a new empty journal before recovery is enabled."""
        if self.path.exists() or self._records:
            raise ValueError("ACTION_JOURNAL_ALREADY_EXISTS")
        self._commit({})

    def transition(self, intent_id: str, *, expected: str, state: str) -> None:
        if (expected, state) not in (("PROPOSED", "DISPATCHED"),
                                     ("PROPOSED", "REJECTED"),
                                     ("PROPOSED", "RECONCILED"),
                                     ("DISPATCHED", "RECONCILED")):
            raise ValueError("ACTION_TRANSITION_INVALID")
        with self._lock:
            record = self._records[intent_id]
            if record.state != expected:
                raise ValueError("ACTION_TRANSITION_CONFLICT")
            self._commit({**self._records, intent_id: replace(record, state=state)})
