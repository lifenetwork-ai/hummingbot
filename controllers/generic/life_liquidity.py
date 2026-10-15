"""LIFE V2 controller adapter for configuration discovery and offline validation.

The trading controller is deliberately inert until the P2–P9 data, accounting,
order, and release gates are implemented. Loading this module cannot place orders.
"""

import asyncio
import time
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Callable, Literal

from pydantic import Field

from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryMonitor
from hummingbot.strategy_v2.life_liquidity.config import ConfigUpdateState, StrategyConfig, parse_duration_seconds
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.extension_telemetry import collect_extension_metrics
from hummingbot.strategy_v2.life_liquidity.fees import CachedFeeRateSource
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.hedge import HedgeObservation, HedgePolicy
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
from hummingbot.strategy_v2.life_liquidity.joint_exposure import (
    JointExposureObservation,
    JointRiskLimits,
    LinearLifeContractSpec,
)
from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger, LossBudgetStatus
from hummingbot.strategy_v2.life_liquidity.market_data import (
    BenchmarkConnectorRoute,
    BookContinuityGate,
    ContinuousTradingGate,
    ContractValidationError,
    LinearSwapContract,
    ListingGate,
    OkxInstrumentSource,
    SnapshotQualityGate,
    is_order_book_ready,
)
from hummingbot.strategy_v2.life_liquidity.markout_risk import MarkoutProbeGuard, ReconciledMarkoutMonitor
from hummingbot.strategy_v2.life_liquidity.order_gateway import CancelRetryPolicy, OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnDepthDecision
from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner
from hummingbot.strategy_v2.life_liquidity.recovery_probe import RecoveryProbeGuard
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.session import SessionManager
from hummingbot.strategy_v2.life_liquidity.shared_capital import SharedCapitalAuthority
from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotRiskBinding
from hummingbot.strategy_v2.life_liquidity.spot_telemetry import collect_quote_inputs, collect_spot_metrics
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.file_controller import FileControllerCompatibility
from hummingbot.strategy_v2.life_liquidity.telemetry import Metric
from hummingbot.strategy_v2.models.base import RunnableStatus

# Synthetic offline guard only. A separately calibrated live book TTL is a P9 decision.
SYNTHETIC_BOOK_MAX_AGE_MS = 2000
SYNTHETIC_FEED_MAX_SILENCE_SECONDS = 65
# Bound retries after metadata becomes available so failed starts do not hammer OKX.
BOOK_BOOTSTRAP_RETRY_BASE_SECONDS = 5
BOOK_BOOTSTRAP_RETRY_MAX_SECONDS = 300


