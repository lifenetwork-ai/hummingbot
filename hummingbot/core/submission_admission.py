"""One owner-loop slot with bounded preference for ready final-send checks."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager


class SubmissionAdmission:
    """FIFO per class; two final sends at most before a waiting new admission.

    The slot covers synchronous owner/intent checks, never network I/O. Waiters
    inherit their caller's context and stay owned by the service event loop.
    """

    def __init__(self):
        self._locked = False
        self._admissions, self._sends = deque(), deque()
        self._send_streak = 0

    def locked(self):
        return self._locked

    async def acquire(self, *, final=False):
        if not self._locked and not self._admissions and not self._sends:
            self._locked = True
            self._send_streak = min(2, self._send_streak + 1) if final else 0
            return True
        queue = self._sends if final else self._admissions
        future = asyncio.get_running_loop().create_future()
        queue.append(future)
        if not self._locked:
            self._wake_next()
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
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
        return True

    def _wake_next(self):
        for queue in (self._admissions, self._sends):
            while queue and queue[0].done():
                queue.popleft()
        if self._sends and (not self._admissions or self._send_streak < 2):
            future = self._sends.popleft()
            self._send_streak = min(2, self._send_streak + 1)
        elif self._admissions:
            future = self._admissions.popleft()
            self._send_streak = 0
        else:
            self._send_streak = 0
            return
        self._locked = True
        future.set_result(True)

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
