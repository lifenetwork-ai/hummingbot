"""Explicit native offline service assembly; no File/ORM or owner bootstrap.

Observation is the default. The explicit imported native trading profile binds
quote requests to the actual controller/V2 runner and both authenticated SDK
event/history routes. Neither profile grants real-account trading eligibility.
"""
import asyncio
import inspect
import json
import math
from contextlib import AsyncExitStack
from dataclasses import dataclass
from decimal import Decimal as D

from pydantic import BaseModel, ConfigDict

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine, ReferencePolicy
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StorageError,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_cancellation import PgMakerCancellation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_schedule import PgMakerLoop, PgMakerSchedule
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import PgObservationAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import (
    PgQuoteRunnerJournal,
    _RunnerFactCapture,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class PgNativeTradingConfig(BaseModel):
    """Explicit imported offline model. No inferred financial thresholds."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    reference_policy: ReferencePolicy
    life_anchor_usdt: D
    model_version: str
    max_observation_skew_ms: int
    max_depth_distance_bps: D
    available_edge_quote_usdt: D
    quote_schedule_timeout_ms: int
    maker_schedule: PgMakerSchedule | None = None
    hedge_enabled: bool = False


class _NativeQuoteDispatch:
    """Original configured service/action/dispatcher task, never a SQL receipt."""

    def __init__(self, service):
        self.service = service
        self._staged = None

    def owns(self, action):
        s = self.service
        s._original()
        pending = s.runner._async_action_dispatcher._pending.get((action.controller_id, action.executor_config.id))
        return (s._trading_ready() and s._quote_action is not None
                and s.journal._capture(action) == s.journal._capture(s._quote_action)
                and pending is not None and pending[0] is asyncio.current_task()
                and s.reporting._trading_admission is self
                and s.quote_application.source is s.quote_source
                and s.journal.local_runner_create_preflight(action))

    def stage(self, action):
        if not self.owns(action):
            raise StorageError('PG_SERVICE_DISPATCH_ORIGIN_REQUIRED')
        self._staged = asyncio.current_task(), fingerprint({'action_json': action.model_dump_json()})

    def consume(self, action):
        staged, self._staged = self._staged, None
        if (not self.owns(action) or staged != (asyncio.current_task(), fingerprint({'action_json': action.model_dump_json()}))):
            raise StorageError('PG_SERVICE_DISPATCH_ORIGIN_REQUIRED')

    def owns_report(self, info):
        """An existing original executor may report during a sibling's send."""
        s = self.service
        s._original()
        executor = s._owned_executors.get(info.id)
        return (executor is not None and info.controller_id == s.config.id
                and any(v is executor for v in s.runner.executor_orchestrator.active_executors[s.config.id])
                and info.config == executor.config and info == executor.executor_info
                and s.journal._capture(CreateExecutorAction(controller_id=s.config.id, executor_config=executor.config))
                == s.journal._entry(CreateExecutorAction(controller_id=s.config.id, executor_config=executor.config)).row)


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
    """Native controller identity, exact runner ownership and protected dispatch."""
    async_dispatch_enabled = True

    def __init__(self, service, config, provider):
        super().__init__(config, provider, asyncio.Queue(maxsize=service.limits.max_pending))
        self.service = service
        self._runner_orchestrator = None
        self.processed_data = {'trading_enabled': False, 'reason_code': 'PG_SERVICE_TRADING_NOT_QUALIFIED'}

    async def runner_facts(self):
        return self._runner_facts_at(utc(self.service.clock()))

    async def native_runner_facts(self, *, _capture):
        if type(_capture) is not _RunnerFactCapture:
            raise StorageError('PG_SERVICE_RUNNER_CAPTURE_REQUIRED')
        observed_at = _capture.take(self.service.journal)
        return self._runner_facts_at(observed_at)

    def _runner_facts_at(self, observed_at):
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
            all_ids = tuple(v.config.id for v in executors)
            if (len(all_ids) != len(set(all_ids)) or any(
                    self.service._owned_executors.get(v.config.id) is not v
                    or v.config.id not in self.service.journal._entries
                    or self.service.journal._capture(CreateExecutorAction(
                        controller_id=self.config.id, executor_config=v.config)) != self.service.journal._entries[v.config.id].row
                    for v in executors)):
                # This selected startup profile is observation-only. A direct
                # generic executor injection must not become complete scope.
                self.service.revoke()
                raise StorageError('PG_SERVICE_UNQUALIFIED_EXECUTOR')
            # Stopped original executors remain reportable until their final
            # projection commits; they cannot create/retry a wire. SDK scope
            # and signed terminal evidence are checked independently.
            ids = tuple(sorted(v.config.id for v in executors if v.is_active))
        return RunnerActionFacts(self.config.storage_binding, observed_at, self.config.id, ids, True)

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
        enabled = self.service._trading_ready()
        self.processed_data = dict(trading_enabled=enabled,
                                   reason_code='PG_SERVICE_OFFLINE_NATIVE' if enabled else 'PG_SERVICE_TRADING_NOT_QUALIFIED')

    def determine_executor_actions(self):
        return []

    def local_runner_create_preflight(self, action):
        return self.service.native_trading and self.service.journal.local_runner_create_preflight(action)

    def local_runner_create_fence(self, action):
        return self.service.native_trading and self.service.journal.local_runner_create_fence(action)

    async def async_authorize_runner_create_action(self, action):
        # The owning cycle just committed this exact permanent claim. Creation
        # is still a local, protected executor: native DISPATCHED, PREPARE and
        # ARM must all commit before its SDK can send. Re-reading the reporting
        # projection here adds no financial authority and spends quote age.
        admission = self.service.reporting._trading_admission
        return admission is not None and admission.owns(action)

    async def async_before_runner_dispatch(self, action):
        return self.service.native_trading and await self.service.reporting.before_dispatch(action)

    async def async_on_runner_create_action_dispatched(self, action):
        from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
        if not self.service.reporting._trading_admission.owns(action):
            raise StorageError('PG_SERVICE_DISPATCH_ORIGIN_REQUIRED')
        executors = self._runner_orchestrator.active_executors[self.config.id]
        matches = [v for v in executors if v.config.id == action.executor_config.id]
        if len(matches) != 1 or type(matches[0]) is not OrderExecutor or matches[0].config != action.executor_config:
            raise StorageError('PG_SERVICE_UNQUALIFIED_EXECUTOR')
        self.service._owned_executors[action.executor_config.id] = matches[0]
        return await self.service.journal.async_on_runner_create_action_dispatched(action)

    def on_runner_create_action_uncertain(self, action):
        self.service.journal.on_runner_create_action_uncertain(action)
        self.service.revoke()

    def on_runner_create_action_rejected(self, action):
        self.service.journal.on_runner_create_action_rejected(action)

    def submit_executor_spot_order(self, *args, **kwargs):
        if not self.service.native_trading:
            raise StorageError('PG_SERVICE_TRADING_NOT_QUALIFIED')
        return self.service.journal.submit_executor_spot_order(*args, **kwargs)

    def to_format_status(self):
        if self.service._trading_ready():
            return ['PostgreSQL offline native trading: serialized quote requests enabled']
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

    def on_tick(self):
        # Native reporting is asynchronous; the generic runner's synchronous
        # stored-info/config reload path cannot be used here. Financial SDK
        # events remain live throughout every source window. Periodic diagnostic
        # snapshots wait until the owning financial cycle finishes and are
        # drained before the next source capture.
        s = self.service
        try:
            s._original()
            if self._is_stop_triggered or s._revoked or not self.market_data_provider.ready:
                return
            if s._quote_task is None and not s.hedge._busy:
                for executor in self.executor_orchestrator.active_executors[s.config.id]:
                    info = executor.executor_info
                    if s._owned_executors.get(info.id) is not executor or not s.reporting._trading_admission.owns_report(info):
                        raise StorageError('PG_SERVICE_UNQUALIFIED_EXECUTOR')
                    s.reporting.enqueue(info)
                if s.makers is not None and s.makers.task is None:
                    s.start_makers()
        except BaseException:
            s.revoke()
            raise

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
                   sources, limits, clock, independent_fill_value, independent_cashflow_value, native_trading=False, _startup_owner=None,
                   provisional_quotes=False):
        from controllers.generic.life_liquidity import LifeLiquidityConfig
        if (type(config) is not LifeLiquidityConfig or config.storage_backend != 'postgres'
                or type(runtime_config) is not PgConfig or config.storage_binding != runtime_config.binding
                or config.storage_binding.environment != 'OFFLINE_SIMULATION'
                or type(owner) is not OwnerToken or type(limits) is not PgServiceLimits
                or not config.strategy.spot.enabled or not config.strategy.perpetual.enabled or not runtime_config.write
                or runtime_config.safety_size < 2 or limits.max_pending > runtime_config.max_pending
                or type(connectors) is not dict or set(connectors) != {'okx', 'okx_perpetual'}
                or getattr(market_data_provider, 'connectors', None) != connectors
                or type(sources) is not tuple or len(sources) != 2
                or any(type(s) is not EventSource or s.controller_id != config.id for s in sources)
                or tuple(s.market for s in sources) != ('okx', 'okx_perpetual')
                or any(not callable(v) for v in (clock, independent_fill_value, independent_cashflow_value))
                or inspect.iscoroutinefunction(clock) or type(native_trading) is not bool
                or type(provisional_quotes) is not bool or provisional_quotes and not native_trading
                or _startup_owner is not None and (type(_startup_owner) is not list or _startup_owner)):
            raise StorageError('PG_SERVICE_ASSEMBLY_INVALID')
        if native_trading:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
            quota = PgSharedSdkQuota.for_connector(connectors['okx'])
            if quota is None or PgSharedSdkQuota.for_connector(connectors['okx_perpetual']) is not quota:
                raise StorageError('PG_SERVICE_SHARED_QUOTA_REQUIRED')
        service = cls()
        service.config = type(config).model_validate_json(config.model_dump_json())
        service._config_json = service.config.model_dump_json()
        service.limits, service.clock, service.connectors = limits, clock, dict(connectors)
        service._connectors = tuple(service.connectors.items())
        service._provider, service._clock, service._limits = market_data_provider, clock, limits
        service._opening, service._revoked, service._paused = True, False, False
        service.runtime = service.financial = service.reporting = service.events = service.runner = service.hedge = None
        service._close_task = None
        service.native_trading, service._native_trading = native_trading, native_trading
        service.provisional_quotes = service._provisional_quotes = provisional_quotes
        service.quote_application = service.quote_source = service.producer = None
        service._quote_task = service._quote_action = None
        service._hedge_task = None
        service._owned_executors = {}
        service._maker_operations = {}
        service.makers = None
        service.controller = PgServiceController(service, service.config, market_data_provider)
        if _startup_owner is not None:
            # The owning process host retains this cleanup handle even when
            # open fails before returning a fully constructed service.
            _startup_owner.append(service)
        try:
            service.runtime = await PgRuntime.open(runtime_config)
            service.financial = PgSpotRepository(service.runtime)
            births = None
            if provisional_quotes:
                from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository

                async def birth_policy(c):
                    return await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.quote_birth_policies WHERE account_id=$1 AND policy_hash=$2)',
                                            runtime_config.account_id, runtime_config.binding.policy_hash)
                if await service.runtime._admit(birth_policy, safety=True) is not True:
                    raise StorageError('PG_SERVICE_BIRTH_POLICY_REQUIRED')
                births = PgQuoteBirthRepository(service.financial)
            native_model = await service._read_trading_config() if native_trading else None
            if native_trading:
                await service._require_financial_intake()
            capital = PgSharedCapitalRepository(service.financial, owner=owner, observation=service._missing_capture,
                                                max_rows=limits.max_rows, max_bytes=limits.max_bytes)
            carry = PgCarryRepository(capital, observation=service._missing_capture,
                                      max_rows=limits.max_rows, max_bytes=limits.max_bytes)
            swap = PgSwapRepository(capital)
            service.journal = PgQuoteRunnerJournal(service.financial, owner=owner, controller_id=config.id,
                                                   config_version=config.strategy.config_version,
                                                   runner_facts=service.controller.runner_facts, local_ready=service._local_ready,
                                                   clock=clock, max_actions=limits.max_actions,
                                                   native_runner_facts=service.controller.native_runner_facts, birth_repository=births)
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
            service.cancellation = cancellation
            service.hedge = await PgHedgeApplication.create(
                PgHedgeRepository(swap), connector=service.connectors['okx_perpetual'], pause_makers=service._pause,
                maker_cancellation=cancellation, collect=service._missing_capture,
                pause_allocation=service._pause_allocation,
                execution_admission=service._hedge_execution_owned if native_trading else None,
                local_fence=service._trading_ready, clock=clock, cycle_timeout_ms=limits.cycle_timeout_ms, **identity)
            service.source = PgHedgeSource(service.collector, carry=carry, swap_source=service.hedge.source,
                                           max_levels=limits.max_levels, timeout_ms=limits.cycle_timeout_ms,
                                           available_edge_quote=D('0') if native_model is None else native_model.available_edge_quote_usdt)
            service.hedge.bind_sources(service.source)
            if native_model is not None and native_model.hedge_enabled:
                await service._require_hedge_policies()
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
            if native_trading:
                await service._install_quotes(native_model, identity)
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
                or self.clock is not self._clock or self.limits is not self._limits or self.native_trading is not self._native_trading
                or self.provisional_quotes is not self._provisional_quotes
                or self.controller.market_data_provider is not self._provider
                or self._provider.connectors != self.connectors):
            self.revoke()
            raise StorageError('PG_SERVICE_CONTEXT_CHANGED')

    def _local_ready(self):
        return not self._revoked and not self.config.manual_kill_switch

    def _trading_ready(self):
        # Signed observation/reporting readiness is never a trading grant.
        # The configured final-route assembly has a separate acceptance exit.
        return self.native_trading and self._local_ready()

    async def _read_trading_config(self):
        async def read(c):
            return await c.fetchval('''SELECT CASE WHEN octet_length(body::text)<=65536 THEN body->'native_trading' END
                FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3''',
                                    self.runtime.config.account_id, self.config.storage_binding.config_hash,
                                    self.config.storage_binding.policy_hash)
        body = await self.runtime._admit(read, safety=True)
        try:
            model = PgNativeTradingConfig.model_validate_json(json.dumps(body))
            if (not model.available_edge_quote_usdt.is_finite() or not 0 <= model.available_edge_quote_usdt < D('1e38')
                    or not 0 < model.quote_schedule_timeout_ms <= 60000):
                raise ValueError()
            return model
        except (ValueError, TypeError):
            raise StorageError('PG_SERVICE_TRADING_CONFIG_INVALID') from None

    async def _install_quotes(self, model, identity):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import PgSpotMarketSource
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_producer import PgSpotQuoteProducer
        engine = ReferenceEngine(model.reference_policy, life_anchor_usdt=model.life_anchor_usdt,
                                 model_version=model.model_version, life_source_id='okx:LIFE-USDT', source_anchors_usdt={})
        self.market = await PgSpotMarketSource.create(
            self.source, reference_engine=engine, max_observation_skew_ms=model.max_observation_skew_ms,
            max_depth_distance_bps=model.max_depth_distance_bps, max_levels=self.limits.max_levels,
            timeout_ms=self.limits.cycle_timeout_ms)
        self.quote_source = await PgQuoteAccountSource.create(
            self.journal, connector=self.connectors['okx'], market_source=self.market,
            max_pending=self.limits.max_pending, **identity)
        self.producer = await PgSpotQuoteProducer.create(self.quote_source)
        self.quote_application = PgSpotQuoteApplication(
            self.quote_source, preparation_source=self.producer.prepare, max_pending=self.limits.max_pending,
            operation_timeout_ms=self.limits.cycle_timeout_ms, shutdown_timeout_ms=self.limits.shutdown_timeout_ms)
        self.producer.bind(self.quote_application)
        self._trading_model = model
        self.reporting.install_trading_admission(_NativeQuoteDispatch(self))
        if model.maker_schedule is not None:
            schedule = model.maker_schedule
            if (2 * len(schedule.levels) > self.limits.max_cancels
                    or any(v >= len(self.producer._levels) for v in schedule.levels)
                    or 2 * len(schedule.levels) * schedule.max_cycles > self.journal.max_actions):
                raise StorageError('PG_SERVICE_MAKER_SCHEDULE_BOUND')
            self.makers = PgMakerLoop(
                schedule, namespace=fingerprint([self.config.storage_binding.json(), self.journal.owner.json()]),
                quote=self.quote, cancel=self.cancel_makers, ready=self._trading_ready, can_quote=self._maker_quote_ready)

    async def _require_financial_intake(self):
        """Both enabled products need their complete native monetary route."""
        async def read(c):
            return await c.fetchval('''SELECT
                EXISTS(SELECT 1 FROM life_financial.spot_fill_policies p WHERE p.account_id=$1 AND p.policy_hash=$2)
                AND EXISTS(SELECT 1 FROM life_financial.swap_fill_policies p WHERE p.account_id=$1 AND p.policy_hash=$2)
                AND EXISTS(SELECT 1 FROM life_financial.funding_accounting_policies p WHERE p.account_id=$1 AND p.policy_hash=$2)
                AND EXISTS(SELECT 1 FROM life_financial.joint_batch_policies p WHERE p.account_id=$1 AND p.policy_hash=$2)''',
                                    self.runtime.config.account_id, self.config.storage_binding.policy_hash)
        if await self.runtime._admit(read, safety=True) is not True:
            raise StorageError('PG_SERVICE_FINANCIAL_INTAKE_REQUIRED')

    async def _require_hedge_policies(self):
        """Refuse a selected route whose immutable native policies are absent.

        This read installs nothing and grants no financial permission. Every
        later source/command independently verifies its current native basis.
        """
        async def read(c):
            return await c.fetchval('''SELECT NOT (life_financial.spot_state(a.account_id)->>'unverified')::boolean
                AND EXISTS(SELECT 1 FROM life_financial.hedge_policies h WHERE h.account_id=a.account_id AND h.policy_hash=a.policy_hash)
                AND EXISTS(SELECT 1 FROM life_financial.portfolio_policies p WHERE p.account_id=a.account_id AND p.policy_hash=a.policy_hash)
                AND EXISTS(SELECT 1 FROM life_financial.shared_release_policies r WHERE r.account_id=a.account_id AND r.policy_hash=a.policy_hash)
                AND EXISTS(SELECT 1 FROM life_financial.swap_terminal_policies t WHERE t.account_id=a.account_id AND t.policy_hash=a.policy_hash)
                FROM life_financial.accounts a WHERE a.account_id=$1''', self.runtime.config.account_id)
        if await self.runtime._admit(read, safety=True) is not True:
            raise StorageError('PG_SERVICE_HEDGE_POLICY_REQUIRED')

    async def execute_hedge(self, operation_id):
        """One configured signed hedge; serialize with original maker work.

        Retire existing owned makers through their authenticated cancellation,
        NAV and release route before the new hedge source is captured. Existing
        plan readback is diagnostic only and cannot authorize another SDK send.
        This operation never restarts the maker schedule.
        """
        identifier(operation_id)
        self._original()
        self._maker_admission()
        if not self._trading_ready() or not self._trading_model.hedge_enabled:
            raise StorageError('PG_SERVICE_HEDGE_NOT_ENABLED')
        if self._quote_task is not None or self.hedge._busy:
            raise StorageError('PG_SERVICE_QUOTE_BACKPRESSURE')
        loop = asyncio.get_running_loop()
        started = loop.time()
        schedule_seconds = self._trading_model.quote_schedule_timeout_ms / 1000
        cycle_seconds = self.limits.cycle_timeout_ms / 1000
        self._quote_task = asyncio.current_task()
        try:
            # Reserve the cycle before the first read. Original-ID readback
            # must not cancel a later maker, pause allocation or collect sources.
            # This is a fresh verified receipt, never a cached risk decision.
            async with asyncio.timeout_at(started + min(schedule_seconds, cycle_seconds)):
                self.hedge._original(observation=True)
                previous = await self.hedge.repository.lookup(operation_id, self.config.storage_binding)
                if previous is None:
                    preflight = await self.hedge.repository.lookup(
                        PgHedgeSource.preflight_operation_id(operation_id), self.config.storage_binding)
                    if preflight is not None:
                        raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
            if previous is not None:
                return previous
            lookup_seconds = loop.time() - started
            current = self.runner.executor_orchestrator.active_executors[self.config.id]
            if current:
                # The existing cancellation API reserves this same task's slot
                # synchronously. No await separates either ownership transfer.
                async with asyncio.timeout_at(started + schedule_seconds):
                    self._quote_task = None
                    await self.cancel_makers(tuple(v.config.id for v in current))
                    self._quote_task = asyncio.current_task()
                schedule_deadline = loop.time() + schedule_seconds
            else:
                schedule_deadline = started + schedule_seconds
            async with asyncio.timeout_at(schedule_deadline):
                await self._join_maker_events()
                if await self._read_trading_config() != self._trading_model:
                    raise StorageError('PG_SERVICE_TRADING_CONFIG_CHANGED')
                await self.controller.runner_facts()
                self._hedge_task = asyncio.current_task()
                try:
                    # The probe consumes the ORIGINAL application cycle budget;
                    # it does not introduce another five-second allowance.
                    remaining = cycle_seconds - lookup_seconds
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        result = await self.hedge.execute(operation_id)
                finally:
                    self._hedge_task = None
                await self._join_maker_events()
                return result
        except BaseException:
            self.revoke()
            raise
        finally:
            self._quote_task = None

    def _hedge_execution_owned(self):
        self._original()
        return (self._hedge_task is asyncio.current_task() and self._quote_task is self._hedge_task
                and self._hedge_task is not None and self._trading_ready() and self._trading_model.hedge_enabled)

    async def settle_hedge(self, operation_id, plan_operation_id):
        """Close an original hedge from fresh signed terminal truth.

        Callers supply identities only. No callback amount or cached ACK can
        certify a terminal or release. An interrupted stage retains its native
        original operation and claims for explicit reconciliation. This route
        neither sends another order nor restarts makers.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import (
            MeasurePortfolio,
            PgPortfolioRepository,
        )
        command = SettleHedge(operation_id, plan_operation_id)
        self._original()
        self._maker_admission()
        if not self.native_trading or not self._trading_model.hedge_enabled:
            raise StorageError('PG_SERVICE_HEDGE_NOT_ENABLED')
        if self._quote_task is not None or self.hedge._busy:
            raise StorageError('PG_SERVICE_QUOTE_BACKPRESSURE')
        self._quote_task = asyncio.current_task()
        started = False
        try:
            async with asyncio.timeout(self._trading_model.quote_schedule_timeout_ms / 1000):
                root = await self.hedge.repository._read(self.config.storage_binding, plan_operation_id)
                if (root is None or root['command']['kind'] != 'HEDGE_PLAN'
                        or root['command']['command']['child'] is None):
                    raise StorageError('PG_SERVICE_HEDGE_CHILD_REQUIRED')
                child = root['command']['command']['child']['prepare']
                intent_id = child['config']['id']
                key = fingerprint(command.json())
                terminal_id, release_id = 'service-hedge-terminal:' + key, 'service-hedge-retire:' + key
                release = self.cancellation.release
                if await release.release._read(release_id, self.config.storage_binding) is not None:
                    # Original verified receipt readback has no new source,
                    # settlement/release grant or SDK effect, even after stop.
                    await release.retire_claim(release_id, intent_id, terminal_id)
                    return await self.hedge.settle(command)
                if not self._trading_ready():
                    raise StorageError('PG_SERVICE_HEDGE_RECONCILIATION_REQUIRED')
                started = True
                await self._join_maker_events()
                if await self._read_trading_config() != self._trading_model:
                    raise StorageError('PG_SERVICE_TRADING_CONFIG_CHANGED')
                await self.hedge.observe_sources('service-hedge-source:' + key)
                portfolio = PgPortfolioRepository(self.collector.swap)
                await portfolio.measure(MeasurePortfolio('service-hedge-nav:' + key),
                                        cut=(await self.financial.observation_cut(self.config.storage_binding)).cut,
                                        owner=self.journal.owner)
                terminal = await release.confirm_terminal(terminal_id, intent_id)
                if terminal.state != 'TERMINAL':
                    raise StorageError('PG_SERVICE_HEDGE_TERMINAL_REQUIRED')
                result = await self.hedge.settle(command)
                if result.state != 'TERMINAL':
                    raise StorageError('PG_SERVICE_HEDGE_SETTLEMENT_REQUIRED')
                await release.retire_claim(release_id, intent_id, terminal_id)
                return result
        except BaseException:
            if started:
                self.revoke()
            raise
        finally:
            self._quote_task = None

    def _maker_quote_ready(self, remaining):
        self._original()
        if type(remaining) is not int or remaining <= 0 or self.makers is None:
            raise StorageError('PG_SERVICE_MAKER_SCHEDULE_BOUND')
        # Do not start risk that cannot fit the original owner's remaining
        # lease even at every configured quote/retirement deadline. This is a
        # derived bound, not a new implicit financial calibration or renewal.
        required_ms = ((remaining + 1) * self._trading_model.quote_schedule_timeout_ms
                       + self.makers.schedule.refresh_interval_ms + self.limits.shutdown_timeout_ms)
        return self._trading_ready() and (self.journal.owner.expires_at - utc(self.clock())).total_seconds() * 1000 > required_ms

    def start_makers(self):
        self._original()
        if self.makers is None or not self._trading_ready():
            raise StorageError('PG_SERVICE_MAKER_SCHEDULE_REQUIRED')
        if self._quote_task is not None or self.hedge._busy:
            raise StorageError('PG_SERVICE_QUOTE_BACKPRESSURE')
        if self.runner.executor_orchestrator.active_executors[self.config.id]:
            raise StorageError('PG_SERVICE_MAKER_START_REQUIRES_RECONCILIATION')
        task = self.makers.start()

        def completed(result):
            if result.cancelled() or result.exception() is not None:
                self.revoke()
        task.add_done_callback(completed)
        return task

    async def stop_makers(self):
        if self.makers is not None:
            await self.makers.stop()

    async def retire_unprepared_birth(self, retirement_id, ticket_id):
        """Stop local risk and permanently retire one unconsumed birth.

        A concurrent original prepare wins or loses at the primary account
        lock. This explicit recovery command cannot release capital, renew a
        ticket, adopt an executor or reactivate this service.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository

        self._original()
        self.revoke()  # Before awaiting the primary, including on replay.
        births = self.journal.births or PgQuoteBirthRepository(self.financial)
        existing = await births.lookup_retirement(retirement_id)
        if existing is not None:
            if existing.ticket_id != ticket_id:
                raise StorageError('PG_OPERATION_CONFLICT')
            return existing
        return await births.retire(retirement_id, ticket_id,
                                   cut=(await self.financial.snapshot(self.runtime.config.binding)).cut,
                                   owner=self.journal.owner)

    async def release_unused_claim(self, operation_id, ticket_id):
        """Explicit stopped-service recovery; never reactivates local risk."""
        self._original()
        self.revoke()
        return await self.cancellation.release.release_unused(operation_id, ticket_id)

    def _maker_admission(self):
        if (self.makers is not None and self.makers.task is not None and not self.makers.task.done()
                and asyncio.current_task() is not self.makers.task):
            raise StorageError('PG_SERVICE_MAKER_RUNNING')

    async def quote(self, intent_id, side, level):
        """One serialized configured quote through the actual V2 dispatcher."""
        self._original()
        self._maker_admission()
        if not self._trading_ready() or self.quote_application is None:
            raise StorageError('PG_SERVICE_TRADING_NOT_QUALIFIED')
        if self._quote_task is not None or self.hedge._busy:
            raise StorageError('PG_SERVICE_QUOTE_BACKPRESSURE')
        self._quote_task = asyncio.current_task()
        try:
            async with asyncio.timeout(self._trading_model.quote_schedule_timeout_ms / 1000), AsyncExitStack() as stack:
                await self.events.drain()
                await self.reporting.drain()
                if await self._read_trading_config() != self._trading_model:
                    raise StorageError('PG_SERVICE_TRADING_CONFIG_CHANGED')
                intent = await self.producer.propose(intent_id, side, level)
                allocated = await self.hedge.allocate_spot('service-allocate:' + fingerprint([intent_id]), intent,
                                                           require_hedge=self._trading_model.hedge_enabled)
                if not allocated.capital.decision.allowed or allocated.carry is None or not allocated.carry.decision.allowed:
                    raise StorageError('PG_SERVICE_CAPITAL_OR_CARRY_DENIED')
                if self._trading_model.hedge_enabled and (allocated.hedge is None or allocated.hedge.decision['allow_spot_risk_increase'] is not True):
                    raise StorageError('PG_SERVICE_HEDGE_SPOT_PAUSED')
                publication_id = 'service-publish:' + fingerprint([intent_id])
                publication = await self.journal.preflight_publication(publication_id)
                if self.quote_source._quota is not None:
                    await stack.enter_async_context(self.quote_source._quota.spot_cycle(self.quote_source, intent))
                if any(m.version == 98 for m in self.runtime._bundle):
                    birth_model = dict(_model_hash=self.producer._configuration_hash) if self.provisional_quotes else {}
                    action = await self.quote_source.capture_and_publish_intent_ready(intent, publication_id, _publication=publication, **birth_model)
                else:
                    # Explicit predecessor-schema qualification only. Current
                    # packages require migration 98 in every primary admission.
                    action = await self.quote_source.capture_intent_ready(intent)
                    await self.journal.publish(publication_id, (action,), _publication=publication)
                self._quote_action = action.model_copy(deep=True)
                self.runner._async_action_dispatcher.submit([action])
                await self.runner._async_action_dispatcher.drain()
                entry = self.journal._entry(action)
                if not entry.accepted and not (entry.birth is not None and entry.dispatch_facts is not None and not entry.revoked):
                    raise StorageError('PG_SERVICE_DISPATCH_REJECTED')
                # The real executor's control loop submits; no manual place call.
                executors = self.runner.executor_orchestrator.active_executors[self.config.id]
                executor = next(v for v in executors if v.config.id == intent_id)
                while executor._order is None and executor.is_active:
                    # The executor's control loop reports protected-submit
                    # failures asynchronously. A revoked journal can no longer
                    # submit; waiting for an order until the schedule expires
                    # would hide that original refusal behind a timeout.
                    if not self.journal.local_runner_create_fence(action):
                        raise StorageError('PG_SERVICE_EXECUTOR_ORIGIN_REVOKED')
                    if self.quote_application._failed:
                        await self.quote_application.drain()
                    await asyncio.sleep(.001)
                if executor._order is None:
                    raise StorageError('PG_SERVICE_EXECUTOR_NOT_SUBMITTED')
                await self.quote_application.drain()
                # The original guarded request/ACK has finished. Observation
                # workers and unrelated SDK callbacks need their own normal
                # collection windows; they must not wait on this quote while
                # this caller waits for their accounting below.
                await stack.aclose()
                await self.events.drain()
                await self.reporting.flush_deferred()
                await self.reporting.drain()
                return action.model_copy(deep=True)
        except BaseException as error:
            if self._quote_action is not None:
                error.add_note('original_quote_age_ms=' + str(round(
                    (utc(self.clock()).timestamp() - self._quote_action.executor_config.timestamp) * 1000, 3)))
            self.revoke()
            raise
        finally:
            self._quote_task = self._quote_action = None

    def _pause(self):
        self._original()
        if self.native_trading and self._quote_task is not asyncio.current_task():
            raise StorageError('PG_SERVICE_CYCLE_ORIGIN_REQUIRED')
        self._paused = True
        for executor in self._owned_executors.values():
            if executor.status is not RunnableStatus.TERMINATED:
                executor.stop()
        return True

    def _pause_allocation(self):
        # The owning quote cycle already serializes new creations. Existing
        # makers keep their original executor/SDK identity while native signed
        # source checks account for every hold. Hedge/cancel still use _pause.
        self._original()
        if self._quote_task is not asyncio.current_task() or self._quote_action is not None:
            raise StorageError('PG_SERVICE_ALLOCATION_ORIGIN_REQUIRED')
        self._paused = True
        return True

    async def _join_maker_events(self):
        if self.events is not None:
            await self.events.drain()
        if self.reporting is not None:
            await self.reporting.flush_deferred()
            await self.reporting.drain()

    async def _retired_maker_history(self, intent_ids):
        """Original native retirement readback only; never select new targets."""
        async def read(c):
            return await c.fetchrow('''SELECT life_financial.spot_state($1) AS snapshot,
                (SELECT jsonb_agg(jsonb_build_object('intent_id',t.intent_id,'state',t.state,
                    'retired',r.account_id IS NOT NULL,'action_state',q.state))
                 FROM life_financial.send_attempts t
                 LEFT JOIN life_financial.shared_claim_releases r USING(account_id,intent_id)
                 LEFT JOIN life_financial.quote_actions q ON q.account_id=t.account_id AND q.action_id=t.intent_id
                 WHERE t.account_id=$1 AND t.product='SPOT' AND t.intent_id=ANY($2::text[])) AS intents''',
                                    self.runtime.config.account_id, list(intent_ids))
        value = await self.runtime._admit(read, safety=True)
        self.financial._snapshot(value['snapshot'], self.config.storage_binding)
        if value['snapshot']['unverified']:
            raise StorageError('PG_SERVICE_MAKER_HISTORY_UNVERIFIED')
        rows = value['intents'] or []
        return (len(rows) == len(intent_ids) and {r['intent_id'] for r in rows} == set(intent_ids)
                and all(r['state'] == 'TERMINAL' and r['retired'] is True and r['action_state'] == 'RECONCILED' for r in rows))

    async def cancel_makers(self, intent_ids):
        """Retire only this operation's original makers after signed closure.

        Completed local replay returns its original identity set and cannot
        cancel replacement makers. An interrupted operation requires native
        reconciliation; it never retries a cancellation or refunds a claim.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import (
            MeasurePortfolio,
            PgPortfolioRepository,
        )
        from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction
        self._original()
        self._maker_admission()
        if (type(intent_ids) is not tuple or not 1 <= len(intent_ids) <= self.limits.max_cancels
                or any(type(v) is not str for v in intent_ids)
                or len(set(intent_ids)) != len(intent_ids)):
            raise StorageError('PG_SERVICE_MAKER_TARGET_SCOPE')
        for intent_id in intent_ids:
            identifier(intent_id)
        targets = tuple(sorted(intent_ids))
        # Content-address the immutable target set. A caller cannot reuse a
        # human operation name after restart to target replacement makers.
        operation_id = 'service-maker:' + fingerprint([self.config.storage_binding.json(), self.journal.owner.json(), list(targets)])
        if operation_id in self._maker_operations:
            completed = self._maker_operations[operation_id]
            if completed is None:
                raise StorageError('PG_SERVICE_MAKER_RECONCILIATION_REQUIRED')
            return completed
        if not self._trading_ready() or self.quote_application is None:
            raise StorageError('PG_SERVICE_TRADING_NOT_QUALIFIED')
        if self._quote_task is not None or self.hedge._busy:
            raise StorageError('PG_SERVICE_QUOTE_BACKPRESSURE')
        # Reserve the cycle slot before the first primary read. Concurrent
        # quote/cancel calls cannot pass admission while this lookup awaits.
        self._quote_task = asyncio.current_task()
        started = False
        try:
            if await self._retired_maker_history(targets):
                return targets  # Coherent cold historical reply, not a cancel grant.
            current = self.runner.executor_orchestrator.active_executors[self.config.id]
            if tuple(sorted(v.config.id for v in current)) != targets:
                raise StorageError('PG_SERVICE_MAKER_TARGET_SCOPE')
            if len(self._maker_operations) >= self.limits.max_actions:
                raise StorageError('PG_SERVICE_MAKER_OPERATION_BOUND')
            self._maker_operations[operation_id] = None
            started = True
            async with asyncio.timeout(self._trading_model.quote_schedule_timeout_ms / 1000):
                await self.controller.runner_facts()  # Validate all original objects before stopping them.
                executors = tuple(self.runner.executor_orchestrator.active_executors[self.config.id])
                self._pause()
                tasks = tuple(v._control_loop_task for v in executors if getattr(v, '_control_loop_task', None) is not None)
                if tasks:
                    await asyncio.shield(asyncio.gather(*tasks))
                await self.quote_application.drain()
                await self._join_maker_events()
                closed = False
                # Each original maker can need one physical cancel and one
                # terminal confirmation. No wire gets a second cancel attempt.
                for _ in range(2 * self.limits.max_cancels + 1):
                    closed = await self.cancellation.cancel_makers(operation_id, yield_after_request=True, target_intents=targets)
                    await self._join_maker_events()
                    if closed is True:
                        break
                if closed is not True:
                    raise StorageError('PG_SERVICE_MAKER_CLOSURE_REQUIRED')
                # Cancellation ACKs, stopped executors and empty SDK state do
                # not release shared capacity. Refresh the original signed
                # capital/carry source and measure native NAV for each original
                # claim. A sibling's earlier NAV can expire while its signed
                # release completes. Each new intent owns distinct permanent
                # source/NAV IDs and still needs an independent signed release
                # proof and physical COMMIT. Any failure revokes the cycle;
                # neither this stage nor a stale attempt is implicitly retried.
                portfolio = PgPortfolioRepository(self.collector.swap)
                for executor in executors:
                    intent_id = executor.config.id

                    async def terminal(c):
                        return await c.fetchval('''SELECT operation_id FROM life_financial.spot_terminal_facts
                            WHERE account_id=$1 AND intent_id=$2''', self.runtime.config.account_id, intent_id)
                    terminal_id = await self.runtime._admit(terminal, safety=True)
                    if terminal_id is None:
                        raise StorageError('PG_SERVICE_MAKER_TERMINAL_REQUIRED')
                    key = fingerprint([operation_id, intent_id])
                    await self.hedge.observe_sources('service-retire-source:' + key)
                    await portfolio.measure(MeasurePortfolio('service-retire-nav:' + key),
                                            cut=(await self.financial.observation_cut(self.config.storage_binding)).cut,
                                            owner=self.journal.owner)
                    await self.cancellation.release.retire_claim('service-retire:' + key, intent_id, terminal_id)
                # Complete all independently proved financial releases before
                # archival work. Recorder projections and reconciliation can
                # otherwise delay a sibling's financial completion.
                # A failed release still revokes this cycle with its original
                # claims/unknowns retained; archival cannot authorize a release.
                for executor in executors:
                    intent_id = executor.config.id
                    action = CreateExecutorAction(controller_id=self.config.id, executor_config=executor.config.model_copy(deep=True))
                    await self.journal.reconcile(action)
                    self.runner.executor_orchestrator.store_executor(StoreExecutorAction(
                        controller_id=self.config.id, executor_id=intent_id))
                    await self.runner.executor_orchestrator.drain_repository_reporting()
                if self.runner.executor_orchestrator.active_executors[self.config.id]:
                    raise StorageError('PG_SERVICE_MAKER_REPORT_PENDING')
                result = tuple(v.config.id for v in executors)
                self._maker_operations[operation_id] = result
                return result
        except BaseException:
            if started:
                self.revoke()
            raise
        finally:
            self._quote_task = None

    async def reload_yaml(self, *args, **kwargs):
        self.revoke()
        raise StorageError('PG_SERVICE_RELOAD_NOT_QUALIFIED')

    def revoke(self):
        if not self._revoked:
            self._revoked = True
            if self.makers is not None:
                self.makers.request_stop()
            if self.runtime is not None:
                self.runtime._healthy = False
                self.runtime._ingress_epoch += 1
            for component in (self.events, self.hedge):
                if component is not None:
                    component.stop()
            if self.quote_application is not None:
                self.quote_application.stop()
            if self.runner is not None:
                self.runner._is_stop_triggered = True
                for executor in self._owned_executors.values():
                    if executor.status is not RunnableStatus.TERMINATED:
                        executor.stop()
            journal = getattr(self, 'journal', None)
            if journal is not None:
                journal.revoke()

    async def close(self):
        self.revoke()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
            # A caller may time out or be cancelled before physical cleanup
            # finishes. Observe late failure while retaining it on the original
            # task for every subsequent join; never abandon or cancel COMMIT.
            self._close_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        async with asyncio.timeout(self._limits.shutdown_timeout_ms / 1000):
            await self.wait_closed()

    async def wait_closed(self):
        """Join already-started physical cleanup, retaining its original error.

        This explicit lifecycle join has no new cleanup attempt or deadline.
        Resource owners must finish it before destroying SDK/DB dependencies;
        cancelling a waiter cannot cancel the owned cleanup task.
        """
        task = self._close_task
        if task is None:
            raise StorageError('PG_SERVICE_CLOSE_NOT_STARTED')
        await asyncio.shield(task)

    async def _close(self):
        failures = []
        controller_id = json.loads(self._config_json)['id']

        async def join(work):
            try:
                await work
            except BaseException as error:
                failures.append(error)

        async def runner_close():
            owned = {self._quote_task}
            if self.makers is not None:
                owned.add(self.makers.task)
            owned.discard(None)
            if owned:
                await asyncio.shield(asyncio.gather(*owned, return_exceptions=True))
            if self.runner is None:
                return
            self.runner._is_stop_triggered = True
            listener = self.runner.listen_to_executor_actions_task
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            await self.runner._async_action_dispatcher.close()
            executors = tuple(self._owned_executors.values())
            for executor in executors:
                if executor.status is not RunnableStatus.TERMINATED:
                    executor.stop()
            tasks = tuple(v._control_loop_task for v in executors if getattr(v, '_control_loop_task', None) is not None)
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
            current = tuple(self.runner.executor_orchestrator.active_executors[controller_id])
            original_ids = {id(executor) for executor in executors}
            for executor in current:
                # Foreign runner entries remain unqualified evidence; neither
                # inspect their payloads nor adopt them into owned reporting.
                if id(executor) in original_ids:
                    self.reporting.enqueue(executor.executor_info)

        async def controller_close():
            self.controller.stop()

        # Caller deadlines remain bounded in close(). This one owned physical
        # cleanup retains SDK/DB resources until late original ACK/report work
        # actually ends, even if a component returns its own timeout/error.
        await join(runner_close())
        await join(controller_close())
        if self.quote_application is not None:
            await join(self.quote_application.close())
            tasks = (*self.quote_application._pending.values(), *self.quote_application._physical.values())
            if tasks:
                await join(asyncio.shield(asyncio.gather(*tasks, return_exceptions=True)))
        if self.hedge is not None:
            await join(self.hedge.close(shutdown_timeout_ms=self._limits.shutdown_timeout_ms))
            await join(self.hedge._idle.wait())
            if self.hedge._physical:
                await join(asyncio.shield(asyncio.gather(*self.hedge._physical, return_exceptions=True)))
        if self.reporting is not None:
            await join(self.reporting.close())
            event_task = None if self.events is None else self.events._task
            if event_task is not None:
                await join(asyncio.shield(asyncio.gather(event_task, return_exceptions=True)))
        if self.financial is not None:
            await join(self.financial.close())
        elif self.runtime is not None:
            await join(self.runtime.close())
        if failures:
            raise failures[0]
