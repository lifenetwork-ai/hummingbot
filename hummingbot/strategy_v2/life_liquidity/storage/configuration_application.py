"""Bounded operational reload with durable audit and explicit reactivation."""
import asyncio
import json
from uuid import uuid4

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageConflict, StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport, service_policy
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig


class FileConfigurationApplication:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.repository = service.reporting.repository
        self._policy_hash = fingerprint(service_policy(service.config))
        self._pending, self._failures = set(), []
        self._mutex = asyncio.Lock()
        self._closed, self._healthy = False, False
        self._poisoned = False
        self.state = None

    @classmethod
    async def install(cls, service, create_binding):
        app = cls(service)
        service.configuration = app
        await app.store._call(app._install, create_binding)
        return app

    def _install(self, create_binding):
        head = self.repository._configuration()
        if head.sequence == 0:
            imported = 'import_origin' in self.store._read().resources
            if not create_binding and not imported:
                raise StorageError('CONFIG_APPROVED_IMPORT_REQUIRED')
            c = self.service.controller.config
            operation = 'startup-config' if not imported else 'import-config:' + self.store._read().resources['import_origin']['sha256'][:32]
            command = ConfigurationReport(operation, self.store.binding, c.id, 0, 1, c.model_dump_json(),
                                          self._policy_hash, fingerprint({'service_json': self.service.config.model_dump_json()}),
                                          'INITIAL', 'CONFIG_STARTUP', int(utc(self.store._clock()).timestamp() * 1000))
            self.repository._publish_current('configuration', command, self.service.spot.owner)
        _, history = self.repository._configuration_history()
        if history[-1][0].policy_hash != self._policy_hash:
            raise StorageError('CONFIG_SERVICE_POLICY_MISMATCH')
        # A newly requested startup kill must never be silently replaced by an
        # older false flag. A stale false flag cannot clear a recorded kill.
        if self.service.controller.config.manual_kill_switch and not json.loads(history[-1][0].controller_json)['manual_kill_switch']:
            self.service.revoke()
            candidate = self.service.config
            self._publish(candidate, fingerprint({'service_json': candidate.model_dump_json()}), None,
                          'startup-kill:' + uuid4().hex)
        self.refresh()

    def refresh(self):
        self.store.assert_worker_thread()
        self.state = self.repository._configuration()
        from controllers.generic.life_liquidity import LifeLiquidityConfig
        config = LifeLiquidityConfig.model_validate_json(self.state.controller_json)
        self.service.controller.config = config
        self.service.config = self.service.config.model_copy(update={'controller': config.model_copy(deep=True)})
        self.service.controller.config_update_state._rejected_update = self.state.rejection_latched or self._poisoned
        if self.state.kill_latched:
            self.service.revoke()
        self._healthy = not self._poisoned

    @property
    def ready(self):
        return (self._healthy and not self._closed and not self._pending and self.state is not None
                and not self.state.rejection_latched and not self.state.kill_latched
                and self.service.controller.config.model_dump_json() == self.state.controller_json)

    def verify(self, *, activation=False):
        """Fresh SQL/checkpoint audit on the sole worker; no cached SQL authority."""
        self.store.assert_worker_thread()
        state = self.repository._configuration()
        if (not self._healthy or self._closed or self._pending or state.kill_latched
                or state.controller_json != self.service.controller.config.model_dump_json()
                or state.last_outcome == 'REJECTED' or state.rejection_latched and not activation):
            raise StorageError('CONFIG_REACTIVATION_REQUIRED')
        return state

    def verify_activation(self):
        return self.verify(activation=True)

    def _reject(self, reason):
        self.service.controller.config_update_state.reject(reason)
        self.service.runtime.invalidate_actions()
        self.service.controller._runner_halt_ok = self.service.controller._halt_runner_orders()

    def submit(self, candidate, *, operation_id):
        self._admit(operation_id)
        reason = None
        digest = fingerprint({'invalid': 'CONFIG_VALIDATION_FAILED'})
        try:
            if type(candidate) is _LoadFailure:
                candidate, reason = None, 'CONFIG_LOAD_FAILED'
                digest = fingerprint({'invalid': reason})
            elif type(candidate) is not FileServiceConfig:
                raise ValueError()
            else:
                captured = candidate.model_dump_json()
                digest = fingerprint({'service_json': captured})
                if len(captured.encode()) > 65536:
                    raise ValueError()
                candidate = FileServiceConfig.model_validate_json(captured)
                digest = fingerprint({'service_json': candidate.model_dump_json()})
                if fingerprint(service_policy(candidate)) != self._policy_hash:
                    reason = 'CONFIG_UPDATE_UNSUPPORTED'
        except Exception:
            candidate, reason = None, 'CONFIG_VALIDATION_FAILED'
            # Invalid objects are never stringified into logs/checkpoints.
        if reason is not None:
            self._reject(reason)
        return self._own(self._apply(candidate, digest, reason, operation_id))

    def _admit(self, operation_id):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import identifier
        identifier(operation_id)
        if len(operation_id) > 96 or self._closed or len(self._pending) >= self.service.config.max_pending:
            self._reject('CONFIG_LOAD_FAILED')
            self._healthy = False
            self._poisoned = True  # An unrecorded rejection cannot be cleared by another reload.
            raise StorageError('CONFIG_RELOAD_CLOSED_OR_BACKPRESSURE')
        self.service.runtime.invalidate_actions()

    def _own(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._pending.add(task)

        def done(result):
            self._pending.discard(result)
            if result.cancelled() or result.exception() is not None:
                self._healthy = False
                self._poisoned = True
                self.service.runtime.revoke()
                if not result.cancelled():
                    self._failures.append(result.exception())
        task.add_done_callback(done)
        # Caller cancellation does not cancel an admitted physical publication.
        result = asyncio.shield(task)
        # Native ControllerBase callers do not await update_config's result.
        # Observe both handles while retaining normal await/exception behavior.
        result.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
        return result

    def submit_yaml(self, path, *, operation_id):
        self._admit(operation_id)
        return self._own(self._load_yaml(path, operation_id))

    async def _load_yaml(self, path, operation_id):
        try:
            candidate = await self.service.read_yaml(path)
        except Exception:
            self._reject('CONFIG_LOAD_FAILED')
            return await self._apply(None, fingerprint({'invalid': 'CONFIG_LOAD_FAILED'}), 'CONFIG_LOAD_FAILED', operation_id)
        reason = None if fingerprint(service_policy(candidate)) == self._policy_hash else 'CONFIG_UPDATE_UNSUPPORTED'
        if reason:
            self._reject(reason)
        return await self._apply(candidate, fingerprint({'service_json': candidate.model_dump_json()}), reason, operation_id)

    def submit_load_failure(self, *, operation_id=None):
        self._reject('CONFIG_LOAD_FAILED')
        # Reuse capacity/cancellation ownership; the reason is selected below.
        return self.submit(_LoadFailure(), operation_id=operation_id or 'config-load:' + uuid4().hex)

    async def _apply(self, candidate, digest, reason, operation_id):
        async with self._mutex:
            control = self.service.control
            if control is None:
                return await self._serialized(candidate, digest, reason, operation_id)
            async with control._lock:
                return await self._serialized(candidate, digest, reason, operation_id)

    async def _serialized(self, candidate, digest, reason, operation_id):
        if self._closed:
            raise StorageError('CONFIG_RELOAD_CLOSED')
        # Kill is an immediate local action, then a coalesced durable risk HALT.
        if candidate is not None and reason is None and candidate.controller.manual_kill_switch:
            if self.service.risk is not None:
                self.service.request_halt()
                await asyncio.shield(self.service._halt_task)
            else:
                self.service.revoke()
        await self.service.reporting.drain()
        receipt = await self.store._call(self._publish, candidate, digest, reason, operation_id)
        # The durable report can finish after stop; it grants no volatile resume.
        if not self._closed:
            await self.store._call(self.refresh)
        return receipt

    def _publish(self, candidate, digest, reason, operation_id):
        _, history = self.repository._configuration_history()
        previous = next((c for c, _, _, _ in history if c.operation_id == operation_id), None)
        if previous is not None:
            if previous.candidate_digest != digest:
                raise StorageConflict('CONFIG_OPERATION_CONFLICT')
            # Exact replay never reapplies an older controller snapshot.
            return self.repository._publish_current('configuration', previous,
                                                    next(owner for c, _, _, owner in history if c == previous))
        head = self.repository._configuration()
        changed = candidate is not None and candidate.controller.model_dump_json() != head.controller_json
        outcome = 'REJECTED' if reason else 'APPLIED' if changed else 'UNCHANGED'
        reason = reason or ('CONFIG_UPDATE_APPLIED' if changed else 'CONFIG_UNCHANGED')
        command = ConfigurationReport(operation_id, self.store.binding, self.repository.controller_id, head.sequence,
                                      head.active_revision + (outcome == 'APPLIED'),
                                      candidate.controller.model_dump_json() if outcome == 'APPLIED' else head.controller_json,
                                      self._policy_hash, digest, outcome, reason,
                                      int(utc(self.store._clock()).timestamp() * 1000))
        return self.repository._publish_current('configuration', command, self.service.runtime.owner)

    async def close(self):
        self._closed = True
        tasks = tuple(self._pending)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.service.config.shutdown_timeout_s)
            if pending:
                raise StorageError('CONFIG_SHUTDOWN_READBACK_REQUIRED')
        if self._failures:
            raise StorageError('CONFIG_REPAIR_REQUIRED') from self._failures[0]


class _LoadFailure:
    pass
