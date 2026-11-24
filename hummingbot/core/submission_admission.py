"""One owner-loop slot with bounded preference for ready work."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from typing import Callable, Optional

from hummingbot.core.engine_diagnostics import RecorderWriteTiming

MAX_READY_SENDS = 4


@dataclass(kw_only=True)
class QueuedWrite:
    context: Context = field(default_factory=copy_context)
    claimed: bool = False
    cancelled: bool = False
    on_claim: Optional[Callable[[], None]] = None


@dataclass
class QueuedIntent(QueuedWrite):
    connector: object
    order: object


class SubmissionAdmission:
    """FIFO grants within each class, with bounded priority at the recorder.

    Received venue evidence gets bounded priority over ordinary writes.
    New intents can wait behind ready sends and other ordinary writes without
    reordering those writes.
    """

    def __init__(self, *, track_writes=False):
        self._locked = False
        self._admissions, self._sends, self._intent_writes = deque(), deque(), deque()
        self._queued_writes = {}
        self._receipt_waiters = set()
        self._send_streak = 0
        self._write_streak = 0
        self._write_timing = RecorderWriteTiming() if track_writes else None

    def locked(self):
        return self._locked

    async def acquire(self, *, final=False, work=None, intent_write=False, operation='other_write', receipt=False):
        queue = self._sends if final else self._intent_writes if intent_write else self._admissions
        if not self._locked and not (self._admissions or self._sends or self._intent_writes):
            self._locked = True
            self._count_grant(queue)
            if self._write_timing is not None:
                self._write_timing.acquired(operation)
            return True
        future = asyncio.get_running_loop().create_future()
        if work is not None:
            self._queued_writes[future] = work
        if receipt:
            self._receipt_waiters.add(future)
        queue.append(future)
        if not self._locked:
            self._wake_next()
        try:
            granted = await future if work is None else await asyncio.shield(future)
            if granted is True and self._write_timing is not None:
                self._write_timing.acquired(operation)
            return granted
        except BaseException:
            if work is not None and work.claimed:
                work.cancelled = True
                # The original caller must wait for a claimed transaction.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
            elif future.done() and not future.cancelled() and future.result() is True:
                # A grant reserves the slot before the task resumes. Hand it on
                # if that task is canceled at exactly this boundary.
                self.release()
            else:
                future.cancel()
                try:
                    queue.remove(future)
                except ValueError:
                    pass
                if not self._locked:
                    self._wake_next()
            raise
        finally:
            self._queued_writes.pop(future, None)
            if receipt:
                self._receipt_waiters.discard(future)

    def take_work(self, maximum, compatible=None, *, final=False, across_receipt=False):
        """Collect ready peers in order, optionally across one receipt writer."""
        queue = self._sends if final else self._admissions
        peers = []
        receipt = None
        try:
            while queue and len(peers) < maximum:
                future = queue[0]
                if future.done():
                    queue.popleft()
                    continue
                if across_receipt and receipt is None and future in self._receipt_waiters:
                    receipt = queue.popleft()
                    continue
                work = self._queued_writes.get(future)
                if work is None or compatible is not None and not compatible(work):
                    break
                queue.popleft()
                work.claimed = True
                if work.on_claim is not None:
                    work.on_claim()
                peers.append((work, future))
        finally:
            if receipt is not None:
                queue.appendleft(receipt)
        return peers

    def _wake_next(self):
        for queue in (self._admissions, self._sends, self._intent_writes):
            while queue and queue[0].done():
                queue.popleft()
        if self._sends and (not (self._admissions or self._intent_writes) or self._send_streak < MAX_READY_SENDS):
            queue = self._sends
        elif self._admissions and (not self._intent_writes or self._write_streak < MAX_READY_SENDS):
            queue = self._admissions
        elif self._intent_writes:
            queue = self._intent_writes
        else:
            self._send_streak = self._write_streak = 0
            return
        self._count_grant(queue)
        self._locked = True
        queue.popleft().set_result(True)

    def _count_grant(self, queue):
        self._send_streak = min(MAX_READY_SENDS, self._send_streak + 1) if queue is self._sends else 0
        if queue is self._admissions:
            self._write_streak = min(MAX_READY_SENDS, self._write_streak + 1)
        elif queue is self._intent_writes:
            self._write_streak = 0

    def release(self):
        if not self._locked:
            raise RuntimeError('Submission admission slot is not acquired')
        if self._write_timing is not None:
            self._write_timing.released()
        self._locked = False
        self._wake_next()

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, *_):
        self.release()

    def final_send(self, *, operation='other_write'):
        return self.write(operation, final=True)

    def receipt_write(self):
        return self.write('transport_receipt', final=True, receipt=True)

    def intent_write(self):
        return self.write('intent', intent_write=True)

    @asynccontextmanager
    async def write(self, operation, *, final=False, intent_write=False, observe_wait=False, receipt=False):
        timing = self._write_timing if observe_wait else None
        wait = timing.begin_wait() if timing is not None else None
        await self.acquire(final=final, intent_write=intent_write, operation=operation, receipt=receipt)
        try:
            if wait is not None:
                timing.report_wait(wait)
            yield
        finally:
            self.release()
