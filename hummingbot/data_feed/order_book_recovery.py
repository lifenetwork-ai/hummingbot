"""Leased, bounded public snapshots for quiet LIFE MM order books."""
import asyncio
import logging
import math
import time
from dataclasses import dataclass, field

from hummingbot.core.data_type.order_book_message import OrderBookMessageType


@dataclass
class BookRecovery:
    users: dict = field(default_factory=dict)
    next_attempt: float = 0
    failures: int = 0
    pending_uid: int = 0
    warned_at: float = float('-inf')


class OrderBookRecovery:
    MAX_REQUESTS = 2
    REQUEST_TIMEOUT = 3
    POLL_INTERVAL = .5

    def __init__(self, provider):
        self.provider = provider
        self.entries = {}
        self.jobs = {}
        self.inflight = set()
        self.task = None
        self.clock = time.monotonic
        self.logger = logging.getLogger(__name__)

    def acquire(self, connector, pair, consumer, max_age):
        if connector not in ('okx', 'okx_perpetual') or not math.isfinite(float(max_age)) or max_age <= 0:
            raise ValueError('Book recovery requires an OKX book and positive freshness budget')
        self.entries.setdefault((connector, pair), BookRecovery()).users[consumer] = float(max_age)
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    def release(self, consumer):
        for key, state in list(self.entries.items()):
            state.users.pop(consumer, None)
            if not state.users:
                del self.entries[key]
                job = self.jobs.pop(key, None)
                if job is not None:
                    job.cancel()
                    self.provider._retain_stopping_task(job)
        if not self.entries:
            self.stop()

    def stop(self):
        self.entries.clear()
        if self.task is not None:
            self.task.cancel()
            self.provider._retain_stopping_task(self.task)
            self.task = None

    def _book(self, key):
        connector = self.provider.get_connector_with_fallback(key[0])
        tracker = connector.order_book_tracker
        book = tracker.order_books[key[1]]
        uid = max(book.snapshot_uid, book.last_diff_uid)
        divisor = 1000 if key[0] == 'okx_perpetual' else 1
        age = self.provider.time() - uid / divisor
        return tracker, uid, age, divisor

    async def run(self):
        # Each run owns its jobs even when release/reacquire starts a successor.
        owned_jobs = set()
        try:
            while self.entries:
                for key, state in list(self.entries.items()):
                    try:
                        _, uid, age, _ = self._book(key)
                    except (KeyError, AttributeError):
                        continue  # Initial tracker setup has its own readiness fence.
                    if state.pending_uid:
                        if uid < state.pending_uid:
                            continue  # One queued snapshot per book, never an unbounded backlog.
                        state.pending_uid = 0
                    if (key in self.jobs or self.clock() < state.next_attempt
                            or 0 < uid and -1 <= age < min(state.users.values()) / 2):
                        continue
                    if len(self.inflight) >= self.MAX_REQUESTS:
                        break
                    state.next_attempt = self.clock() + min(5, min(state.users.values()) / 2)
                    job = asyncio.create_task(self.recover(key, state))
                    self.jobs[key] = job
                    owned_jobs.add(job)
                    self.inflight.add(job)

                    def finished(task, identity=key):
                        owned_jobs.discard(task)
                        self.inflight.discard(task)
                        if self.jobs.get(identity) is task:
                            self.jobs.pop(identity, None)

                    job.add_done_callback(finished)
                await asyncio.sleep(self.POLL_INTERVAL)
        finally:
            for job in owned_jobs:
                job.cancel()
            await asyncio.gather(*tuple(owned_jobs), return_exceptions=True)

    async def recover(self, key, state):
        maximum_age = min(state.users.values())
        try:
            tracker, _, _, divisor = self._book(key)
            snapshot = await asyncio.wait_for(tracker.data_source._order_book_snapshot(trading_pair=key[1]),
                                              timeout=self.REQUEST_TIMEOUT)
            if self.entries.get(key) is not state:
                return
            maximum_age = min(state.users.values())
            current_tracker, uid, _, _ = self._book(key)
            age = self.provider.time() - snapshot.update_id / divisor
            if (snapshot.type != OrderBookMessageType.SNAPSHOT or snapshot.trading_pair != key[1]
                    or snapshot.update_id <= 0 or not math.isfinite(age) or not -1 <= age <= maximum_age):
                raise ValueError('invalid_or_stale_snapshot')
            if tracker is not current_tracker or snapshot.update_id <= uid:
                # A WS update or tracker replacement won. Never roll its state back,
                # including spot updates sharing the same whole-second UID.
                return
            state.pending_uid = snapshot.update_id
            snapshot.content['_mm_recovery'] = True
            tracker._order_book_snapshot_stream.put_nowait(snapshot)
            if state.failures:
                self.logger.info('Book snapshot recovery resumed connector=%s pair=%s failures=%s', *key, state.failures)
            state.failures = 0
            state.next_attempt = self.clock() + min(5, maximum_age / 2)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self.entries.get(key) is not state:
                return
            state.failures += 1
            state.next_attempt = self.clock() + min(30, 2 ** min(state.failures, 5))
            if self.clock() - state.warned_at >= 60:
                state.warned_at = self.clock()
                self.logger.warning('Book snapshot recovery unavailable connector=%s pair=%s error_type=%s failures=%s',
                                    *key, type(error).__name__, state.failures)
