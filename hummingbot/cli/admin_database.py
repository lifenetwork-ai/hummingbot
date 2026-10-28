"""Bound pending Admin DB work, reserving admission and dispatch for controls.

Only two jobs are submitted to the standard thread executor at a time. A client
cancellation never frees capacity while its physical DB call is still running.
"""
import asyncio
import heapq
import itertools
from concurrent.futures import ThreadPoolExecutor


class DatabaseBusy(RuntimeError):
    """Safe backpressure: callers may retry with their existing request identity."""


class AdminDatabaseExecutor:
    MAX_PENDING = 16
    MAX_REPORTS = 12
    WORKERS = 2

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=self.WORKERS, thread_name_prefix='admin-db')
        self.queue = []
        self.sequence = itertools.count()
        self.active = self.pending = self.reports = 0
        self.closed = False

    def submit(self, call, *, control=False):
        if self.closed:
            raise RuntimeError('Admin database executor is closed')
        if self.pending >= self.MAX_PENDING or (not control and self.reports >= self.MAX_REPORTS):
            raise DatabaseBusy('Admin database capacity is busy')
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        self.pending += 1
        self.reports += not control
        heapq.heappush(self.queue, (0 if control else 1, next(self.sequence), result, call, control))
        self.dispatch(loop)
        return result

    def release(self, control):
        self.pending -= 1
        self.reports -= not control

    def dispatch(self, loop):
        while not self.closed and self.active < self.WORKERS and self.queue:
            _, _, result, call, control = heapq.heappop(self.queue)
            if result.cancelled():
                self.release(control)
                continue
            try:
                job = self.executor.submit(call)
            except Exception as error:
                self.release(control)
                result.set_exception(error)
                continue
            self.active += 1
            job.add_done_callback(lambda finished, result=result, control=control:
                                  loop.call_soon_threadsafe(self.complete, loop, result, control, finished))

    def complete(self, loop, result, control, job):
        self.active -= 1
        self.release(control)
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
            _, _, result, _, control = heapq.heappop(self.queue)
            result.cancel()
            self.release(control)
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
