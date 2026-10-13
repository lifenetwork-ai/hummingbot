"""Bounded reporting application used by the actual V2 executor orchestrator."""
import asyncio
import json
import math
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection, FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class FileRecorderApplication:
    def __init__(self, repository, runtime, *, max_pending, shutdown_timeout_s):
        if (not isinstance(repository, FileRecorderRepository) or repository.store is not runtime.store
                or repository.binding != runtime.binding or repository.controller_id != runtime.controller.config.id
                or type(max_pending) is not int or not 0 < max_pending <= repository.store.limits.max_pending
                or type(shutdown_timeout_s) not in (int, float) or not math.isfinite(shutdown_timeout_s)
                or shutdown_timeout_s <= 0):
            raise StorageError('RECORDER_APPLICATION_BINDING_INVALID')
        self.repository, self.store, self.runtime = repository, repository.store, runtime
        self.binding = repository.binding
        self.max_pending, self.shutdown_timeout_s = max_pending, shutdown_timeout_s
        self._pending, self._failures = {}, []
        self._mutex = asyncio.Lock()
        self._closed, self._busy, self._healthy = False, False, False
        self._startup_infos = ()

    @classmethod
    async def install(cls, repository, runtime, *, max_pending, shutdown_timeout_s):
        app = cls(repository, runtime, max_pending=max_pending, shutdown_timeout_s=shutdown_timeout_s)
        await app.recover()
        runtime.bind_recorder_application(app)
        return app

    @property
    def ready(self):
        return self._healthy and not self._closed and not self._busy and not self._pending

    @property
    def startup_infos(self):
        return tuple(info.model_copy(deep=True) for info in self._startup_infos)

    async def recover(self):
        """Repair original reporting commands only; never construct/send an executor."""
        async with self._mutex:
            self._healthy = False
            for item in await self.repository.pending_projections(self.binding):
                await self.repository.project(item.command, item.original_cut, item.owner)
            for item in await self.repository.pending_progress(self.binding):
                await self.repository.update_progress(item.command, cut=item.original_cut, owner=item.owner)
            rows = await self.repository.read(self.binding)
            self._startup_infos = tuple(ExecutorInfo.model_validate_json(row.payload_json) for row in rows)
            # Position reporting requires its own bound port, never adoption of
            # unscoped legacy SQLite rows through the executor recovery path.
            await self.repository.assert_supported_positions(self.binding)
            self._healthy = True

    async def before_dispatch(self, action):
        if (not self.ready or not self.runtime._local_ready()
                or action.controller_id != self.repository.controller_id):
            return False
        self._busy = True
        try:
            async with self._mutex:
                config = action.executor_config.model_copy(deep=True)
                info = ExecutorInfo(id=config.id, timestamp=config.timestamp, type=config.type, config=config,
                                    controller_id=config.controller_id, status=RunnableStatus.NOT_STARTED,
                                    net_pnl_pct=Decimal('0'), net_pnl_quote=Decimal('0'), cum_fees_quote=Decimal('0'),
                                    filled_amount_quote=Decimal('0'), is_active=False, is_trading=False,
                                    custom_info={'recovery_account_uid': self.binding.account_uid, 'recovery_order_ids': []})
                command = ExecutorProjection.from_info('initial:' + fingerprint({'config_json': config.model_dump_json()}), self.binding, info)
                cut = (await self.store.read(self.binding)).cut
                await self.repository.project(command, cut, self.runtime.owner)
                return not self._closed and not self.runtime._revoked
        except BaseException:
            self._healthy = False
            self.runtime.revoke()
            raise
        finally:
            self._busy = False

    def fresh_stored_infos(self):
        """File adapter bridge for existing proof code, only on its owning worker."""
        self.store.assert_worker_thread()
        return tuple(ExecutorInfo.model_validate_json(row.payload_json) for row in self.repository._read()
                     if json.loads(row.payload_json)['status'] == RunnableStatus.TERMINATED.value)

    def enqueue(self, info, *, on_committed=None):
        if self._closed or not self._healthy:
            raise StorageError('RECORDER_APPLICATION_UNAVAILABLE')
        captured = ExecutorProjection.from_info('capture', self.binding, info)
        digest = fingerprint({'payload': captured.payload_json})
        key = (info.id, digest)
        previous = self._pending.get(key)
        if previous is not None:
            if on_committed is not None:
                previous[1].append(on_committed)
            return previous[0]
        if len(self._pending) >= self.max_pending:
            self._healthy = False
            self.runtime.revoke()
            raise StorageError('RECORDER_APPLICATION_BACKPRESSURE')
        callbacks = [] if on_committed is None else [on_committed]
        task = asyncio.create_task(self._update(captured, callbacks))
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

    async def _update(self, captured, callbacks):
        async with self._mutex:
            snapshot = await self.repository.progress_cut(captured.executor_id, self.binding)
            sequence = snapshot.sequence
            command = ExecutorProgress('progress:' + fingerprint({'id': captured.executor_id, 'sequence': sequence,
                                                                  'payload': captured.payload_json}),
                                       self.binding, sequence, captured.payload_json)
            receipt = await self.repository.update_progress(command, cut=snapshot.cut, owner=self.runtime.owner)
            for callback in tuple(callbacks):
                callback(receipt)
            return receipt

    async def drain(self):
        tasks = tuple(row[0] for row in self._pending.values())
        if tasks:
            await asyncio.gather(*tasks)
        if self._failures:
            raise StorageError('RECORDER_APPLICATION_REPAIR_REQUIRED') from self._failures[0]

    async def close(self):
        self._closed = True
        self.runtime.revoke()
        tasks = tuple(row[0] for row in self._pending.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout_s)
            for task in pending:
                task.cancel()
            if pending:
                raise StorageError('RECORDER_SHUTDOWN_READBACK_REQUIRED')
        if self._failures:
            raise StorageError('RECORDER_APPLICATION_REPAIR_REQUIRED') from self._failures[0]
