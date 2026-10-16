"""Main-loop bounded report capture; backend publication stays in subclasses."""
import asyncio

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport


class ReportingQueue:
    @property
    def ready(self):
        return self._healthy and not self._closed and not self._busy and not self._pending

    def invalidate(self):
        self._healthy = False
        self.runtime.revoke()

    @property
    def startup_infos(self):
        return tuple(info.model_copy(deep=True) for info in self._startup_infos)

    @property
    def startup_positions(self):
        return self._startup_positions  # Frozen snapshots; never mutable ORM objects.

    def enqueue(self, info, *, on_committed=None):
        try:
            captured = ExecutorProjection.from_info('capture', self.binding, info)
        except Exception:
            self.invalidate()
            raise
        return self._enqueue(('executor', captured.executor_id, fingerprint({'payload': captured.payload_json})),
                             captured, self._update, on_committed)

    def enqueue_positions(self, positions, timestamp_ms, *, on_committed=None):
        try:
            captured = PositionReport('capture', self.binding, self.repository.controller_id, 0, timestamp_ms, tuple(positions))
        except Exception:
            self.invalidate()
            raise
        return self._enqueue(('positions', fingerprint(captured.json())), captured, self._update_positions, on_committed)

    def _enqueue(self, key, captured, update, on_committed):
        if self._closed or not self._healthy:
            raise StorageError('RECORDER_APPLICATION_UNAVAILABLE')
        previous = self._pending.get(key)
        if previous is not None:
            if previous[0].done():
                # Completion may precede the scheduled done callback. Do not
                # append a notification to a callback list that already ran.
                receipt = previous[0].result()
                if on_committed is not None:
                    on_committed(receipt)
            elif on_committed is not None:
                if len(previous[1]) >= self.max_callbacks:
                    self.invalidate()
                    raise StorageError('RECORDER_CALLBACK_BACKPRESSURE')
                previous[1].append(on_committed)
            return previous[0]
        if len(self._pending) >= self.max_pending:
            self._healthy = False
            self.runtime.revoke()
            raise StorageError('RECORDER_APPLICATION_BACKPRESSURE')
        callbacks = [] if on_committed is None else [on_committed]
        task = asyncio.create_task(update(captured, callbacks))
        self._pending[key] = (task, callbacks)

        def completed(result):
            self._pending.pop(key, None)
            if result.cancelled():
                self._healthy = False
            elif result.exception() is not None:
                self._failures.append(result.exception())
                self._healthy = False
            if not self._healthy:
                self.runtime.revoke()
        task.add_done_callback(completed)
        return task

    async def drain(self):
        tasks = tuple(row[0] for row in self._pending.values())
        if tasks:
            await asyncio.gather(*tasks)
        if self._failures:
            raise StorageError('RECORDER_APPLICATION_REPAIR_REQUIRED') from self._failures[0]

    async def close(self):
        self._closed = True
        self.runtime.revoke()
        if self._event_application is not None:
            self._event_application.stop()
        tasks = tuple(row[0] for row in self._pending.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout_s)
            for task in pending:
                task.cancel()
            if pending:
                raise StorageError('RECORDER_SHUTDOWN_READBACK_REQUIRED')
        if self._failures:
            raise StorageError('RECORDER_APPLICATION_REPAIR_REQUIRED') from self._failures[0]
