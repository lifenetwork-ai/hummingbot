"""Reusable spot ownership coverage, fenced by independently verified cuts.

This is a disposable in-process proof, never durable send authority. Every use
still verifies exact disk bytes and financial memory. Archived settled branches
are merge-validated once per pair of immutable cuts; active rows are checked on
every use. Legacy journals retain complete coverage with whole-cut invalidation.
"""
from types import MappingProxyType

from hummingbot.strategy_v2.life_liquidity.lazy_archive import LazyArchiveMap, PartitionMap, TransformedMap


def _same_cut(left, right):
    if isinstance(left, MappingProxyType) and left is right:
        return True
    return (isinstance(left, TransformedMap) and isinstance(right, TransformedMap)
            and isinstance(left.source, LazyArchiveMap) and isinstance(right.source, LazyArchiveMap)
            and left == right)


def _rows(mapping):
    # Archived maps already yield canonical key order with segment-sized RAM.
    return mapping.items() if isinstance(mapping, (PartitionMap, TransformedMap)) else iter(sorted(mapping.items()))


class SpotJournalProof:
    def __init__(self, wal, reservations):
        self.wal, self.reservations = wal, reservations
        self._proof = None

    def _verify(self):
        wal = self.wal
        # An uninitialized empty WAL is permitted only for initial planning.
        # The protected gateway must durably claim identity before any send.
        if not (not wal._must_exist and not wal._uncertain and wal._archive is None
                and not wal._records and not wal.path.exists() and not wal.path.is_symlink()):
            wal.assert_verified()
        self.reservations.verify_memory()
        return wal._records, self.reservations._reservations

    @staticmethod
    def _coverage(wal_rows, reservation_rows, *, settled=False):
        """Merge complete key unions, retaining only active WAL identities."""
        reservations = iter(_rows(reservation_rows))
        next_reservation = next(reservations, None)
        active = []
        for key, record in _rows(wal_rows):
            if record.intent_id != key or record.reservation_id != key:
                raise ValueError("SPOT_JOURNAL_IDENTITY_CONFLICT")
            if next_reservation is not None and next_reservation[0] < key:
                raise ValueError("SPOT_JOURNAL_ORPHAN_RESERVATION")
            item = None
            if next_reservation is not None and next_reservation[0] == key:
                item = next_reservation[1]
                next_reservation = next(reservations, None)
            terminal = record.state in ("TERMINAL", "ABORTED_BEFORE_SEND")
            if record.state not in ("PREPARED", "SEND_UNKNOWN", "ACKED", "TERMINAL", "ABORTED_BEFORE_SEND"):
                raise ValueError("SPOT_JOURNAL_STATE_INVALID")
            if item is None:
                if record.state != "ABORTED_BEFORE_SEND":
                    raise ValueError("SPOT_JOURNAL_MISSING_RESERVATION")
            elif ((item.intent.intent_id, item.intent.session_id, item.intent.epoch)
                  != (key, record.session_id, record.epoch)
                  or record.slot_side is not None and record.slot_side != item.intent.side
                  or (item.state == "TERMINAL") != terminal):
                raise ValueError("SPOT_JOURNAL_IDENTITY_CONFLICT")
            if not terminal:
                if settled:
                    raise ValueError("SPOT_JOURNAL_PARTITION_INVALID")
                active.append(record)
        if next_reservation is not None:
            raise ValueError("SPOT_JOURNAL_ORPHAN_RESERVATION")
        return tuple(active)

    def matches(self, *, session_id=None, epoch=None, pending_intent_id=None):
        with self.wal._lock, self.reservations._lock:
            try:
                wal_rows, reservation_rows = self._verify()
                partitioned = isinstance(wal_rows, PartitionMap) and isinstance(reservation_rows, PartitionMap)
                left = wal_rows.settled if partitioned else wal_rows
                right = reservation_rows.settled if partitioned else reservation_rows
                cached = self._proof
                if (cached is None or not _same_cut(cached[0], left)
                        or not _same_cut(cached[1], right)):
                    active = self._coverage(left, right, settled=partitioned)
                    # Recheck after the cold scan before publishing a proof.
                    end_wal, end_reservations = self._verify()
                    if end_wal is not wal_rows or end_reservations is not reservation_rows:
                        raise ValueError("SPOT_JOURNAL_CHANGED_DURING_PROOF")
                    self._proof = (left, right, active)
                else:
                    active = cached[2]
                if partitioned:
                    active = self._coverage(wal_rows.active, reservation_rows.active)
                for record in active:
                    if record.state == "PREPARED" and record.intent_id != pending_intent_id:
                        return False
                    if session_id is not None and (
                            record.slot_market is None or (record.session_id, record.epoch) != (session_id, epoch)):
                        return False
                return True
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self._proof = None
                return False


def spot_journal_proof(wal, reservations):
    """Share one bounded proof slot between the installed planner and sender."""
    proof = getattr(wal, "_spot_reservation_proof", None)
    if not isinstance(proof, SpotJournalProof) or proof.wal is not wal or proof.reservations is not reservations:
        proof = SpotJournalProof(wal, reservations)
        wal._spot_reservation_proof = proof
    return proof
