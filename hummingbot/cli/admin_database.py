"""Bound Admin DB work and reserve one slot for operations, one for heavy reads.

Only one operations job and one heavy job are submitted to the existing
two-thread executor at a time. A client
cancellation never frees capacity while its physical DB call is still running.
"""
import asyncio
import heapq
import itertools
from concurrent.futures import ThreadPoolExecutor


class DatabaseBusy(RuntimeError):
    """Safe backpressure: callers may retry with their existing request identity."""


class AdminDatabaseExecutor:
    # Keep the existing ten-retry control burst admissible alongside ten readers.
    MAX_PENDING = 32
    MAX_REPORTS = 12
    # Reserve non-control admission even when history/report queues are full.
    MAX_HEAVY = MAX_REPORTS - 2
    WORKERS = 2

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=self.WORKERS, thread_name_prefix='admin-db')
        self.queue = []
        self.sequence = itertools.count()
        self.active = self.pending = self.reports = 0
        self.active_heavy = 0
        self.heavy_pending = 0
        self.closed = False

    def submit(self, call, *, control=False, heavy=False):
        if self.closed:
            raise RuntimeError('Admin database executor is closed')
        if control and heavy:
            raise ValueError('Controls must use the operations lane')
        if (self.pending >= self.MAX_PENDING or (not control and self.reports >= self.MAX_REPORTS)
                or (heavy and self.heavy_pending >= self.MAX_HEAVY)):
            raise DatabaseBusy('Admin database capacity is busy')
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        self.pending += 1
        self.reports += not control
        self.heavy_pending += heavy
        heapq.heappush(self.queue, (0 if control else 2 if heavy else 1,
                                    next(self.sequence), result, call, control, heavy))
        self.dispatch(loop)
        return result

    def release(self, control, heavy):
        self.pending -= 1
        self.reports -= not control
        self.heavy_pending -= heavy

    def dispatch(self, loop):
        while not self.closed and self.active < self.WORKERS and self.queue:
            # At most 32 pending entries. Skip a busy lane without blocking the
            # other lane; controls still overtake queued operational reads.
            # Heavy FIFO progress is independent of an operations/control burst.
            deferred = []
            selected = None
            while self.queue:
                entry = heapq.heappop(self.queue)
                _, _, result, call, control, heavy = entry
                if result.cancelled():
                    self.release(control, heavy)
                    continue
                busy = (self.active_heavy >= 1 if heavy else
                        self.active - self.active_heavy >= max(1, self.WORKERS - 1))
                if busy:
                    deferred.append(entry)
                    continue
                selected = entry
                break
            for entry in deferred:
                heapq.heappush(self.queue, entry)
            if selected is None:
                break
            _, _, result, call, control, heavy = selected
            try:
                job = self.executor.submit(call)
            except Exception as error:
                self.release(control, heavy)
                result.set_exception(error)
                continue
            self.active += 1
            self.active_heavy += heavy
            job.add_done_callback(lambda finished, result=result, control=control, heavy=heavy:
                                  loop.call_soon_threadsafe(self.complete, loop, result, control, heavy, finished))

    def complete(self, loop, result, control, heavy, job):
        self.active -= 1
        self.active_heavy -= heavy
        self.release(control, heavy)
        if not result.done():
            if job.cancelled():
                result.cancel()
            elif job.exception() is not None:
                result.set_exception(job.exception())
            else:
                result.set_result(job.result())
        self.dispatch(loop)

    async def close(self):
        self.closed = True
        while self.queue:
            _, _, result, _, control, heavy = heapq.heappop(self.queue)
            result.cancel()
            self.release(control, heavy)
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
