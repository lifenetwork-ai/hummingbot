"""Awaited PostgreSQL recorder application for explicit V2 assemblies.

Reporting readiness is not account, planner, ownership or exchange send authority.
No connector cache is restored from these diagnostic projection records.
"""
import asyncio
import inspect
import json
import math
from dataclasses import asdict
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.configuration import PgConfigurationRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.positions import PgPositionRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection, EventSource
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress
from hummingbot.strategy_v2.life_liquidity.storage.reporting_queue import ReportingQueue
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class PgRecorderApplication(ReportingQueue):
    def __init__(self, financial, *, controller, owner, sources, revoke, local_ready, max_pending,
                 max_callbacks, shutdown_timeout_s, max_rows, max_read_bytes):
        from controllers.generic.life_liquidity import LifeLiquidityConfig
        config = getattr(controller, 'config', None)
        if (type(financial) is not PgSpotRepository or type(owner) is not OwnerToken or type(config) is not LifeLiquidityConfig
                or config.storage_backend != 'postgres' or config.storage_binding != financial.binding
                or not financial._runtime.config.write or financial._runtime.config.safety_size < 2
                or type(max_pending) is not int or not 0 < max_pending <= financial._runtime.config.max_pending
                or type(max_callbacks) is not int or not 0 < max_callbacks <= 1024
                or type(shutdown_timeout_s) not in (int, float) or not math.isfinite(shutdown_timeout_s) or shutdown_timeout_s <= 0
                or not callable(revoke) or inspect.iscoroutinefunction(revoke)
                or not callable(local_ready) or inspect.iscoroutinefunction(local_ready)
                or type(sources) is not tuple or not sources or len(sources) > 2
                or any(type(s) is not EventSource or s.controller_id != config.id for s in sources)
                or len({s.market for s in sources}) != len(sources)):
            raise StorageError('PG_RECORDER_APPLICATION_CONFIG_INVALID')
        self.financial, self.controller, self.owner = financial, controller, owner
        self.runtime = self  # Actual orchestrator checks this exact controller identity.
        self.binding = financial.binding
        self._revoke_callback, self._local_ready = revoke, local_ready
        self._revoked = False
        self.repository = PgRecorderRepository(financial, controller_id=config.id, max_rows=max_rows, max_read_bytes=max_read_bytes)
        self.configurations = PgConfigurationRepository(financial, controller_id=config.id, max_rows=max_rows, max_read_bytes=max_read_bytes)
        self.position_repository = PgPositionRepository(financial, controller_id=config.id, max_rows=max_rows, max_read_bytes=max_read_bytes)
        self.events = tuple(PgEventRepository(financial, source=s, max_rows=max_rows, max_read_bytes=max_read_bytes) for s in sources)
        self.max_pending, self.max_callbacks, self.shutdown_timeout_s = max_pending, max_callbacks, shutdown_timeout_s
        # All reporters sharing one qualified account runtime serialize their
        # stage/apply pairs. One safety pool slot remains for financial facts.
        if not hasattr(financial._runtime, '_recorder_mutex'):
            financial._runtime._recorder_mutex = asyncio.Lock()
        self._mutex = financial._runtime._recorder_mutex
        self._pending, self._failures = {}, []
        self._closed, self._busy, self._healthy = False, False, False
        self._startup_infos, self._startup_positions = (), ()
        self._event_application = None
        self._runner_context = None

    @classmethod
    async def install(cls, financial, **kwargs):
        app = cls(financial, **kwargs)
        try:
            await app.recover()
        except BaseException:
            app.invalidate()
            raise
        return app

    def revoke(self):
        if not self._revoked:
            self._revoked = True
            result = self._revoke_callback()
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise StorageError('PG_RECORDER_REVOCATION_MUST_BE_SYNCHRONOUS')

    def _admission_ready(self):
        try:
            return self.ready and not self._revoked and self._local_ready() is True
        except Exception:
            return False

    def runner_startup(self, connectors):
        """Qualify the installed original product event/history routes before V2."""
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_event_application import (
            PgJointEventApplication,
        )
        events = self._event_application
        expected = PgJointEventApplication if self.controller.config.strategy.perpetual.enabled else PgSpotEventApplication
        if (self._runner_context is not None or not self._admission_ready() or type(events) is not expected
                or events.reporting is not self
                or type(connectors) is not dict):
            raise StorageError('PG_RECORDER_RUNNER_STARTUP_UNQUALIFIED')
        sources = events.runner_sources()
        if (set(connectors) != {source.market for _, source in sources}
                or any(connectors[source.market] is not connector for connector, source in sources)):
            raise StorageError('PG_RECORDER_RUNNER_SOURCE_MISMATCH')
        self._runner_context = (self.controller, events, sources)
        return self.controller, sources

    def assert_executor_creation(self, action):
        # Actual V2 startup here qualifies reporting/history only. Until the
        # complete native final trading assembly exists, even a direct
        # orchestrator call must not reach generic strategy.buy/sell.
        if self._runner_context is not None:
            raise ValueError('PG_RUNNER_TRADING_ASSEMBLY_REQUIRED')

    async def recover(self):
        """Repair immutable original reporting only. Never construct a sender."""
        async with self._mutex:
            self._healthy = False

            async def profile(c):
                return await c.fetchval("""SELECT CASE WHEN jsonb_typeof(body->'recorder_sources')='array' THEN
                    CASE WHEN jsonb_array_length(body->'recorder_sources')<=2
                        AND octet_length((body->'recorder_sources')::text)<=$4 THEN body->'recorder_sources' END END
                    FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3""",
                                        self.financial._runtime.config.account_id, self.binding.config_hash, self.binding.policy_hash,
                                        self.repository.max_read_bytes)
            imported = await self.financial._runtime._admit(profile, safety=True)
            if (not isinstance(imported, list) or sorted(json.dumps(s, sort_keys=True) for s in imported)
                    != sorted(json.dumps(asdict(repository.source), sort_keys=True) for repository in self.events)):
                raise StorageError('PG_RECORDER_REGISTERED_SOURCES_REQUIRED')
            repositories = (self.configurations, self.repository, self.position_repository, *self.events)
            for repository in repositories:
                for item in await repository.pending(self.binding):
                    cut = (await self.financial.snapshot(self.binding)).cut
                    await repository.apply(item.command.operation_id, cut=cut, owner=item.owner)
            state = await self.configurations.configuration(self.binding)
            if state.sequence == 0:
                captured = self.controller.config.model_dump_json()
                observed = await self.financial.observation_cut(self.binding)
                command = ConfigurationReport(
                    'config-start:' + fingerprint({'owner': self.owner.json(), 'config': captured}), self.binding,
                    self.controller.config.id, 0, 1, captured, self.binding.policy_hash,
                    fingerprint(self.controller.config.model_dump(mode='json')), 'INITIAL', 'CONFIG_STARTUP',
                    int(observed.observed_at.timestamp() * 1000))
                await self.configurations.stage(command, cut=observed.cut, owner=self.owner)
                await self.configurations.apply(command.operation_id, cut=(await self.financial.snapshot(self.binding)).cut, owner=self.owner)
                state = await self.configurations.configuration(self.binding)
            if state.controller_json != self.controller.config.model_dump_json():
                raise StorageError('PG_RECORDER_CONTROLLER_CONFIG_MISMATCH')
            if state.rejection_latched or state.kill_latched:
                self.revoke()
            rows = await self.repository.read(self.binding)
            self._startup_infos = tuple(ExecutorInfo.model_validate_json(row.payload_json) for row in rows)
            self._startup_positions = (await self.position_repository.positions(self.binding)).positions
            for repository in self.events:
                await repository.read(self.binding)
            self._healthy = True

    async def before_dispatch(self, action):
        if self._runner_context is not None:
            return False  # Observation-only startup does not admit new risk.
        if not self._admission_ready() or action.controller_id != self.controller.config.id:
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
                await self.repository.project(command, owner=self.owner)
                return not self._closed and not self._revoked and self._local_ready() is True
        except BaseException:
            self.invalidate()
            raise
        finally:
            self._busy = False

    def fresh_stored_infos(self):
        raise StorageError('PG_RECORDER_ASYNC_READ_REQUIRED')

    async def stored_infos(self):
        rows = await self.repository.read(self.binding)
        return tuple(ExecutorInfo.model_validate_json(row.payload_json) for row in rows
                     if json.loads(row.payload_json)['status'] == RunnableStatus.TERMINATED.value)

    async def _update(self, captured, callbacks):
        async with self._mutex:
            rows = await self.repository._read(self.binding, False)
            current = [row for row in rows if json.loads(row['body']['command']['payload_json'])['id'] == captured.executor_id]
            if len(current) != 1:
                raise StorageError('PG_RECORDER_INITIAL_REPORT_REQUIRED')
            if current[0]['body']['command']['payload_json'] == captured.payload_json:
                receipt = await self.repository.apply(
                    current[0]['projection_id'], cut=(await self.financial.snapshot(self.binding)).cut,
                    owner=OwnerToken.from_json(current[0]['body']['owner']))
                for callback in tuple(callbacks):
                    callback(receipt)
                return receipt
            sequence = current[0]['sequence'] - 1  # Native progress starts at zero after INITIAL.
            command = ExecutorProgress('progress:' + fingerprint({'id': captured.executor_id, 'sequence': sequence,
                                                                  'payload': captured.payload_json}), self.binding, sequence, captured.payload_json)
            receipt = await self.repository.project(command, owner=self.owner)
            for callback in tuple(callbacks):
                callback(receipt)
            return receipt

    async def _update_positions(self, captured, callbacks):
        async with self._mutex:
            state = await self.position_repository.positions(self.binding)
            command = PositionReport('positions:' + fingerprint({'capture': captured.json(), 'sequence': state.sequence}),
                                     self.binding, captured.controller_id, state.sequence, captured.timestamp_ms, captured.positions)
            receipt = await self.position_repository.project(command, owner=self.owner)
            for callback in tuple(callbacks):
                callback(receipt)
            return receipt

    def enqueue_configuration(self, command, *, on_committed=None):
        if (type(command) is not ConfigurationReport or command.binding != self.binding
                or command.controller_id != self.controller.config.id):
            self.invalidate()
            raise StorageError('PG_RECORDER_CONFIGURATION_CAPTURE_INVALID')
        if command.outcome == 'REJECTED' or json.loads(command.controller_json)['manual_kill_switch']:
            # Revocation is synchronous at capture; a queue/DB failure cannot
            # leave the old local dispatch permission in place.
            self.revoke()
        return self._enqueue(('configuration', command.operation_id, fingerprint(command.json())),
                             command, self._update_configuration, on_committed)

    async def _update_configuration(self, command, callbacks):
        async with self._mutex:
            receipt = await self.configurations.project(command, owner=self.owner)
            for callback in tuple(callbacks):
                callback(receipt)
            return receipt

    def enqueue_event(self, command):
        if type(command) is not EventProjection or command.binding != self.binding or not any(r.source == command.source for r in self.events):
            self.invalidate()
            raise StorageError('PG_RECORDER_EVENT_CAPTURE_INVALID')
        return self._enqueue(('event', command.event_id, command.payload_json), command, self._update_event, None)

    async def _update_event(self, command, callbacks):
        async with self._mutex:
            repository = next(r for r in self.events if r.source == command.source)
            origin = await repository.event_origin(command.event_id, self.binding)
            owner = self.owner
            if origin is not None:
                if origin.command.payload_json != command.payload_json or origin.command.source != command.source:
                    raise StorageError('PG_RECORDER_EVENT_OPERATION_CONFLICT')
                command, owner = origin.command, origin.owner
            return await repository.project(command, owner=owner)

    async def restore_spot_dedup(self, reader):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_dedup import PgSpotDedupRecovery
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
        if (type(reader) is not PgSpotAccountReader or reader.repository is not self.financial
                or len([r for r in self.events if r.source.market == 'okx']) != 1):
            raise StorageError('PG_RECORDER_DEDUP_CONTEXT_INVALID')

        def idle():
            return (self._healthy and not self._closed and not self._pending and not self._busy
                    and self.controller.config.storage_binding == self.binding)

        if self._mutex.locked():
            raise StorageError('PG_RECORDER_DEDUP_IDLE_REQUIRED')
        async with self._mutex:
            if not idle():
                raise StorageError('PG_RECORDER_DEDUP_IDLE_REQUIRED')
            events = next(r for r in self.events if r.source.market == 'okx')
            return await PgSpotDedupRecovery(reader, events, local_idle=idle).restore()

    async def install_spot_events(self, observer):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
        return await PgSpotEventApplication.install(self, observer)

    async def install_joint_events(self, observer):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_event_application import (
            PgJointEventApplication,
        )
        return await PgJointEventApplication.install(self, observer)

    async def close(self):
        try:
            if self._event_application is not None:
                await self._event_application.close()
        finally:
            await super().close()
