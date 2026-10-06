"""Durable intent identity before any network send."""

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable


@dataclass(frozen=True)
class IntentRecord:
    intent_id: str
    client_order_id: str
    session_id: str
    epoch: int
    reservation_id: str
    state: str
    exchange_order_id: str | None = None
    cancel_requested: bool = False
    cancel_attempts: int = 0
    last_cancel_attempt_at: str | None = None
    exchange_terminal_observed: bool = False


class IntentWAL:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = RLock()
        self._records: dict[str, IntentRecord] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
                raise ValueError("intent WAL invalid")
            self._records = {key: IntentRecord(**value) for key, value in data["records"].items()}
            for record in self._records.values():
                if (not isinstance(record.cancel_requested, bool)
                        or not isinstance(record.exchange_terminal_observed, bool)
                        or not isinstance(record.cancel_attempts, int)
                        or isinstance(record.cancel_attempts, bool)
                        or record.cancel_attempts < 0
                        or (record.cancel_attempts > 0) != (record.last_cancel_attempt_at is not None)
                        or record.cancel_attempts > 0 and not record.cancel_requested
                        or record.exchange_terminal_observed and (
                            not record.exchange_order_id
                            or record.state not in ("ACKED", "TERMINAL"))):
                    raise ValueError("intent WAL cancel state invalid")
                if record.last_cancel_attempt_at is not None:
                    try:
                        self._cancel_time(record.last_cancel_attempt_at)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("intent WAL cancel time invalid") from exc

    @staticmethod
    def _cancel_time(value: str) -> datetime:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("cancel time must be UTC")
        return timestamp.astimezone(timezone.utc)

    def _save(self, records: dict[str, IntentRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1,
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

    def _commit(self, record: IntentRecord) -> None:
        updated = dict(self._records)
        updated[record.intent_id] = record
        self._save(updated)
        self._records = updated

    def get(self, intent_id: str) -> IntentRecord:
        with self._lock:
            return self._records[intent_id]

    def scoped_records(self, session_id: str, epoch: int) -> tuple[IntentRecord, ...]:
        with self._lock:
            return tuple(record for record in self._records.values()
                         if record.session_id == session_id and record.epoch == epoch)

    def all_records(self) -> tuple[IntentRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def find_by_client_order_id(self, client_order_id: str) -> IntentRecord:
        with self._lock:
            for record in self._records.values():
                if record.client_order_id == client_order_id:
                    return record
        raise KeyError(client_order_id)

    def mark_cancel_requested(self, intent_id: str) -> bool:
        with self._lock:
            record = self._records[intent_id]
            if record.cancel_requested:
                return False
            self._commit(replace(record, cancel_requested=True))
            return True

    def mark_cancel_attempt(self, intent_id: str, *, at: datetime) -> bool:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("cancel attempt time must be UTC")
        with self._lock:
            record = self._records[intent_id]
            if record.state in ("PREPARED", "ABORTED_BEFORE_SEND", "TERMINAL"):
                raise ValueError("CANCEL_INTENT_NOT_OPEN")
            if record.last_cancel_attempt_at is not None:
                previous = self._cancel_time(record.last_cancel_attempt_at)
                if at < previous:
                    raise ValueError("CANCEL_CLOCK_ROLLBACK")
            self._commit(replace(record, cancel_requested=True,
                                 cancel_attempts=record.cancel_attempts + 1,
                                 last_cancel_attempt_at=at.isoformat()))
            return True

    def mark_exchange_terminal_observed(self, intent_id: str, exchange_order_id: str) -> bool:
        if not isinstance(exchange_order_id, str) or not exchange_order_id:
            raise ValueError("ORDER_EXCHANGE_ID_INVALID")
        with self._lock:
            record = self._records[intent_id]
            if (record.state in ("PREPARED", "ABORTED_BEFORE_SEND")
                    or record.exchange_order_id not in (None, exchange_order_id)):
                raise ValueError("ORDER_EXCHANGE_ID_CONFLICT")
            if record.exchange_terminal_observed:
                return False
            self._commit(replace(record, state="ACKED" if record.state == "SEND_UNKNOWN" else record.state,
                                 exchange_order_id=exchange_order_id, exchange_terminal_observed=True))
            return True

    def mark_terminal(self, intent_id: str, exchange_order_id: str) -> bool:
        if not isinstance(exchange_order_id, str) or not exchange_order_id:
            raise ValueError("ORDER_EXCHANGE_ID_INVALID")
        with self._lock:
            record = self._records[intent_id]
            if record.exchange_order_id not in (None, exchange_order_id):
                raise ValueError("ORDER_EXCHANGE_ID_CONFLICT")
            if record.state == "TERMINAL":
                return False
            self._commit(replace(record, state="TERMINAL",
                                 exchange_order_id=exchange_order_id,
                                 exchange_terminal_observed=True))
            return True

    def pending_reconciliation(self, session_id: str, epoch: int) -> tuple[str, ...]:
        with self._lock:
            return tuple(record.client_order_id for record in self._records.values()
                         if record.session_id == session_id and record.epoch == epoch
                         and record.state in ("PREPARED", "SEND_UNKNOWN"))

    def scoped_order_ids(self, session_id: str, epoch: int) -> tuple[str, ...]:
        """Include ACKed orders; only authoritative terminal reconciliation can remove them."""
        with self._lock:
            return tuple(record.client_order_id for record in self._records.values()
                         if record.session_id == session_id and record.epoch == epoch
                         and record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND"))

    def begin(self, intent_id: str, *, client_order_id: str, session_id: str,
              epoch: int, reservation_id: str) -> None:
        if (not intent_id or not client_order_id or not session_id or not reservation_id
                or not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1):
            raise ValueError("intent identity invalid")
        with self._lock:
            if intent_id in self._records:
                raise ValueError("RECONCILE_BEFORE_RETRY")
            if any(record.client_order_id == client_order_id for record in self._records.values()):
                raise ValueError("CLIENT_ORDER_ID_DUPLICATE")
            prepared = IntentRecord(intent_id, client_order_id, session_id, epoch,
                                    reservation_id, "PREPARED")
            self._commit(prepared)

    def arm_send(self, intent_id: str, *, client_order_id: str, session_id: str,
                 epoch: int, reservation_id: str) -> bool:
        with self._lock:
            record = self._records[intent_id]
            if (record.state != "PREPARED" or record.cancel_requested
                    or (record.client_order_id, record.session_id, record.epoch,
                        record.reservation_id) != (client_order_id, session_id, epoch,
                                                   reservation_id)):
                raise ValueError("RECONCILE_BEFORE_RETRY")
            self._commit(replace(record, state="SEND_UNKNOWN"))
            return True

    def abort_before_send(self, intent_id: str) -> bool:
        with self._lock:
            record = self._records[intent_id]
            if record.state not in ("PREPARED", "ABORTED_BEFORE_SEND"):
                raise ValueError("ORDER_MAY_HAVE_BEEN_SENT")
            try:
                durable_records = IntentWAL(self.path)._records
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("WAL_STATE_UNCERTAIN") from exc
            if durable_records != self._records:
                raise ValueError("WAL_STATE_UNCERTAIN")
            if record.state == "ABORTED_BEFORE_SEND":
                return False
            self._commit(replace(record, state="ABORTED_BEFORE_SEND"))
            return True

    def prepare(self, intent_id: str, *, client_order_id: str, session_id: str,
                epoch: int, reservation_id: str) -> None:
        self.begin(intent_id, client_order_id=client_order_id, session_id=session_id,
                   epoch=epoch, reservation_id=reservation_id)
        self.arm_send(intent_id, client_order_id=client_order_id,
                      session_id=session_id, epoch=epoch, reservation_id=reservation_id)

    def acknowledge(self, intent_id: str, exchange_order_id: str) -> bool:
        if not isinstance(exchange_order_id, str) or not exchange_order_id:
            raise ValueError("ORDER_ACK_UNAVAILABLE")
        with self._lock:
            record = self._records[intent_id]
            if record.state in ("ACKED", "TERMINAL"):
                if record.exchange_order_id != exchange_order_id:
                    raise ValueError("ORDER_ACK_CONFLICT")
                return False
            if record.state != "SEND_UNKNOWN":
                raise ValueError("ORDER_ACK_STATE_INVALID")
            self._commit(replace(record, state="ACKED", exchange_order_id=exchange_order_id))
            return True

    def send_once(self, intent_id: str, *, client_order_id: str, session_id: str,
                  epoch: int, reservation_id: str,
                  sender: Callable[[str], str]) -> str:
        self.prepare(intent_id, client_order_id=client_order_id,
                     session_id=session_id, epoch=epoch, reservation_id=reservation_id)
        # The connector call is deliberately outside the WAL lock. A lost ACK
        # leaves SEND_UNKNOWN and must be reconciled by wire ID before retry.
        exchange_order_id = sender(client_order_id)
        self.acknowledge(intent_id, exchange_order_id)
        return exchange_order_id