def _simulation_template() -> StrategyConfig:
    """Synthetic CLI create template; never a live allocation or trading policy."""
    return StrategyConfig.model_validate({
        "execution_mode": "simulation",
        "session": {"duration": "4h"},
        "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    })


class LifeLiquidityConfig(ControllerConfigBase):
    controller_type: Literal["generic"] = "generic"
    controller_name: Literal["life_liquidity"] = "life_liquidity"
    storage_backend: Literal['file', 'postgres'] = Field(default='file', json_schema_extra={'is_updatable': False})
    storage_binding: StateBinding | None = Field(default=None, json_schema_extra={'is_updatable': False})
    total_amount_quote: Decimal = Field(
        default=Decimal("0"),
        json_schema_extra={"is_updatable": False},
    )
    strategy: StrategyConfig = Field(
        default_factory=_simulation_template,
        description="Nested settings can be edited in YAML or with hbot config dotted keys; restart to apply.",
        json_schema_extra={"prompt_on_new": False, "is_updatable": False},
    )
    recovery_state_dir: str | None = Field(default=None, json_schema_extra={"is_updatable": False})
    recovery_account_uid: str | None = Field(default=None, json_schema_extra={"is_updatable": False})
    require_quote_action_journal: bool = Field(
        default=False,
        description="Require the account-bound quote action journal during cold-start recovery.",
        json_schema_extra={"is_updatable": False})
    recovery_reconciliation_max_age_ms: int | None = Field(
        default=None, json_schema_extra={"is_updatable": False})
    safety_watchdog_interval_ms: int | None = Field(
        default=None, gt=0,
        description="Explicit safety polling interval; required before a recovery-enabled live deployment.",
        json_schema_extra={"is_updatable": False})
    cancel_retry_interval_ms: int | None = Field(
        default=None, gt=0, description="Minimum UTC interval between cancel attempts for one order (ms).",
        json_schema_extra={"is_updatable": False})
    cancel_max_requests_per_cycle: int | None = Field(
        default=None, gt=0,
        description="Maximum cancel/status REST requests across all scopes in one safety cycle.",
        json_schema_extra={"is_updatable": False})
    own_depth_max_observation_skew_ms: int | None = Field(
        default=None, gt=0, json_schema_extra={"is_updatable": False})
    own_depth_max_book_age_ms: int | None = Field(
        default=None, gt=0, json_schema_extra={"is_updatable": False})
    own_depth_max_distance_bps: Decimal | None = Field(
        default=None, ge=0, lt=10000, json_schema_extra={"is_updatable": False})

    def update_markets(self, markets):
        # P2.9 bootstraps the public LIFE book after listing checks. Benchmarks
        # are public data routes, not order-capable markets.
        return markets


class LifeLiquidityController(FileControllerCompatibility, ControllerBase):
    def __init__(self, config: LifeLiquidityConfig, *args, **kwargs):
        if config.storage_backend != 'file':
            raise ValueError('LIFE_POSTGRES_RUNTIME_NOT_IMPLEMENTED')
        super().__init__(config, *args, **kwargs)
        self.config_update_state = ConfigUpdateState(config.strategy)
        self.benchmark_route = BenchmarkConnectorRoute.from_strategy(config.strategy)
        self.listing_gate = ListingGate(inst_type="SPOT", inst_id=config.strategy.spot.pair)
        self.perpetual_listing_gate = ListingGate(
            inst_type="SWAP", inst_id=f"{config.strategy.perpetual.pair}-SWAP")
        self.perpetual_contract: LinearSwapContract | None = None
        self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
        self._perpetual_poll_task: asyncio.Task | None = None
        self.spot_book_bootstrap_ready = False
        self.spot_book_bootstrap_reason_code = "ORDER_BOOK_BOOTSTRAP_PENDING"
        self.spot_book_bootstrap_failures = 0
        self.spot_book_bootstrap_next_at = 0.0
        self.book_ready = False
        self.continuous_gate = ContinuousTradingGate()
        self.snapshot_gate = SnapshotQualityGate(config.strategy.spot.pair, SYNTHETIC_BOOK_MAX_AGE_MS)
        self.continuity_gate = BookContinuityGate(SYNTHETIC_FEED_MAX_SILENCE_SECONDS)
        self.processed_data = {}
        self._order_safety_manager: SessionManager | None = None
        self._order_safety_gateway: OkxSpotOrderGateway | None = None
        self._order_safety_wal: IntentWAL | None = None
        self._order_safety_reservations: ReservationLedger | None = None
        self._order_safety_account_lock: AccountRiskPoolLock | None = None
        self._account_uid_verified = False
        self._release_account_lock_on_task_done = False
        self._order_safety_stopped = False
        self._storage_runtime = None
        self._telemetry = None
        self._telemetry_value = None
        self._telemetry_utc_ms = None
        self._telemetry_max_value_age_ms = None
        self._telemetry_rejects = 0
        self._telemetry_sent_ms = {}
        self._telemetry_cancel_ms = {}
        self._telemetry_recording = False
        self._permission_gates = {}
        self.telemetry_reason_code = "TELEMETRY_NOT_INSTALLED"
        self._safety_rearm_verified = False
        self._safety_rearm_in_progress = False
        self._runner_orchestrator = None
        self._runner_halt_ok = False
        self._runner_wire_owners: dict[str, str] = {}
        self._runner_stored_executor_ids: set[str] = set()
        self._runner_scope_invalid = False
        self._runner_fill_observations: dict[str, tuple] = {}
        self._runner_cancel_observations: dict[str, str | None] = {}
        self._own_depth_decision = OwnDepthDecision(None, "OWN_DEPTH_NOT_EVALUATED")
        self._protected_spot_sender: ProtectedSpotExecutorSender | None = None
        self._protected_swap_sender: ProtectedSwapExecutorSender | None = None
        self._shared_capital_authority: SharedCapitalAuthority | None = None
        self._carry_monitor: CarryMonitor | None = None
        self._joint_recovery: JointRecovery | None = None
        self._joint_recovery_restore_required = False
        self._joint_reconciliation_task: asyncio.Task | None = None
        self.carry_reason_code = "CARRY_NOT_INSTALLED"
        self._hedge_coordinator: HedgeCoordinator | None = None
        self._quote_action_planner: QuoteActionPlanner | None = None
        self._runtime_risk_gate: SafetyGate | None = None
        self._runtime_risk_observation: Callable[[], SafetyObservation] | None = None
        self._runtime_risk_clock_ms: Callable[[], int] | None = None
        self._runtime_risk_max_age_ms: int | None = None
        self.runtime_risk_reason_code = "RUNTIME_RISK_NOT_INSTALLED"
        self._joint_contract: LinearLifeContractSpec | None = None
        self._joint_limits: JointRiskLimits | None = None
        self._joint_observation: Callable[[], JointExposureObservation] | None = None
        self.joint_risk_reason_code = "JOINT_RISK_NOT_INSTALLED"
        self._hedge_policy: HedgePolicy | None = None
        self._hedge_observation: Callable[[], HedgeObservation] | None = None
        self._last_joint_observation: JointExposureObservation | None = None
        self.hedge_reason_code = "HEDGE_GATE_NOT_INSTALLED"
        self._execution_loss_budget: LossBudgetLedger | None = None
        self._execution_loss_utc_clock: Callable[[], datetime] | None = None
        self.execution_loss_status: LossBudgetStatus | None = None
        self.execution_loss_reason_code = "EXECUTION_LOSS_BUDGET_NOT_INSTALLED"
        self._fill_attributor: ReconciledFillAttributor | None = None
        self.fill_attribution_reason_code = "FILL_ATTRIBUTION_NOT_INSTALLED"
        self._capital_risk_monitor: CapitalRiskMonitor | None = None
        self._markout_monitor: ReconciledMarkoutMonitor | None = None
        self._markout_probe_guard: MarkoutProbeGuard | None = None
        self._spot_risk_binding: SpotRiskBinding | None = None
        self._spot_exit_planner = None
        self._inventory_executor = None
        self._runner_stop_revoked_ids: set[str] = set()
        self._fee_rate_cache: CachedFeeRateSource | None = None
        self._fee_refresh_task: asyncio.Task | None = None
        self._recovery_probe_guard: RecoveryProbeGuard | None = None
        self.markout_reason_code = "MARKOUT_NOT_INSTALLED"
        self._quote_action_recovery_ready = False
        self._quote_action_recovery_records = None
        self.quote_action_recovery_reason_code = "QUOTE_ACTION_RECOVERY_NOT_CONFIGURED"
        self.order_safety_task: asyncio.Task | None = None
        self.order_safety_watchdog_task: asyncio.Task | None = None
        self.order_safety_reason_code = "ORDER_SAFETY_NOT_INSTALLED"
        self._pause_reconciliation_required = False
        self._observed_session_state: str | None = None

    def on_runner_order_event_failure(self) -> None:
        self._runner_scope_invalid = True

    def on_runner_order_filled(self, event: OrderFilledEvent) -> None:
        """Hold runner evidence until authenticated exchange reconciliation agrees."""
        if self._storage_runtime is not None and self._storage_runtime.application is not None:
            self._storage_runtime.application.enqueue_runner_event('fill', event)
            return
        self._on_runner_order_filled_sync(event)

    def on_runner_order_canceled(self, event: OrderCancelledEvent) -> None:
        """A tracker cancel event is a hint, never exchange terminal proof."""
        if self._storage_runtime is not None and self._storage_runtime.application is not None:
            self._storage_runtime.application.enqueue_runner_event('cancel', event)
            return
        self._on_runner_order_canceled_sync(event)

    def has_unverified_runner_order_events(self) -> bool:
        return (self._runner_scope_invalid or bool(self._runner_fill_observations)
                or bool(self._runner_cancel_observations))

    def _configured_cancel_retry_policy(self) -> CancelRetryPolicy | None:
        interval = self.config.cancel_retry_interval_ms
        maximum = self.config.cancel_max_requests_per_cycle
        if interval is None or maximum is None:
            return None
        return CancelRetryPolicy(interval, maximum)

    def start(self):
        """Schedule safety separately from the readiness-dependent controller loop."""
        self._order_safety_stopped = False
        super().start()
        existing = self.order_safety_watchdog_task
        if existing is not None and not existing.done() and not existing.cancelling():
            return
        interval_ms = self.config.safety_watchdog_interval_ms
        if (not isinstance(interval_ms, int) or isinstance(interval_ms, bool)
                or interval_ms <= 0):
            if self._order_safety_manager is not None or self.config.recovery_state_dir is not None:
                self.order_safety_reason_code = "ORDER_SAFETY_WATCHDOG_UNCONFIGURED"
            return
        try:
            cancel_policy = self._configured_cancel_retry_policy()
        except ValueError:
            self.order_safety_reason_code = "ORDER_SAFETY_CANCEL_POLICY_INVALID"
            return
        if ((self._order_safety_manager is not None or self.config.recovery_state_dir is not None)
                and cancel_policy is None):
            self.order_safety_reason_code = "ORDER_SAFETY_CANCEL_POLICY_UNCONFIGURED"
            return
        try:
            # ControllerBase.start also schedules its loop before the event
            # loop is necessarily running; use that same loop for safety.
            loop = asyncio.get_event_loop()
        except RuntimeError:
            self.order_safety_reason_code = "ORDER_SAFETY_LOOP_UNAVAILABLE"
            return
        if self._order_safety_gateway is not None:
            # Before the runner supplies its live/stored executor scope we may
            # cancel known WAL orders, but cannot certify reconciliation.
            self._order_safety_gateway.runner_scope_check = self._runner_executor_scope_complete
        self.order_safety_watchdog_task = loop.create_task(
            self._order_safety_watchdog(interval_ms / 1000))
        self.order_safety_watchdog_task.add_done_callback(self._on_order_safety_watchdog_done)

    async def _order_safety_watchdog(self, interval_seconds: float) -> None:
        while not self._order_safety_stopped:
            try:
                self.on_safety_tick(time.monotonic())
            except Exception:
                self.order_safety_reason_code = "ORDER_SAFETY_WATCHDOG_TICK_FAILED"
                self.logger().exception("LIFE order-safety watchdog tick failed")
            await asyncio.sleep(interval_seconds)

    def _on_order_safety_watchdog_done(self, task: asyncio.Task) -> None:
        if (task is self.order_safety_watchdog_task and not self._order_safety_stopped
                and (task.cancelled() or task.exception() is not None)):
            self.order_safety_reason_code = "ORDER_SAFETY_WATCHDOG_FAILED"
            self.logger().error("LIFE order-safety watchdog stopped unexpectedly")

    def update_config(self, new_config: LifeLiquidityConfig):
        service = self._storage_runtime.application if self._storage_runtime is not None else None
        if service is not None:
            return service.update_controller_config(new_config)
        if new_config.storage_backend != self.config.storage_backend or new_config.storage_binding != self.config.storage_binding:
            self.config_update_state.reject('CONFIG_UPDATE_UNSUPPORTED')
            raise ValueError('LIFE storage binding changes require restart')
        if new_config.strategy != self.config.strategy:
            self.config_update_state.reject("CONFIG_UPDATE_UNSUPPORTED")
            raise ValueError("LIFE nested hot reload is unavailable until the controller gate is implemented")
        super().update_config(new_config)

    def on_config_load_failure(self):
        service = self._storage_runtime.application if self._storage_runtime is not None else None
        if service is not None:
            return service.config_load_failed()
        return self.config_update_state.reject("CONFIG_LOAD_FAILED")

    def trading_permissions_ready(self) -> bool:
        # P4 will replace this with the final order-permission checks.
        return False

    @property
    def async_dispatch_enabled(self):
        return self._storage_runtime is not None

    def install_storage_runtime(self, runtime):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend
        if (self._storage_runtime is not None or runtime.owns_controller(self) is not True
                or runtime.backend is not Backend.FILE or runtime.binding != self.config.storage_binding):
            raise ValueError('LIFE_STORAGE_RUNTIME_BINDING_INVALID')
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
        runtime.validate_binding(self.config.storage_binding, fingerprint(self.config.strategy.model_dump(mode='json')))
        runtime.bind_final_authority()
        self._storage_runtime = runtime

    async def async_authorize_runner_create_action(self, action):
        return self._storage_runtime is not None and await self._storage_runtime.authorize_action(action) is True

    async def async_before_runner_dispatch(self, action):
        return self._storage_runtime is not None and await self._storage_runtime.before_dispatch(action) is True

    def local_runner_create_fence(self, action):
        return self._storage_runtime is not None and self._storage_runtime.local_fence(action) is True

    def local_runner_create_preflight(self, action):
        return self._storage_runtime is not None and self._storage_runtime.preflight(action) is True

    def _is_swap_config(self, config) -> bool:
        return getattr(config, "connector_name", None) == self.config.strategy.perpetual.connector

    def manual_kill_switch(self) -> None:
        """Persist an operator HALT and start the existing cancel/reconcile loop."""
        runtime = self._storage_runtime
        if runtime is not None:
            if runtime.application is None:
                raise StorageError('RUNTIME_HALT_APPLICATION_REQUIRED')
            runtime.application.request_halt()
            return
        if self._runtime_risk_gate is None:
            raise ValueError("RUNTIME_RISK_GATE_NOT_INSTALLED")
        try:
            self._runtime_risk_gate.halt("MANUAL_KILL_SWITCH")
        finally:
            self.on_safety_tick(time.monotonic())

    async def manual_rearm(self, *, operator_id: str) -> None:
        """Rearm a clear recovery latch only after fresh account and runner proof.

        This does not clear HALT, resume a session, or enable production trading.
        The existing observation hysteresis and all quote gates still apply.
        """
        if self._storage_runtime is not None:
            raise StorageError('RUNTIME_SIGNED_REARM_REQUIRED')
        gate = self._runtime_risk_gate
        maximum = self.config.recovery_reconciliation_max_age_ms
        if (gate is None or not gate.rearm_required or gate.state == "HALTED"
                or not isinstance(operator_id, str) or not operator_id.strip()
                or self._safety_rearm_in_progress or self._order_safety_stopped
                or not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0
                or self.order_safety_task is not None and not self.order_safety_task.done()):
            raise ValueError("SAFETY_REARM_PROOF_REQUIRED")
        self._safety_rearm_in_progress = True
        try:
            await asyncio.wait_for(self._reconcile_for_manual_rearm(), maximum / 1000)
            if self._joint_recovery is not None:
                if not await self._joint_recovery.reconcile():
                    raise ValueError("JOINT_REARM_PROOF_REQUIRED")
                self._joint_recovery.manual_rearm(operator_id=operator_id)
            self._safety_rearm_verified = True
            gate.arm_after_reconciliation(reconciled=True, operator_id=operator_id)
            self.runtime_risk_reason_code = "STARTUP_REVALIDATION"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ValueError("SAFETY_REARM_PROOF_REQUIRED") from exc
        finally:
            self._safety_rearm_verified = False
            self._safety_rearm_in_progress = False

    def _recovery_probe_policy_matches(self) -> bool:
        guard, gate = self._recovery_probe_guard, self._runtime_risk_gate
        try:
            policy = self.config.strategy.risk.resume_policy
            return (guard is not None and gate is not None
                    and guard.max_quote_base == policy.probe_size_base == gate.recovery_probe_base
                    and parse_duration_seconds(policy.stable_data_duration) * 1000 == gate.stable_data_ms)
        except (ValueError, TypeError, AttributeError):
            return False

    def recovery_probe_active(self) -> bool:
        return (self._runtime_risk_gate is not None and self._runtime_risk_gate.state == "DEGRADED"
                and self._recovery_probe_policy_matches())

    async def settle_service_inventory(self, cycle_id: str, intent_ids: tuple[str, ...]) -> bool:
        """Requery exchange/account scope before releasing a closed inventory hold."""
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return await runtime.application.settle_inventory(cycle_id, intent_ids)
        attributor, gateway = self._fill_attributor, self._order_safety_gateway
        if (self.config.strategy.economics.objective != "liquidity_service"
                or attributor is None or attributor.subsidy_budget is None or gateway is None
                or not isinstance(intent_ids, tuple) or not intent_ids
                or any(not isinstance(key, str) or not key for key in intent_ids)
                or len(set(intent_ids)) != len(intent_ids)):
            raise ValueError("SUBSIDY_CYCLE_EVIDENCE_UNAVAILABLE")
        scopes = {(attributor.wal.get(key).session_id, attributor.wal.get(key).epoch)
                  for key in intent_ids}
        for session_id, epoch in sorted(scopes):
            result = await gateway.reconcile(session_id, epoch)
            if (not result.scope_complete or not result.trade_events_reconciled
                    or result.open_order_ids or result.pending_cancel_ids or result.unknown_order_ids):
                raise ValueError("SUBSIDY_ACCOUNT_SCOPE_UNAVAILABLE")
        return attributor.settle_inventory_cycle(cycle_id, intent_ids)

    def bounded_spot_planner(self, config):
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.planner_for(config)
        if self._inventory_executor is not None and self._inventory_executor.handles(config):
            return self._inventory_executor
        if self._spot_exit_planner is not None and self._spot_exit_planner.handles(config):
            return self._spot_exit_planner
        return None

    def is_risk_reducing_config(self, config) -> bool:
        return self.bounded_spot_planner(config) is not None and config.side == TradeType.SELL

    def on_runner_stop_action(self, action) -> None:
        """Revoke an already dispatched intent before the runner stops its executor."""
        if self._storage_runtime is not None and self._storage_runtime.application is not None:
            self._storage_runtime.application.stop_local(action)
            return
        if (self._storage_runtime is not None and action.controller_id == self.config.id
                and self._storage_runtime.cancel_spot_local(action.executor_id)):
            return
        sender = self._protected_swap_sender
        if sender is not None and action.controller_id == self.config.id and sender.owns_intent(action.executor_id):
            self._runner_stop_revoked_ids.add(action.executor_id)
            sender.revoke(action.executor_id)
            return
        wal = self._order_safety_wal
        if wal is None or action.controller_id != self.config.id:
            return
        try:
            record = wal.get(action.executor_id)
        except KeyError:
            return
        self._runner_stop_revoked_ids.add(record.intent_id)
        # In-memory revocation precedes either potentially failing checkpoint.
        wal.mark_cancel_requested(record.intent_id)
        if self._order_safety_reservations is not None:
            self._order_safety_reservations.request_cancel(record.intent_id)

    def suppress_create_for_stop_batch(self) -> bool:
        """A LIFE cancellation request takes priority over new risk in one runner batch."""
        return True

    def on_runner_create_action_rejected(self, action) -> bool:
        if self._storage_runtime is not None and self._storage_runtime.application is not None:
            return self._storage_runtime.application.rejected(action)
        if self._storage_runtime is not None:
            self._storage_runtime.forget_action(action.executor_config.id)
        self._record_telemetry("QUEUE_REJECT", allowed=False, reason_code="RUNNER_ACTION_REJECTED",
                               intent_id=getattr(action.executor_config, "id", None))
        if self._is_swap_config(action.executor_config):
            if self._protected_swap_sender is not None:
                self._protected_swap_sender.revoke(action.executor_config.id)
            return False
        if self.bounded_spot_planner(action.executor_config) is not None:
            return self.bounded_spot_planner(action.executor_config).action_transition(action, "REJECTED")
        planner = self._quote_action_planner
        return planner.on_runner_action_rejected(action) if planner is not None else False

    def on_runner_create_action_dispatched(self, action) -> bool:
        if self._storage_runtime is not None and self._storage_runtime.application is not None:
            return self._storage_runtime.application.dispatched(action)
        if self._storage_runtime is not None:
            self._storage_runtime.forget_action(action.executor_config.id)
        self._record_telemetry("QUEUE_DISPATCH", allowed=True, reason_code="RUNNER_ACTION_DISPATCHED",
                               intent_id=getattr(action.executor_config, "id", None))
        if self._is_swap_config(action.executor_config):
            return self._protected_swap_sender is not None and self._protected_swap_sender.authorizes_config(action.executor_config)
        if self.bounded_spot_planner(action.executor_config) is not None:
            return self.bounded_spot_planner(action.executor_config).action_transition(action, "DISPATCHED")
        planner = self._quote_action_planner
        return planner.on_runner_action_dispatched(action) if planner is not None else False

    def submit_executor_spot_order(self, config, *, amount: Decimal,
                                   price: Decimal, order_type) -> str:
        # Historical runner hook name; SWAP orders must use their own permit.
        if (not self._is_swap_config(config) and self._storage_runtime is not None
                and self._storage_runtime.handles_financial_spot):
            return self._storage_runtime.submit_spot(config, amount=amount, price=price, order_type=order_type)
        if self._is_swap_config(config):
            if self._protected_swap_sender is None:
                raise PermissionError("LIFE_SWAP_TRADING_DISABLED")
            return self._protected_swap_sender.submit(config, amount=amount, price=price, order_type=order_type)
        sender = self._protected_spot_sender
        exit_order = self.bounded_spot_planner(config) is not None
        allowed = (self.bounded_spot_planner(config).authorizes_config(config) if exit_order
                   else self.allow_create_executor_actions())
        if sender is None or allowed is not True:
            raise PermissionError("LIFE_TRADING_DISABLED")
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            raise PermissionError("QUOTE_ACTION_RECOVERY_UNVERIFIED")
        planner = self._quote_action_planner
        if (planner is not None and not exit_order
                and (not planner.authorizes_config(config)
                     or amount != config.amount or price != config.price
                     or order_type != OrderType.LIMIT_MAKER)):
            raise PermissionError("QUOTE_ACTION_NOT_AUTHORIZED")
        return sender.submit(config, amount=amount, price=price, order_type=order_type)

    def _runner_executors(self):
        active = getattr(self._runner_orchestrator, "active_executors", None)
        if not isinstance(active, Mapping):
            return None
        executors = active.get(self.config.id, [])
        return tuple(executors) if isinstance(executors, (list, tuple)) else None

    def _halt_runner_orders(self, *, spot_only=False) -> bool:
        """Revoke executor renewals before starting exchange-side cancellation."""
        from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor

        executors = self._runner_executors()
        if executors is None:
            self._runner_scope_invalid = True
            return False
        safe = True
        for executor in executors:
            if not isinstance(executor, OrderExecutor):
                safe = False
                self._runner_scope_invalid = True
                continue
            if spot_only and self._is_swap_config(executor.config):
                continue
            try:
                if executor.status in (RunnableStatus.NOT_STARTED, RunnableStatus.RUNNING):
                    executor.early_stop()
                if executor.status not in (RunnableStatus.SHUTTING_DOWN, RunnableStatus.TERMINATED):
                    safe = False
                if (not isinstance(executor.config.id, str) or not executor.config.id
                        or executor.config.controller_id != self.config.id
                        or not self._runner_product_config_valid(executor.config)):
                    safe = False
                for wire_id in executor.recovery_order_ids():
                    owner = self._runner_wire_owners.get(wire_id)
                    if owner is not None and owner != executor.config.id:
                        safe = False
                    self._runner_wire_owners[wire_id] = executor.config.id
            except Exception:
                safe = False
                self.logger().exception("LIFE executor safety stop failed")
        if not safe:
            self._runner_scope_invalid = True
        return safe

    def _runner_product_config_valid(self, config):
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.recognizes_config(config)
        if (config.connector_name == self.config.strategy.spot.connector
                and config.trading_pair == self.config.strategy.spot.pair):
            return True
        if self._joint_recovery is None or self._protected_swap_sender is None:
            return False
        try:
            self._protected_swap_sender._validate(config)
            with self._protected_swap_sender.journal.locked() as state:
                return state["actions"][config.id]["config"] == config.model_dump(mode="json")
        except Exception:
            return False

    def on_runner_safety_tick(self, timestamp: float, orchestrator) -> None:
        """Attach the real executor scope before the readiness-independent safety tick."""
        self._runner_orchestrator = orchestrator
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            runtime.application.on_runner_safety_tick(timestamp)
            return
        try:
            permitted = self.trading_permissions_ready() is True
        except Exception:
            permitted = False
        manager = self._order_safety_manager
        self._runner_halt_ok = False
        exit_ready = self._bounded_pending_ready()
        if (manager is None or self._order_safety_gateway is None
                or manager.state != "ACTIVE" and not exit_ready or not permitted):
            self._runner_halt_ok = self._halt_runner_orders()
        if self._order_safety_gateway is not None:
            self._order_safety_gateway.runner_scope_check = self._runner_executor_scope_complete
        self.on_safety_tick(timestamp)

    def on_safety_tick(self, timestamp: float) -> None:
        """Persist expiry and run order cancellation independent of quote readiness."""
        if self._order_safety_stopped:
            return
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            runtime.application.on_safety_tick(timestamp)
            return
        if runtime is not None and runtime.handles_financial_spot:
            runtime.on_spot_safety_tick(timestamp)
            return
        if self._inventory_executor is not None:
            self._inventory_executor.on_safety_tick()
        # Do not short-circuit this observation on quote readiness or session expiry.
        carry_ready = self._carry_ready()
        if (self._joint_recovery is not None and self._joint_recovery.reconciliation_due()
                and (self.order_safety_task is None or self.order_safety_task.done())
                and (self._joint_reconciliation_task is None or self._joint_reconciliation_task.done())):
            try:
                self._joint_reconciliation_task = asyncio.get_running_loop().create_task(self._joint_recovery.reconcile())
            except RuntimeError:
                pass  # Allocation still requires a fresh completed joint receipt.
        if self._fee_rate_cache is not None and (
                self._fee_refresh_task is None or self._fee_refresh_task.done()):
            try:
                self._fee_refresh_task = asyncio.get_running_loop().create_task(
                    self._fee_rate_cache.refresh_if_due())
            except RuntimeError:
                pass  # An unavailable/overdue cache remains unqualified.
        manager = self._order_safety_manager
        if manager is None:
            if self.config.recovery_state_dir is not None:
                try:
                    self._restore_order_safety()
                except AccountLockUnavailable as exc:
                    self.order_safety_reason_code = str(exc)
                    return
                except Exception:
                    self.order_safety_reason_code = "ORDER_SAFETY_RECOVERY_FAILED"
                    self.logger().exception("LIFE order-safety recovery failed")
                    return
                manager = self._order_safety_manager
            else:
                return
        try:
            if manager.state == "PAUSED" and self._observed_session_state != "PAUSED":
                self._pause_reconciliation_required = True
                self.order_safety_reason_code = "RECONCILIATION_REQUIRED"
            hedge_ready = self._hedge_ready()
            # A qualified coordinator drains spot risk and authorizes its own
            # bounded hedge. The parent session still enforces all hard gates.
            risk_ready = (self._runtime_risk_ready() and self._spot_risk_ready() and self._joint_risk_ready()
                          and (self._hedge_coordinator is not None or hedge_ready) and carry_ready)
            loss_ready = risk_ready and self._execution_loss_ready()
            accounting_ready = loss_ready and self._fill_attribution_ready()
            markout_ready = accounting_ready and self._markout_risk_ready()
            planner = self._quote_action_planner
            qualified = planner.session_snapshot() if markout_ready and planner is not None else None
            stop_pending = self._runner_stops_pending()
            ready = (not stop_pending and markout_ready and qualified is not None and self._spot_quote_gates_ready()
                     and not self._pause_reconciliation_required)
            exit_ready = not stop_pending and self._bounded_pending_ready()
            previous_state = manager.state
            manager.tick(reference_ready=ready or exit_ready, all_gates_ready=ready or exit_ready,
                         market_reference_ready=(exit_ready or ready and qualified.market_reference_ready is True))
            if previous_state == "ACTIVE" and manager.state == "PAUSED":
                self._pause_reconciliation_required = True
                self.order_safety_reason_code = "RECONCILIATION_REQUIRED"
            self._observed_session_state = manager.state
        except Exception:
            self.order_safety_reason_code = "SESSION_SAFETY_TICK_FAILED"
            self.logger().exception("LIFE safety session tick failed")
            return
        if (self._runner_orchestrator is not None and self._order_safety_gateway is not None
                and manager.state in ("PAUSED", "EXPIRED", "TRANSITIONING")):
            if not self._runner_halt_ok:
                self._runner_halt_ok = self._halt_runner_orders()
            self._order_safety_gateway.runner_scope_check = self._runner_executor_scope_complete
        if self.order_safety_task is not None and not self.order_safety_task.done():
            return
        hedge_drain = manager.state == "ACTIVE" and self._hedge_coordinator is not None and not hedge_ready
        if hedge_drain:
            self._halt_runner_orders(spot_only=True)
        if manager.state not in ("PAUSED", "EXPIRED", "TRANSITIONING"):
            if not hedge_drain:
                return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.order_safety_reason_code = "ORDER_SAFETY_LOOP_UNAVAILABLE"
            return
        self.order_safety_task = loop.create_task(self._cancel_and_reconcile_orders(spot_only=hedge_drain))
        self.order_safety_task.add_done_callback(self._on_order_safety_done)

    def _on_order_safety_done(self, task: asyncio.Task) -> None:
        self._release_account_lock_when_quiescent()
        if task.cancelled():
            self.order_safety_reason_code = "ORDER_SAFETY_CANCELLED"
            return
        failure = task.exception()
        if failure is not None:
            self.order_safety_reason_code = "ORDER_SAFETY_FAILED"
            self.logger().error("LIFE safety order task failed", exc_info=(
                type(failure), failure, failure.__traceback__))

    async def _cancel_and_reconcile_orders(self, *, spot_only=False) -> None:
        if self._storage_runtime is not None:
            raise StorageError('RUNTIME_REPOSITORY_RECONCILIATION_REQUIRED')
        manager = self._order_safety_manager
        gateway = self._order_safety_gateway
        wal = self._order_safety_wal
        if self._order_safety_account_lock is not None and not self._account_uid_verified:
            try:
                observed_uid = await gateway.connector.get_account_uid()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.order_safety_reason_code = "ACCOUNT_UID_UNVERIFIED"
                self.logger().exception("LIFE account UID verification failed")
                return
            if observed_uid != self.config.recovery_account_uid:
                self.order_safety_reason_code = "ACCOUNT_UID_MISMATCH"
                return
            self._account_uid_verified = True
        current = manager.current_session
        scopes = {(record.session_id, record.epoch) for record in wal.active_records()}
        scopes.add((current.session_id, current.epoch))

        async def cancel_spot():
            if gateway.cancel_retry_policy is not None:
                await gateway.request_cancel_scopes(tuple(sorted(scopes)))
            else:
                for session_id, epoch in sorted(scopes):
                    await gateway.request_cancel(session_id, epoch)

        async def cancel_swaps():
            if not spot_only:
                await asyncio.wait_for(self._joint_recovery.cancel_working_swaps(),
                                       self._joint_recovery.max_age_ms / 1000)

        joint_ready = True
        if self._joint_recovery is not None:
            # Each product gets transport time independently; joint proof still
            # controls release. The configured freshness deadline bounds I/O.
            async def bounded_spot():
                async with asyncio.timeout(self._joint_recovery.max_age_ms / 1000):
                    await cancel_spot()

            canceled, swap_result = await asyncio.gather(bounded_spot(), cancel_swaps(), return_exceptions=True)
            # Full history qualification is synchronous work. Start both
            # products' cancel transports before it can occupy the event loop.
            joint_ready = (not isinstance(swap_result, BaseException)
                           and await self._joint_recovery.reconcile() is True)
        else:
            try:
                await cancel_spot()
                canceled = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                canceled = exc
        bounded_cancel_failed = isinstance(canceled, BaseException)
        if not joint_ready:
            self.order_safety_reason_code = "JOINT_RECONCILIATION_UNAVAILABLE"
        primary_result = None
        reason = "OLD_ORDERS_RECONCILED" if joint_ready else "JOINT_RECONCILIATION_UNAVAILABLE"
        for session_id, epoch in sorted(scopes):
            cancel_failed = bounded_cancel_failed
            try:
                fetch = gateway.reconcile(session_id, epoch)
                result = (await asyncio.wait_for(fetch, self._joint_recovery.max_age_ms / 1000)
                          if self._joint_recovery is not None else await fetch)
            except asyncio.CancelledError:
                raise
            except Exception:
                reason = "RECONCILIATION_FETCH_FAILED"
                self.logger().exception("LIFE order reconciliation failed")
                continue
            if (manager.state == "TRANSITIONING" and session_id == current.session_id
                    and epoch == current.epoch):
                primary_result = result
            if (result.scope_complete and result.trade_events_reconciled
                    and self.verify_runner_fill_events()
                    and not (result.open_order_ids or result.pending_cancel_ids
                             or result.unknown_order_ids)):
                scoped_wire_ids = {record.client_order_id for record in wal.scoped_records(
                    session_id, epoch)}
                self._runner_fill_observations = {
                    trade_id: observation
                    for trade_id, observation in self._runner_fill_observations.items()
                    if observation[1] not in scoped_wire_ids}
                self._runner_cancel_observations = {
                    wire_id: exchange_id
                    for wire_id, exchange_id in self._runner_cancel_observations.items()
                    if wire_id not in scoped_wire_ids}
            if not result.scope_complete:
                reason = "RECONCILIATION_INCOMPLETE"
            elif not result.trade_events_reconciled:
                reason = "OLD_FILLS_UNRECONCILED"
            elif (result.open_order_ids or result.pending_cancel_ids
                  or result.unknown_order_ids):
                reason = "CANCEL_REQUEST_FAILED" if cancel_failed else "OLD_ORDERS_UNRESOLVED"
        if self._inventory_executor is not None:
            await self._inventory_executor.reconcile()
        if manager.state == "TRANSITIONING" and primary_result is not None:
            try:
                qualified = None
                if (reason == "OLD_ORDERS_RECONCILED"
                        and self._runtime_risk_ready() and self._joint_risk_ready()
                        and self._hedge_ready() and self._execution_loss_ready()
                        and self._fill_attribution_ready() and self._markout_risk_ready()
                        and not self.has_unverified_runner_order_events()
                        and self._spot_quote_gates_ready()
                        and self._quote_action_planner is not None):
                    qualified = self._quote_action_planner.session_snapshot()
                anchor = (qualified.market_anchor_usdt if qualified is not None
                          and qualified.market_reference_ready is True else None)
                market_ready = (isinstance(anchor, Decimal) and anchor.is_finite()
                                and anchor > 0)
                manager.tick(reference_ready=market_ready, all_gates_ready=market_ready,
                             reconciliation=primary_result,
                             market_reference_ready=market_ready,
                             market_anchor_usdt=anchor if market_ready else None)
            except Exception:
                reason = "SESSION_SAFETY_TICK_FAILED"
                self.logger().exception("LIFE transition reconciliation failed")
        if reason == "OLD_ORDERS_RECONCILED" and manager.state == "TRANSITIONING":
            reason = manager.reason_code
        self.order_safety_reason_code = reason
        if reason == "OLD_ORDERS_RECONCILED" and manager.state == "PAUSED":
            self._pause_reconciliation_required = False

    def stop(self):
        self._order_safety_stopped = True
        if self._storage_runtime is not None:
            self._storage_runtime.revoke()
        history_worker = getattr(self._joint_recovery, "history_worker", None)
        if history_worker is not None:
            history_worker.close()
        inventory_task = getattr(self._inventory_executor, "active_task", None)
        if inventory_task is not None:
            inventory_task.add_done_callback(lambda _: self._release_account_lock_when_quiescent())
            if inventory_task is not self.order_safety_task:
                inventory_task.cancel()
        if self._joint_reconciliation_task is not None:
            self._joint_reconciliation_task.add_done_callback(
                lambda _: self._release_account_lock_when_quiescent())
            self._joint_reconciliation_task.cancel()
        if self._fee_refresh_task is not None:
            self._fee_refresh_task.cancel()
        super().stop()
        if self.order_safety_watchdog_task is not None and not self.order_safety_watchdog_task.done():
            self.order_safety_watchdog_task.cancel()
        if self.order_safety_task is not None and not self.order_safety_task.done():
            self.order_safety_task.cancel()
        self._release_account_lock_on_task_done = True
        self._release_account_lock_when_quiescent()
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            self._perpetual_poll_task.cancel()

    def _release_account_lock_when_quiescent(self) -> None:
        if (self._release_account_lock_on_task_done
                and all(task is None or task.done() for task in (
                    self.order_safety_task, self._joint_reconciliation_task,
                    getattr(self._inventory_executor, "active_task", None)))):
            self._release_account_lock()

    def _release_account_lock(self) -> None:
        runtime = self._storage_runtime
        if runtime is not None and runtime.handles_financial_spot is True:
            # The repository owns physical exclusion until its FIFO cleanup
            # drains every admitted financial/reporting job, including late ACKs.
            self._release_account_lock_on_task_done = False
            return
        if self._order_safety_account_lock is not None:
            self._order_safety_account_lock.release()
            self._order_safety_account_lock = None
            self._account_uid_verified = False
            self._release_account_lock_on_task_done = False

    def benchmark_connector(self):
        if self.benchmark_route is None:
            raise ValueError("BENCHMARK_SOURCE_NOT_CONFIGURED")
        return self.benchmark_route.resolve(self.market_data_provider)

    def _record_telemetry(self, stage, wire_id=None, allowed=None, reason_code="SNAPSHOT",
                          *, intent_id=None) -> bool:
        recorder = self._telemetry
        if recorder is None:
            return True
        if self._storage_runtime is not None:
            self._storage_runtime.assert_native_context()
        if self._telemetry_recording:
            return False
        self._telemetry_recording = True
        try:
            if stage in ("QUEUE_REJECT", "FINAL_SEND") and allowed is False:
                self._telemetry_rejects += 1
            manager = self._order_safety_manager
            current = manager.current_session if manager is not None else None
            record = None
            if wire_id is not None:
                record = self._order_safety_wal.find_by_client_order_id(wire_id)
                intent_id = record.intent_id
            now = recorder.clock_ms()
            metrics, markouts, inputs = {}, {}, {}
            if stage == "SNAPSHOT":
                try:
                    value = self._telemetry_value()
                except Exception:
                    value = None
                metrics, markouts = collect_spot_metrics(
                    self, utc_now_ms=self._telemetry_utc_ms(), independent_value=value,
                    max_value_age_ms=self._telemetry_max_value_age_ms,
                    synthetic=recorder.synthetic, monotonic_ms=now)
                if self.config.strategy.perpetual.enabled:
                    metrics.update(collect_extension_metrics(self, synthetic=recorder.synthetic))
            if stage not in ("CANCEL_REQUEST", "EXCHANGE_CONFIRM", "ACK"):
                inputs = collect_quote_inputs(self)
                if record is not None:
                    inputs.update(wal_state=record.state, cancel_requested=record.cancel_requested)
                    held = self._order_safety_reservations.reservation_state(record.intent_id)
                    if held is not None:
                        inputs.update(intent_price_usdt=str(held.intent.limit_price_usdt),
                                      intent_quantity_base=str(held.intent.quantity_base),
                                      intent_remaining_base=str(held.remaining_base), intent_side=held.intent.side)
            quality = "synthetic" if recorder.synthetic else "verified"
            if stage == "FINAL_SEND" and allowed is True:
                self._telemetry_sent_ms.setdefault(wire_id, now)
            if stage == "CANCEL_REQUEST":
                self._telemetry_cancel_ms.setdefault(wire_id, now)
            if stage == "ACK" and wire_id in self._telemetry_sent_ms:
                metrics["ack_latency_ms"] = Metric(
                    Decimal(now - self._telemetry_sent_ms[wire_id]), "ms", quality, "FINAL_CHECK_TO_ACK")
            if stage == "EXCHANGE_CONFIRM" and wire_id in self._telemetry_cancel_ms:
                metrics["cancel_confirm_latency_ms"] = Metric(
                    Decimal(now - self._telemetry_cancel_ms[wire_id]), "ms", quality, "CANCEL_TO_ACCOUNT_PROOF")
            metrics["reject_count"] = Metric(Decimal(self._telemetry_rejects), "count",
                                             "synthetic" if recorder.synthetic else "verified", "ACTION_REJECTIONS")
            recorder.capture(stage=stage, allowed=allowed, reason_code=reason_code,
                             session_id=record.session_id if record else current.session_id if current else None,
                             epoch=record.epoch if record else current.epoch if current else None,
                             config_version=current.config_version if current else None,
                             intent_id=intent_id, wire_id=wire_id, event_id=recorder.active_event_id,
                             gates=self._permission_gates, inputs=inputs, metrics=metrics, markouts=markouts)
            return True
        except Exception:
            recorder.healthy = False
            self.telemetry_reason_code = "TELEMETRY_UNAVAILABLE"
            return False
        finally:
            self._telemetry_recording = False

    def _spot_quote_gates_ready(self) -> bool:
        return (self.config.strategy.spot.enabled and self.config_update_state.order_permission()
                and self.listing_gate.metadata_ready
                and self.book_ready and self._live_spot_book_ready()
                and self.continuous_gate.ready and self.snapshot_gate.permit()
                and self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate)
                and self.trading_permissions_ready())

    def _live_spot_book_ready(self) -> bool:
        try:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
            tracked_books = getattr(getattr(connector, "order_book_tracker", None), "order_books", None)
            if isinstance(tracked_books, dict) and self.config.strategy.spot.pair not in tracked_books:
                return False
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return False
        return is_order_book_ready(connector, self.config.strategy.spot.pair)

    def _book_feed_health(self):
        try:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
            health = connector.order_book_tracker.data_source.book_feed_health(self.config.strategy.spot.pair)
            return health if isinstance(health, BookFeedHealth) else None
        except (AttributeError, KeyError, TypeError):
            return None

    async def control_task(self):
        self.book_ready = False
        self.snapshot_gate.reset()
        self.continuity_gate.reset()
        self.continuous_gate.reset()
        self._schedule_perpetual_listing_poll()
        if not self.config.strategy.spot.enabled:
            await self.update_processed_data()
            return
        source = OkxInstrumentSource(self.market_data_provider, self.config.strategy.spot.connector, "SPOT")
        await self.listing_gate.poll(self.listing_gate.clock(), source.fetch)
        if (self.listing_gate.metadata_ready and not self.spot_book_bootstrap_ready
                and self.snapshot_gate.clock() >= self.spot_book_bootstrap_next_at):
            try:
                self.spot_book_bootstrap_ready = bool(
                    await self.market_data_provider.initialize_order_book(
                        self.config.strategy.spot.connector, self.config.strategy.spot.pair))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.spot_book_bootstrap_ready = False
            self.spot_book_bootstrap_reason_code = (
                "ORDER_BOOK_BOOTSTRAP_CONFIRMED" if self.spot_book_bootstrap_ready
                else "ORDER_BOOK_BOOTSTRAP_FAILED")
            if self.spot_book_bootstrap_ready:
                self.spot_book_bootstrap_failures = 0
            else:
                self.spot_book_bootstrap_failures += 1
                delay = min(BOOK_BOOTSTRAP_RETRY_MAX_SECONDS,
                            BOOK_BOOTSTRAP_RETRY_BASE_SECONDS
                            * 2 ** min(self.spot_book_bootstrap_failures - 1, 6))
                self.spot_book_bootstrap_next_at = self.snapshot_gate.clock() + delay
        if self.listing_gate.metadata_ready and self.spot_book_bootstrap_ready:
            try:
                connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
                tracked_books = getattr(getattr(connector, "order_book_tracker", None), "order_books", None)
                if isinstance(tracked_books, dict) and self.config.strategy.spot.pair not in tracked_books:
                    self.spot_book_bootstrap_ready = False
                    self.spot_book_bootstrap_reason_code = "ORDER_BOOK_TRACKING_LOST"
                    self.spot_book_bootstrap_failures = 1
                    self.spot_book_bootstrap_next_at = (self.snapshot_gate.clock()
                                                        + BOOK_BOOTSTRAP_RETRY_BASE_SECONDS)
                else:
                    self.book_ready = is_order_book_ready(connector, self.config.strategy.spot.pair)
            except Exception:
                pass
        if self.book_ready:
            book_response = None
            received_monotonic = None
            request_started_monotonic = None
            try:
                request_started_monotonic = self.snapshot_gate.clock()
                book_response = await source.fetch_order_book(self.config.strategy.spot.pair)
                received_monotonic = self.snapshot_gate.clock()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.snapshot_gate.reason_code = "BOOK_FETCH_ERROR"
            try:
                time_response = await source.fetch_server_time()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.continuous_gate.reason_code = "EXCHANGE_TIME_FETCH_ERROR"
            else:
                self.continuous_gate.evaluate(self.listing_gate.instrument_info, time_response)
            if self.continuous_gate.ready and received_monotonic is not None:
                self.snapshot_gate.evaluate(
                    book_response, exchange_now_ms=self.continuous_gate.exchange_time_ms,
                    received_monotonic=received_monotonic, market_state=self.listing_gate.instrument_state,
                    continuous_trading_ready=True,
                )
                if self.snapshot_gate.permit():
                    self.continuity_gate.confirm(self._book_feed_health(), self.snapshot_gate.snapshot,
                                                 request_started_monotonic)
        await self.update_processed_data()
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            if self.market_data_provider.ready and self.executors_update_event.is_set():
                actions = await runtime.application.plan()
                if actions:
                    await self.send_actions(actions)
        elif self.snapshot_gate.permit():
            await super().control_task()

    def _schedule_perpetual_listing_poll(self):
        if not self.config.strategy.perpetual.enabled:
            self.perpetual_contract = None
            self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
            return
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            return
        gate = self.perpetual_listing_gate
        if gate.clock() < gate.next_refresh_at:
            return
        # Revoke an old positive observation before the asynchronous fetch starts.
        gate.instrument_found = False
        gate.instrument_rules = None
        gate.instrument_state = None
        gate.instrument_info = None
        self.perpetual_contract = None
        self.perpetual_contract_reason_code = "SWAP_METADATA_PENDING"
        self._perpetual_poll_task = asyncio.create_task(self._poll_perpetual_listing_and_publish())

    async def _poll_perpetual_listing_and_publish(self):
        try:
            await self._poll_perpetual_listing()
            await self.update_processed_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.perpetual_contract = None
            self.perpetual_listing_gate.instrument_found = False
            self.perpetual_listing_gate.instrument_rules = None
            self.perpetual_listing_gate.next_refresh_at = (
                self.perpetual_listing_gate.clock() + self.perpetual_listing_gate.base_delay)
            self.perpetual_contract_reason_code = "SWAP_METADATA_FETCH_ERROR"
            self.logger().exception("LIFE SWAP metadata poll failed")
            await self.update_processed_data()

    async def _poll_perpetual_listing(self):
        self.perpetual_contract = None
        if not self.config.strategy.perpetual.enabled:
            self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
            return
        source = OkxInstrumentSource(self.market_data_provider,
                                     self.config.strategy.perpetual.connector, "SWAP")
        await self.perpetual_listing_gate.poll(self.perpetual_listing_gate.clock(), source.fetch)
        self.perpetual_contract_reason_code = self.perpetual_listing_gate.reason_code
        if self.perpetual_listing_gate.instrument_found:
            try:
                self.perpetual_contract = LinearSwapContract.from_okx(
                    self.perpetual_listing_gate.instrument_info,
                    self.config.strategy.perpetual.pair,
                )
            except ContractValidationError as exc:
                self.perpetual_contract_reason_code = exc.reason_code
            else:
                if self.perpetual_listing_gate.instrument_state == "live":
                    self.perpetual_contract_reason_code = "SWAP_METADATA_CONFIRMED"

    async def update_processed_data(self):
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            await runtime.application.refresh_processed_data()
            return
        self._update_processed_data_sync()

    def to_format_status(self):
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.status_lines()
        reason = self.processed_data.get("reason_code", self.listing_gate.reason_code)
        if self.snapshot_gate.ready and not self.snapshot_gate.permit():
            reason = "BOOK_STALE"
        elif self.snapshot_gate.permit() and not self.continuity_gate.permit(
                self._book_feed_health(), self.snapshot_gate):
            reason = self.continuity_gate.reason_code
        session = self._session_status_fields()
        return [f"LIFE liquidity {session['market']}: {self.listing_gate.state} ({reason}); "
                f"reference: {session['reference_price_usdt'] or 'unavailable'}; "
                f"session: {session['session_state']} until "
                f"{session['session_expires_at'] or 'unavailable'} "
                f"({session['session_reason_code']}); "
                f"pause: {session['pause_reason_code'] or 'none'}; "
                f"transition/reconciliation: {session['transition_reason_code'] or 'none'}/"
                f"{session['reconciliation_reason_code']}; "
                f"order safety: {self.order_safety_reason_code}; "
                f"quote action recovery: {self.quote_action_recovery_reason_code}; "
                f"own depth: {self._own_depth_decision.reason_code}; "
                f"telemetry: {self.telemetry_reason_code}; "
                f"carry: {self.carry_reason_code}; "
                f"residual risk: {self._spot_exit_planner.residual_reason_code if self._spot_exit_planner else 'unavailable'}; "
                "trading is disabled pending P2–P9 gates."]
