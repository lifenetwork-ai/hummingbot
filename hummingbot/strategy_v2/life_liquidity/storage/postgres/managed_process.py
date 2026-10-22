"""Original offline service lease lifecycle; no successor or key takeover.

Registration is an explicit owner-side callback receiving only public fields.
The service owns the nondurable capability, heartbeat and a one-way local arm
latch. Neither lookup nor a restarted heartbeat can restore that latch.
"""
import asyncio
import inspect
import time

from pydantic import BaseModel, ConfigDict, model_validator

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease, ProcessCapability


class PgManagedProcessConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    claim_operation_id: str
    heartbeat_interval_ms: int

    @model_validator(mode='after')
    def _valid(self):
        try:
            identifier(self.claim_operation_id)
        except StorageError:
            raise ValueError('invalid claim operation') from None
        if not 0 < self.heartbeat_interval_ms <= 60000:
            raise ValueError('invalid heartbeat interval')
        return self


class PgManagedProcess:
    def __init__(self, service, config, register):
        if type(config) is not PgManagedProcessConfig or not callable(register):
            raise StorageError('PG_SERVICE_MANAGED_PROCESS_INVALID')
        self.service, self.config, self.register = service, config, register
        self._mode, self.task = 'UNARMED', None
        self._sequence = 0
        self.lease = PgProcessLease(service.runtime, ProcessCapability.create(service.runtime.config, service._process_owner))

    @property
    def mode(self):
        try:
            self.lease.capability._validate(self.service.runtime.config)
            if (self.service._managed_process is not self or self.service._process is not self
                    or self.lease.runtime is not self.service.runtime
                    or self.service.runtime._process_capability is not self.lease.capability
                    or self.lease.capability._owner != self.service._process_owner):
                raise StorageError('PG_SERVICE_PROCESS_CONTEXT_CHANGED')
        except StorageError:
            self.service.revoke()
        if self._mode != 'STOPPED' and not self.service.runtime.lease_valid:
            # Readiness is checked at each local request boundary, including
            # before the heartbeat task gets CPU after an event-loop pause.
            self.service.revoke()
        return self._mode

    async def start(self):
        service, cfg = self.service, self.service.runtime.config

        async def policy(c):
            return await c.fetchrow('SELECT lease_duration_ms FROM life_financial.process_lease_policies WHERE account_id=$1 AND policy_hash=$2',
                                    cfg.account_id, cfg.binding.policy_hash)
        imported = await service.runtime._admit(policy, safety=True)
        if imported is None or self.config.heartbeat_interval_ms * 2 >= imported['lease_duration_ms']:
            raise StorageError('PG_SERVICE_PROCESS_HEARTBEAT_POLICY_INVALID')
        async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
            result = self.register(self.lease.capability.registration)
            if not inspect.isawaitable(result):
                raise StorageError('PG_SERVICE_PROCESS_REGISTRATION_MUST_BE_ASYNC')
            await result
        receipt = await self.lease.claim(self.config.claim_operation_id)
        if receipt.replayed or not self.lease.ready:
            raise StorageError('PG_SERVICE_PROCESS_ORIGINAL_CLAIM_REQUIRED')
        self.task = asyncio.create_task(self._heartbeat())
        self.task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def _heartbeat(self):
        try:
            while self.mode != 'STOPPED':
                await asyncio.sleep(max(0, min(self.config.heartbeat_interval_ms / 1000,
                                               self.service.runtime._lease_deadline - time.monotonic())))
                if self._mode == 'STOPPED':
                    return
                if not self.service.runtime.lease_valid:
                    raise StorageError('PG_SERVICE_PROCESS_LEASE_LOST')
                self._sequence += 1
                operation = 'process-beat:' + fingerprint({'claim': self.config.claim_operation_id, 'sequence': self._sequence})
                receipt = await self.lease.heartbeat(operation)
                if receipt.replayed or not self.service.runtime.lease_valid:
                    raise StorageError('PG_SERVICE_PROCESS_ORIGINAL_HEARTBEAT_REQUIRED')
        except BaseException:
            # Includes cancellation and unknown COMMIT. No retry/refresh/rearm.
            self.service.revoke()

    async def arm(self):
        service = self.service
        if service._opening or service._revoked or self.mode != 'UNARMED':
            raise StorageError('PG_SERVICE_PROCESS_STATE_INVALID')
        cfg = service.runtime.config

        async def check(c):
            await c.execute('''SELECT life_financial.guard_account($1,$2,$3,a.version)
                FROM life_financial.accounts a WHERE a.account_id=$1''',
                            cfg.account_id, cfg.binding.json(), service._process_owner.json())
        try:
            await service.runtime._admit(check, safety=False, operation='process_service_arm')
            if service._revoked or self.mode != 'UNARMED' or service.config.manual_kill_switch:
                raise StorageError('PG_SERVICE_PROCESS_STATE_INVALID')
            self._mode = 'ACTIVE'
        except BaseException:
            service.revoke()
            raise

    def drain(self):
        if self.mode not in ('UNARMED', 'ACTIVE') or self.service._opening or self.service._revoked:
            raise StorageError('PG_SERVICE_PROCESS_STATE_INVALID')
        # Local admission closes synchronously, before an operator waits for
        # a signed native DRAIN. This does not claim that DB ownership moved or
        # that an already accepted network request has been excluded.
        self._mode = 'DRAINING'
        if self.service.makers is not None:
            self.service.makers.request_stop()

    async def recovery_only(self):
        service = self.service
        if self.mode != 'DRAINING' or service._revoked:
            raise StorageError('PG_SERVICE_PROCESS_STATE_INVALID')
        cfg = service.runtime.config

        async def check(c):
            row = await c.fetchrow('''SELECT a.mode,life_financial.guard_spot_observation($1,$2,$3,a.version)
                FROM life_financial.accounts a WHERE a.account_id=$1''',
                                   cfg.account_id, cfg.binding.json(), service._process_owner.json())
            if row is None or row['mode'] != 'RECOVERY_ONLY':
                raise StorageError('PG_SERVICE_NATIVE_RECOVERY_ONLY_REQUIRED')
        await service.runtime._admit(check, safety=True, operation='process_service_recovery')
        if service._revoked or self.mode != 'DRAINING':
            raise StorageError('PG_SERVICE_PROCESS_STATE_INVALID')
        self._mode = 'RECOVERY_ONLY'

    def stop(self):
        self._mode = 'STOPPED'
        self.lease.stop()
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()

    async def join(self):
        if self.task is not None:
            await asyncio.shield(asyncio.gather(self.task, return_exceptions=True))
