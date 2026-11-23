"""One owner-loop slot with bounded preference for ready work."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from typing import Callable, Optional

MAX_READY_SENDS = 4


@dataclass
class QueuedIntent:
    connector: object
    order: object
    context: Context = field(default_factory=copy_context)
    claimed: bool = False
    cancelled: bool = False
    on_claim: Optional[Callable[[], None]] = None


class SubmissionAdmission:
    """FIFO within each class, with bounded priority at the recorder.

    Receipts precede ordinary writes. New intents can wait behind ready sends
    and other ordinary writes without reordering those writes.
    """

    def __init__(self):
        self._locked = False
        self._admissions, self._sends, self._intent_writes = deque(), deque(), deque()
        self._intents = {}
        self._send_streak = 0
        self._write_streak = 0

    def locked(self):
        return self._locked

    async def acquire(self, *, final=False, intent=None, intent_write=False):
        queue = self._sends if final else self._intent_writes if intent_write else self._admissions
        if not self._locked and not (self._admissions or self._sends or self._intent_writes):
            self._locked = True
            self._count_grant(queue)
            return True
        future = asyncio.get_running_loop().create_future()
        if intent is not None:
            self._intents[future] = intent
        queue.append(future)
        if not self._locked:
            self._wake_next()
        try:
            return await future if intent is None else await asyncio.shield(future)
        except BaseException:
            if intent is not None and intent.claimed:
                intent.cancelled = True
                # A peer with a transaction in flight stays in the original
                # submit task until the recorder knows the transaction outcome.
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
            self._intents.pop(future, None)

    def take_intents(self, maximum):
        """Collect consecutive native waiters while the leader owns the slot."""
        peers = []
        while self._admissions and len(peers) < maximum:
            future = self._admissions[0]
            if future.done():
                self._admissions.popleft()
                continue
            intent = self._intents.get(future)
            if intent is None:
                break
            self._admissions.popleft()
            intent.claimed = True
            if intent.on_claim is not None:
                intent.on_claim()
            peers.append((intent, future))
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
        self._locked = False
        self._wake_next()

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, *_):
        self.release()

    @asynccontextmanager
    async def final_send(self):
        await self.acquire(final=True)
        try:
            yield
        finally:
            self.release()

    @asynccontextmanager
    async def intent_write(self):
        await self.acquire(intent_write=True)
        try:
            yield
        finally:
            self.release()
