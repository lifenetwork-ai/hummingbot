"""Continuity state for OKX public books WebSocket messages."""

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class BookFeedHealth:
    connected: bool = False
    synchronized: bool = False
    epoch: int = 0
    sequence_id: int | None = None
    snapshot_sequence_id: int | None = None
    snapshot_exchange_timestamp_ms: int | None = None
    snapshot_received_monotonic: float | None = None
    last_message_monotonic: float | None = None
    reason_code: str = "BOOK_FEED_DISCONNECTED"


class OkxBookHealthTracker:
    def __init__(self, trading_pairs: list[str], clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._states = {pair: BookFeedHealth() for pair in trading_pairs}

    def add_pair(self, pair: str):
        self._states.setdefault(pair, BookFeedHealth())

    def status(self, pair: str) -> BookFeedHealth:
        return self._states.get(pair, BookFeedHealth(reason_code="BOOK_FEED_UNAVAILABLE"))

    def on_connect(self):
        for pair, state in self._states.items():
            self._states[pair] = BookFeedHealth(connected=True, epoch=state.epoch + 1,
                                                reason_code="BOOK_SNAPSHOT_REQUIRED")

    def on_disconnect(self):
        for pair, state in self._states.items():
            self._states[pair] = BookFeedHealth(epoch=state.epoch + 1)

    def _invalidate(self, pair: str, reason: str):
        state = self._states[pair]
        self._states[pair] = BookFeedHealth(connected=state.connected, epoch=state.epoch + 1,
                                            reason_code=reason)

    def on_message(self, pair: str, action: str, data: dict) -> bool:
        state = self.status(pair)
        if not state.connected or pair not in self._states:
            return False
        seq, prev = data.get("seqId"), data.get("prevSeqId")
        if (type(seq) is not int or seq < 0 or type(prev) is not int
                or prev < -1 or not isinstance(data.get("bids"), list)
                or not isinstance(data.get("asks"), list)):
            self._invalidate(pair, "BOOK_SEQUENCE_INVALID")
            return False
        now = self.clock()
        if action == "snapshot":
            try:
                timestamp = int(data["ts"])
            except (KeyError, TypeError, ValueError):
                timestamp = 0
            if prev != -1 or timestamp <= 0:
                self._invalidate(pair, "BOOK_SEQUENCE_INVALID")
                return False
            self._states[pair] = BookFeedHealth(
                connected=True, synchronized=True, epoch=state.epoch + 1,
                sequence_id=seq, snapshot_exchange_timestamp_ms=timestamp,
                snapshot_sequence_id=seq,
                snapshot_received_monotonic=now, last_message_monotonic=now,
                reason_code="BOOK_FEED_SYNCHRONIZED",
            )
            return True
        if action != "update" or not state.synchronized:
            self._invalidate(pair, "BOOK_SNAPSHOT_REQUIRED")
            return False
        if prev != state.sequence_id:
            self._invalidate(pair, "BOOK_SEQUENCE_GAP")
            return False
        # Empty book updates are OKX heartbeats. A decreasing seqId can be a
        # documented sequence reset; prevSeqId still proves continuity.
        if seq == prev and (data.get("bids") or data.get("asks")):
            self._invalidate(pair, "BOOK_SEQUENCE_INVALID")
            return False
        self._states[pair] = BookFeedHealth(
            connected=True, synchronized=True, epoch=state.epoch,
            sequence_id=seq,
            snapshot_sequence_id=state.snapshot_sequence_id,
            snapshot_exchange_timestamp_ms=state.snapshot_exchange_timestamp_ms,
            snapshot_received_monotonic=state.snapshot_received_monotonic,
            last_message_monotonic=now, reason_code="BOOK_FEED_SYNCHRONIZED",
        )
        return True
