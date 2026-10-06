"""Durable intent identity before any network send."""

import json
import os
import tempfile
from dataclasses import dataclass, replace
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
                                 exchange_order_id=exchange_order_id))
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
                         and record.state != "TERMINAL")

    def prepare(self, intent_id: str, *, client_order_id: str, session_id: str,
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
            self._commit(replace(prepared, state="SEND_UNKNOWN"))

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
