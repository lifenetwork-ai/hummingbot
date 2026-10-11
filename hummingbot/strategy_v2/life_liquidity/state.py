"""Durable intent identity before any network send."""

import fcntl
import json
import os
import sqlite3
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Callable

from hummingbot.strategy_v2.life_liquidity.journal_archive import JournalArchive
from hummingbot.strategy_v2.life_liquidity.lazy_archive import PartitionMap, TransformedMap, scratch_index


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
    slot_market: str | None = None
    slot_side: str | None = None
    slot_level: int | None = None


class IntentWAL:
    def __setattr__(self, name, value):
        if getattr(self, "_sealed_evidence", False):
            raise TypeError("verified WAL evidence is read-only")
        super().__setattr__(name, value)

    # Resource cache only: exact bytes are reread on every construction. Frozen
    # records and detached maps retain validation without stat-based trust.
    _verified_images = OrderedDict()
    _image_lock = RLock()
    _cache_bytes = 32 * 1024 * 1024
    _cache_files = 8

    @classmethod
    def _remember(cls, path, raw, records, wires):
        with cls._image_lock:
            key = str(path.absolute())
            cls._verified_images.pop(key, None)
            if len(raw) <= cls._cache_bytes:
                cls._verified_images[key] = (raw, records, wires)
            while (len(cls._verified_images) > cls._cache_files
                   or sum(len(v[0]) for v in cls._verified_images.values()) > cls._cache_bytes):
                cls._verified_images.popitem(last=False)

    def __init__(self, path: Path, *, archive=None):
        self.path = Path(path)
        self._lock = RLock()
        self._uncertain = False
        self._must_exist = self.path.exists() or self.path.is_symlink()
        self._records: dict[str, IntentRecord] = {}
        self._archive = None if archive is None else JournalArchive(self.path, archive, kind="wal")
        self._archive_authority = self._archive
        if self._archive is not None and not self.path.is_file():
            raise ValueError("ARCHIVE_JOURNAL_UNAVAILABLE")
        if self.path.exists():
            if self._archive is None:
                raw = self.path.read_bytes()
            else:
                manifest, data = self._archive.read()
                self._archive.raw = manifest
                self._install_archive_data(data)
                self._archive.verify()
                return
            with self._image_lock:
                cached = self._verified_images.get(str(self.path.absolute()))
                if cached is not None and cached[0] == raw:
                    self._records = cached[1]
                    self._wire_image = (self._records, cached[2])
                    return
            data = json.loads(raw)
            if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
                raise ValueError("intent WAL invalid")
            self._records = {key: IntentRecord(**value) for key, value in data["records"].items()}
            self._records = {key: self._decode_record(key, row.__dict__) for key, row in self._records.items()}
            self._records = MappingProxyType(self._records)
            self._index_wires()
            self._remember(self.path, raw, self._records, self._wire_image[1])
        self._index_wires()

    @staticmethod
    def _decode_record(key, value):
        record = IntentRecord(**value)
        if key != record.intent_id:
            raise ValueError('WAL_INTENT_ID_MISMATCH')
        if not isinstance(record.client_order_id, str) or not record.client_order_id:
            raise ValueError('WAL_WIRE_ID_INVALID')
        slot_fields = (record.slot_market, record.slot_side, record.slot_level)
        if (any(value is not None for value in slot_fields)
                and (not isinstance(record.slot_market, str) or not record.slot_market
                     or record.slot_side not in ("BUY", "SELL")
                     or not isinstance(record.slot_level, int)
                     or isinstance(record.slot_level, bool) or record.slot_level < 0)):
            raise ValueError("intent WAL slot identity invalid")
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
                IntentWAL._cancel_time(record.last_cancel_attempt_at)
            except (TypeError, ValueError) as exc:
                raise ValueError("intent WAL cancel time invalid") from exc
        return record

    def _install_archive_data(self, data):
        with self._image_lock:
            self._verified_images.pop(str(self.path.absolute()), None)
        source = data['records']
        records = PartitionMap(
            {key: self._decode_record(key, row) for key, row in source.active.items()},
            TransformedMap(source.settled, IntentWAL._decode_record))
        # Disk scratch bounds cold duplicate validation without keeping all wire
        # IDs in a Python set. It is discarded before this cut can authorize work.
        with scratch_index() as index:
            index.execute('CREATE TABLE wires (wire TEXT PRIMARY KEY)')
            slotted = False
            for record in records.values():
                try:
                    index.execute('INSERT INTO wires VALUES (?)', (record.client_order_id,))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('WAL_WIRE_ID_DUPLICATE') from exc
                slotted |= record.slot_market is not None
        self._records = records
        self._has_slotted_history = slotted
        self._wire_image = (records, MappingProxyType({row.client_order_id: row for row in records.active.values()}))

    def _active_values(self):
        if isinstance(self._records, PartitionMap):
            return self._records.active.values()
        return (r for r in self._records.values() if r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND'))

    def active_records(self):
        """Pending identity scope; historical audit APIs remain explicit."""
        with self._lock:
            return tuple(self._active_values())

    def _cache_verified_archive(self):
        disk = type(self).__new__(type(self))
        for name in ('path', '_uncertain', '_must_exist', '_records', '_wire_image', '_has_slotted_history'):
            setattr(disk, name, getattr(self, name))
        disk._lock = RLock()
        disk._archive = self._archive.verified_copy()
        disk._archive_authority = disk._archive
        disk._sealed_evidence = True
        self._verified_disk_cache = (self._archive.raw, disk)

    def _check_archive_binding(self):
        if (self._archive is not self._archive_authority
                or self._archive is not None and self._archive.path != self.path):
            raise ValueError("WAL_ARCHIVE_BINDING_CHANGED")

    def reopen(self):
        """Cold verification with this instance's explicit archive policy."""
        self._check_archive_binding()
        return type(self)(self.path, archive=None if self._archive is None else self._archive.archive.policy)

    def verified_disk(self):
        """Reuse decoded immutable evidence after exact manifest/segment reads."""
        self._check_archive_binding()
        if self._uncertain or self.path.is_symlink() or not self.path.is_file():
            raise ValueError("WAL_STATE_UNCERTAIN")
        if self._archive is None:
            raw = self.path.read_bytes()
        else:
            raw, _ = self._archive.read()
        cached = getattr(self, "_verified_disk_cache", None)
        if cached is None or cached[0] != raw:
            disk = self.reopen()
            if self.path.read_bytes() != raw:
                raise ValueError("WAL_CHANGED_DURING_VERIFY")
            disk._sealed_evidence = True
            cached = (raw, disk)
            self._verified_disk_cache = cached
        return cached[1]

    def migrate_archive(self, policy):
        """Explicit quiescent migration; every identity tombstone is retained."""
        with self._lock, self._file_lock():
            if self._archive is not None:
                raise ValueError("ARCHIVE_MIGRATION_UNSAFE")
            self.assert_verified()
            candidate = JournalArchive(self.path, policy, kind="wal")
            try:
                candidate.publish({"schema_version": 1,
                                   "records": {key: row.__dict__ for key, row in self._records.items()}},
                                  expected=self.path.read_bytes())
            except Exception:
                self._uncertain = candidate.uncertain
                raise
            self._archive = candidate
            self._archive_authority = candidate
            self._install_archive_data(candidate.verify())
            self._cache_verified_archive()

    def _index_wires(self):
        image = getattr(self, "_wire_image", None)
        if image is not None and image[0] is self._records and isinstance(self._records, (MappingProxyType, PartitionMap)):
            return image[1]
        indexed = {}
        for key, record in self._records.items():
            if key != record.intent_id:
                raise ValueError("WAL_INTENT_ID_MISMATCH")
            if record.client_order_id in indexed:
                raise ValueError("WAL_WIRE_ID_DUPLICATE")
            indexed[record.client_order_id] = record
        # Only internally immutable record cuts can use identity reuse. Test
        # fixtures/legacy mutable maps are re-indexed until disk verification
        # replaces them with an independently validated immutable image.
        self._wire_image = (self._records, MappingProxyType(indexed))
        return self._wire_image[1]

    def assert_verified(self):
        if self._uncertain or not self.path.is_file() or self.path.is_symlink():
            raise ValueError("WAL_STATE_UNCERTAIN")
        disk = self.verified_disk()
        if self._records != disk._records:
            raise ValueError("WAL_STATE_UNCERTAIN")
        # Frozen records behind a detached immutable map can share the verified
        # image. Every subsequent check still reads the exact on-disk bytes.
        self._records = disk._records
        self._wire_image = disk._wire_image

    @staticmethod
    def _cancel_time(value: str) -> datetime:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("cancel time must be UTC")
        return timestamp.astimezone(timezone.utc)

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        if lock_path.is_symlink():
            raise ValueError("WAL_STATE_UNCERTAIN")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _save(self, records: dict[str, IntentRecord]) -> None:
        self._check_archive_binding()
        if self._archive is not None:
            try:
                self._archive.publish({"schema_version": 1,
                                       "records": {key: row.__dict__ for key, row in records.items()}},
                                      expected=self._archive.raw)
            except Exception:
                self._uncertain = self._archive.uncertain
                raise
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                payload = json.dumps({"schema_version": 1,
                                      "records": {key: value.__dict__ for key, value in records.items()}},
                                     sort_keys=True, separators=(",", ":"))
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            # _save's callers can include malformed records in tests; cache
            # only images that have actually passed constructor validation.
        except OSError:
            if replace_attempted:
                self._uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _commit(self, record: IntentRecord) -> None:
        with self._lock, self._file_lock():
            if self._uncertain or self.path.is_symlink() or (self._must_exist and not self.path.is_file()):
                raise ValueError("WAL_STATE_UNCERTAIN")
            if self._archive is not None and record.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                self.assert_verified()
                active = dict(self._records.active)
                if record.intent_id in self._records.settled:
                    raise ValueError('RECONCILE_BEFORE_RETRY')
                active[record.intent_id] = self._decode_record(record.intent_id, record.__dict__)
                try:
                    self._archive.publish_active({key: row.__dict__ for key, row in active.items()},
                                                 expected=self._archive.raw)
                except Exception:
                    self._uncertain = self._archive.uncertain
                    raise
                self._records = PartitionMap(active, self._records.settled)
                self._wire_image = (self._records, MappingProxyType({r.client_order_id: r for r in active.values()}))
                self._has_slotted_history |= record.slot_market is not None
                self._cache_verified_archive()
                return
            try:
                durable_records = self.reopen()._records if self.path.exists() else {}
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("WAL_STATE_UNCERTAIN") from exc
            if durable_records != self._records:
                raise ValueError("WAL_STATE_UNCERTAIN")
            updated = dict(self._records.items())
            updated[record.intent_id] = record
            self._save(updated)
            if self._archive is None:
                self._records = MappingProxyType(updated)
            else:
                self._install_archive_data(self._archive.verify())
            self._must_exist = True

    def initialize_empty(self) -> None:
        """Durably anchor an empty WAL before first-order risk accounting."""
        with self._lock, self._file_lock():
            if (self._uncertain or self._must_exist or self._records
                    or self.path.exists() or self.path.is_symlink()):
                raise ValueError("WAL_INITIALIZATION_UNSAFE")
            self._save({})
            self._must_exist = True

    def has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return intent_id in self._records

    def get(self, intent_id: str) -> IntentRecord:
        with self._lock:
            return self._records[intent_id]

    def scoped_records(self, session_id: str, epoch: int) -> tuple[IntentRecord, ...]:
        with self._lock:
            return tuple(record for record in self._records.values()
                         if record.session_id == session_id and record.epoch == epoch)

    def all_records(self) -> tuple[IntentRecord, ...]:
        """Canonical identity order matches a reopened, key-sorted journal."""
        with self._lock:
            return tuple(row for _, row in sorted(self._records.items()))

    def find_by_client_order_id(self, client_order_id: str) -> IntentRecord:
        with self._lock:
            if isinstance(self._records, PartitionMap):
                active = self._index_wires()
                if client_order_id in active:
                    return active[client_order_id]
                key, row = self._records.settled.source.find(client_order_id)
                return self._decode_record(key, row)
            return self._index_wires()[client_order_id]

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
            return tuple(record.client_order_id for record in self._active_values()
                         if record.session_id == session_id and record.epoch == epoch
                         and record.state in ("PREPARED", "SEND_UNKNOWN"))

    def scoped_order_ids(self, session_id: str, epoch: int) -> tuple[str, ...]:
        """Include ACKed orders; only authoritative terminal reconciliation can remove them."""
        with self._lock:
            return tuple(record.client_order_id for record in self._active_values()
                         if record.session_id == session_id and record.epoch == epoch
                         and record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND"))

    def begin(self, intent_id: str, *, client_order_id: str, session_id: str,
              epoch: int, reservation_id: str, slot_market: str | None = None,
              slot_side: str | None = None, slot_level: int | None = None) -> None:
        if (not intent_id or not client_order_id or not session_id or not reservation_id
                or not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1):
            raise ValueError("intent identity invalid")
        slot_fields = (slot_market, slot_side, slot_level)
        slotted = any(value is not None for value in slot_fields)
        if slotted and (not isinstance(slot_market, str) or not slot_market
                        or slot_side not in ("BUY", "SELL")
                        or not isinstance(slot_level, int) or isinstance(slot_level, bool)
                        or slot_level < 0):
            raise ValueError("SLOT_IDENTITY_INVALID")
        with self._lock:
            if self._archive is not None:
                self._archive.archive.assert_capacity()
            # A second WAL instance may have advanced the journal since this one
            # loaded it. Never overwrite that state while claiming a slot.
            try:
                durable_records = self.verified_disk()._records if self.path.exists() else {}
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("WAL_STATE_UNCERTAIN") from exc
            if durable_records != self._records:
                raise ValueError("WAL_STATE_UNCERTAIN")
            if slotted:
                for record in self._active_values():
                    if record.state in ("TERMINAL", "ABORTED_BEFORE_SEND"):
                        continue
                    if record.slot_market is None:
                        raise ValueError("SLOT_SCOPE_UNPROVEN")
                    if record.session_id != session_id or record.epoch != epoch:
                        raise ValueError("OLD_SESSION_ORDERS_UNRESOLVED")
                    if (record.slot_market, record.slot_side, record.slot_level) == slot_fields:
                        raise ValueError("SLOT_OCCUPIED")
            elif (self._has_slotted_history if isinstance(self._records, PartitionMap)
                  else any(record.slot_market is not None for record in self._records.values())):
                raise ValueError("SLOT_REQUIRED")
            if intent_id in self._records:
                raise ValueError("RECONCILE_BEFORE_RETRY")
            try:
                self.find_by_client_order_id(client_order_id)
            except KeyError:
                pass
            else:
                raise ValueError("CLIENT_ORDER_ID_DUPLICATE")
            prepared = IntentRecord(intent_id, client_order_id, session_id, epoch,
                                    reservation_id, "PREPARED", slot_market=slot_market,
                                    slot_side=slot_side, slot_level=slot_level)
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
                durable_records = self.reopen()._records
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("WAL_STATE_UNCERTAIN") from exc
            if durable_records != self._records:
                raise ValueError("WAL_STATE_UNCERTAIN")
            if record.state == "ABORTED_BEFORE_SEND":
                return False
            self._commit(replace(record, state="ABORTED_BEFORE_SEND"))
            return True

    def abort_rejected_at_send_boundary(self, intent_id: str, *, client_order_id: str,
                                        session_id: str, epoch: int) -> bool:
        """Use only inside a REST pre-send callback that rejected before network I/O."""
        with self._lock:
            record = self._records[intent_id]
            if (record.state != "SEND_UNKNOWN" or record.cancel_requested
                    or record.cancel_attempts != 0 or record.exchange_order_id is not None
                    or record.exchange_terminal_observed
                    or (record.client_order_id, record.session_id, record.epoch) != (
                        client_order_id, session_id, epoch)):
                raise ValueError("ORDER_MAY_HAVE_BEEN_SENT")
            try:
                durable_records = self.reopen()._records
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("WAL_STATE_UNCERTAIN") from exc
            if durable_records != self._records:
                raise ValueError("WAL_STATE_UNCERTAIN")
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
