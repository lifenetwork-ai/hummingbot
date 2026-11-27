"""A scoped executor-history read that can overlap a managed strategy handoff."""
import asyncio
from contextvars import copy_context
from threading import Event


class StartupHistoryRead:
    def __init__(self, recorder, identities):
        self.recorder = recorder
        self.identities = tuple(identities)
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        cancelled = Event()

        def read_history():
            # The cold executor import loads several native modules before any history I/O.
            from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
            return ExecutorOrchestrator.read_startup_performance(
                self.recorder, self.identities, cancelled=cancelled,
                compact=getattr(self.recorder, 'durable_position_checkpoints', False) is True)

        job = asyncio.get_running_loop().run_in_executor(None, copy_context().run, read_history)
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            # Drain the worker during Stop/SIGTERM, including any open history cursor.
            cancelled.set()
            while not job.done():
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if job.done() and not job.cancelled():
                job.exception()
            raise
