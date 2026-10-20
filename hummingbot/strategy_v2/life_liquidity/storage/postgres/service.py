"""Explicit native offline service assembly; no File/ORM or owner bootstrap.

This installation is deliberately observation-only until its trading lifecycle
is qualified. It constructs the actual configured controller, V2 runner and
both authenticated SDK event/history routes over one native account runtime.
"""
import asyncio
import inspect
import math
from dataclasses import dataclass

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_cancellation import PgMakerCancellation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import PgObservationAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
from hummingbot.strategy_v2.models.base import RunnableStatus


@dataclass(frozen=True)
class PgServiceLimits:
    max_rows: int
    max_bytes: int
    max_reads: int
    max_pages: int
    max_actions: int
    max_pending: int
    max_callbacks: int
    max_uid_age_ms: int
    max_auth_age_ms: int
    max_clock_skew_ms: int
    source_max_age_ms: int
    request_timeout_ms: int
    collection_timeout_ms: int
    cycle_timeout_ms: int
    shutdown_timeout_ms: int
    max_cancels: int
    cancel_retry_ms: int
    max_levels: int

    def __post_init__(self):
        if (any(type(v) is not int or v <= 0 for v in vars(self).values())
                or self.max_rows > 4096 or self.max_bytes > 16777216 or self.max_reads > 512
                or self.max_pages > 64 or self.max_actions > 4096 or self.max_pending > 64
                or self.max_callbacks > 1024 or self.max_cancels > 64 or self.max_levels > 64
                or self.source_max_age_ms > self.max_uid_age_ms
                or max(self.max_uid_age_ms, self.max_auth_age_ms, self.max_clock_skew_ms,
                       self.request_timeout_ms, self.collection_timeout_ms, self.cycle_timeout_ms,
                       self.shutdown_timeout_ms, self.cancel_retry_ms) > 60000):
            raise StorageError('PG_SERVICE_LIMITS_INVALID')


class PgServiceController(ControllerBase):
    """Native controller identity and complete runner scope; no generic sender."""
    async_dispatch_enabled = True

    def __init__(self, service, config, provider):
        super().__init__(config, provider, asyncio.Queue(maxsize=service.limits.max_pending))
        self.service = service
        self._runner_orchestrator = None
        self.processed_data = {'trading_enabled': False, 'reason_code': 'PG_SERVICE_TRADING_NOT_QUALIFIED'}

    async def runner_facts(self):
        self.service._original()
        orchestrator = self._runner_orchestrator
        if orchestrator is None:
            if not self.service._opening:
                raise StorageError('PG_SERVICE_RUNNER_MISSING')
            ids = ()
        else:
            if (orchestrator is not self.service.runner.executor_orchestrator
                    or set(orchestrator.active_executors) != {self.config.id}):
                raise StorageError('PG_SERVICE_RUNNER_SCOPE_CHANGED')
            executors = orchestrator.active_executors[self.config.id]
            ids = tuple(sorted(v.config.id for v in executors))
            if len(ids) != len(set(ids)) or ids:
                # This selected startup profile is observation-only. A direct
                # generic executor injection must not become complete scope.
                raise StorageError('PG_SERVICE_UNQUALIFIED_EXECUTOR')
        return RunnerActionFacts(self.config.storage_binding, utc(self.service.clock()), self.config.id, ids, True)

    def initialize_candles(self):
        pass

    def start(self):
        self.service._original()
        if self.service._revoked or self.status is not RunnableStatus.NOT_STARTED:
            raise StorageError('PG_SERVICE_CONTROLLER_ALREADY_STARTED_OR_STOPPED')
        # Both SDK listener pumps own observation work; there is no inherited
        # planner/control task that could accidentally create generic actions.
        self._status = RunnableStatus.RUNNING

    def stop(self):
        self.service.revoke()
        super().stop()

    async def update_processed_data(self):
        self.service._original()
        self.processed_data = dict(trading_enabled=False, reason_code='PG_SERVICE_TRADING_NOT_QUALIFIED')

    def determine_executor_actions(self):
        return []

    def local_runner_create_preflight(self, action):
        return False

    def local_runner_create_fence(self, action):
        return False

    async def async_authorize_runner_create_action(self, action):
        return False

    def on_runner_create_action_rejected(self, action):
        self.service.journal.on_runner_create_action_rejected(action)

    def submit_executor_spot_order(self, *args, **kwargs):
        raise StorageError('PG_SERVICE_TRADING_NOT_QUALIFIED')

    def to_format_status(self):
        return ['PostgreSQL observation service: trading disabled']


class PgServiceRunner(StrategyV2Base):
    @property
    def current_timestamp(self):
        timestamp = super().current_timestamp
        return timestamp if math.isfinite(timestamp) else utc(self.service.clock()).timestamp()

    def __init__(self, service):
        self.service, self._started_headless = service, False
        super().__init__(service.connectors, repository_reporting={service.config.id: service.reporting},
                         dispatch_max_pending=service.limits.max_pending,
                         dispatch_shutdown_timeout_s=service.limits.shutdown_timeout_ms / 1000)

    def start(self, clock, timestamp):
        if self._started_headless or self.service._revoked:
            raise StorageError('PG_SERVICE_RUNNER_ALREADY_STARTED_OR_STOPPED')
        self._started_headless = True
        self._last_timestamp = timestamp
        self.service.controller.start()

    def stop(self, clock):
        self._is_stop_triggered = True
        self.service.revoke()

    def create_actions_proposal(self):
        return []

    def stop_actions_proposal(self):
        return []


class PgLiquidityService:
    """Own the native runtime, both SDK listeners, reporting and actual runner.

    Inputs are explicit operational limits and existing immutable account/owner
    configuration. Startup never imports funds, signs approval or activates risk.
    """

    @classmethod
    async def open(cls, config, *, runtime_config, owner, connectors, market_data_provider,
                   sources, limits, clock, independent_fill_value, independent_cashflow_value):
        from controllers.generic.life_liquidity import LifeLiquidityConfig
        if (type(config) is not LifeLiquidityConfig or config.storage_backend != 'postgres'
                or type(runtime_config) is not PgConfig or config.storage_binding != runtime_config.binding
                or config.storage_binding.environment != 'OFFLINE_SIMULATION'
                or type(owner) is not OwnerToken or type(limits) is not PgServiceLimits
                or not config.strategy.perpetual.enabled or not runtime_config.write
                or runtime_config.safety_size < 2 or limits.max_pending > runtime_config.max_pending
                or type(connectors) is not dict or set(connectors) != {'okx', 'okx_perpetual'}
                or getattr(market_data_provider, 'connectors', None) != connectors
                or type(sources) is not tuple or len(sources) != 2
                or any(type(s) is not EventSource or s.controller_id != config.id for s in sources)
                or tuple(s.market for s in sources) != ('okx', 'okx_perpetual')
                or any(not callable(v) for v in (clock, independent_fill_value, independent_cashflow_value))
                or inspect.iscoroutinefunction(clock)):
            raise StorageError('PG_SERVICE_ASSEMBLY_INVALID')
        service = cls()
        service.config = type(config).model_validate_json(config.model_dump_json())
        service._config_json = service.config.model_dump_json()
        service.limits, service.clock, service.connectors = limits, clock, dict(connectors)
        service._connectors = tuple(service.connectors.items())
        service._provider, service._clock, service._limits = market_data_provider, clock, limits
        service._opening, service._revoked, service._paused = True, False, False
        service.runtime = service.financial = service.reporting = service.events = service.runner = service.hedge = None
        service._close_task = None
        service.controller = PgServiceController(service, service.config, market_data_provider)
        try:
            service.runtime = await PgRuntime.open(runtime_config)
            service.financial = PgSpotRepository(service.runtime)
            capital = PgSharedCapitalRepository(service.financial, owner=owner, observation=service._missing_capture,
                                                max_rows=limits.max_rows, max_bytes=limits.max_bytes)
            carry = PgCarryRepository(capital, observation=service._missing_capture,
                                      max_rows=limits.max_rows, max_bytes=limits.max_bytes)
            swap = PgSwapRepository(capital)
            service.journal = PgQuoteRunnerJournal(service.financial, owner=owner, controller_id=config.id,
                                                   config_version=config.strategy.config_version,
                                                   runner_facts=service.controller.runner_facts, local_ready=service._local_ready,
                                                   clock=clock, max_actions=limits.max_actions)
            identity = dict(max_uid_age_ms=limits.max_uid_age_ms, max_auth_age_ms=limits.max_auth_age_ms,
                            max_clock_skew_ms=limits.max_clock_skew_ms, collection_timeout_ms=limits.request_timeout_ms)
            account = await PgObservationAccountSource.create(service.journal, connector=service.connectors['okx'],
                                                              max_pending=limits.max_pending, **identity)
            service.reader = PgSpotAccountReader(account, max_reads=limits.max_reads,
                                                 max_bytes=limits.max_bytes, timeout_ms=limits.collection_timeout_ms)
            service.collector = PgJointAccountReconciler(service.reader, swap, max_pages=limits.max_pages,
                                                         max_age_ms=limits.source_max_age_ms)
            cancellation = PgMakerCancellation(service.collector, paused=lambda: service._paused,
                                               max_cancels=limits.max_cancels, retry_interval_ms=limits.cancel_retry_ms,
                                               timeout_ms=limits.cycle_timeout_ms)
            service.hedge = await PgHedgeApplication.create(
                PgHedgeRepository(swap), connector=service.connectors['okx_perpetual'], pause_makers=service._pause,
                maker_cancellation=cancellation, collect=service._missing_capture,
                local_fence=service._trading_ready, clock=clock, cycle_timeout_ms=limits.cycle_timeout_ms, **identity)
            service.source = PgHedgeSource(service.collector, carry=carry, swap_source=service.hedge.source,
                                           max_levels=limits.max_levels, timeout_ms=limits.cycle_timeout_ms)
            service.hedge.bind_sources(service.source)
            observer = PgJointFinancialObserver(service.source, independent_fill_value=independent_fill_value,
                                                independent_cashflow_value=independent_cashflow_value)
            service.reporting = await PgRecorderApplication.install(
                service.financial, controller=service.controller, owner=owner, sources=sources,
                revoke=service.revoke, local_ready=service._local_ready,
                max_pending=limits.max_pending, max_callbacks=limits.max_callbacks,
                shutdown_timeout_s=limits.shutdown_timeout_ms / 1000,
                max_rows=limits.max_rows, max_read_bytes=limits.max_bytes)
            service.events = await service.reporting.install_joint_events(observer)
            service.runner = PgServiceRunner(service)
            service._opening = False
            service._original()
            return service
        except BaseException:
            await service.close()
            raise

    @staticmethod
    def _missing_capture(*args):
        raise StorageError('PG_SERVICE_ORIGINAL_CAPTURE_REQUIRED')

    def _original(self):
        if (self.config.model_dump_json() != self._config_json or tuple(self.connectors.items()) != self._connectors
                or self.controller.service is not self or self.controller.config is not self.config
                or self.clock is not self._clock or self.limits is not self._limits
                or self.controller.market_data_provider is not self._provider
                or self._provider.connectors != self.connectors):
            self.revoke()
            raise StorageError('PG_SERVICE_CONTEXT_CHANGED')

    def _local_ready(self):
        return not self._revoked and not self.config.manual_kill_switch

    @staticmethod
    def _trading_ready():
        # Signed observation/reporting readiness is never a trading grant.
        # The configured final-route assembly has a separate acceptance exit.
        return False

    def _pause(self):
        self._paused = True
        return True

    async def reload_yaml(self, *args, **kwargs):
        self.revoke()
        raise StorageError('PG_SERVICE_RELOAD_NOT_QUALIFIED')

    def revoke(self):
        if not self._revoked:
            self._revoked = True
            if self.runtime is not None:
                self.runtime._healthy = False
                self.runtime._ingress_epoch += 1
            for component in (self.events, self.hedge):
                if component is not None:
                    component.stop()
            journal = getattr(self, 'journal', None)
            if journal is not None:
                journal.revoke()

    async def close(self):
        self.revoke()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        async with asyncio.timeout(self.limits.shutdown_timeout_ms / 1000):
            await asyncio.shield(self._close_task)

    async def _close(self):
        try:
            if self.runner is not None:
                self.runner._is_stop_triggered = True
                listener = self.runner.listen_to_executor_actions_task
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
                await self.runner._async_action_dispatcher.close()
                self.controller.stop()
            try:
                if self.hedge is not None:
                    await self.hedge.close(shutdown_timeout_ms=self.limits.shutdown_timeout_ms)
            finally:
                if self.reporting is not None:
                    await self.reporting.close()
        finally:
            if self.financial is not None:
                await self.financial.close()
            elif self.runtime is not None:
                await self.runtime.close()
