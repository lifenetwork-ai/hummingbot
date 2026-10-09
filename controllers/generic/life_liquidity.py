"""LIFE V2 controller adapter for configuration discovery and offline validation.

The trading controller is deliberately inert until the P2–P9 data, accounting,
order, and release gates are implemented. Loading this module cannot place orders.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from pydantic import Field

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.config import ConfigUpdateState, StrategyConfig
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.hedge import HedgeObservation, HedgePolicy, plan_life_hedge
from hummingbot.strategy_v2.life_liquidity.joint_exposure import (
    JointExposureObservation,
    JointRiskLimits,
    LinearLifeContractSpec,
    evaluate_joint_exposure,
)
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
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    CancelRetryPolicy,
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnDepthDecision
from hummingbot.strategy_v2.life_liquidity.own_depth_runner import separate_local_own_depth
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner, QuotePlanningSnapshot
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.session import SessionManager, SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
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


class LifeLiquidityController(ControllerBase):
    def __init__(self, config: LifeLiquidityConfig, *args, **kwargs):
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
        self._runner_orchestrator = None
        self._runner_halt_ok = False
        self._runner_wire_owners: dict[str, str] = {}
        self._runner_stored_executor_ids: set[str] = set()
        self._runner_scope_invalid = False
        self._runner_fill_observations: dict[str, tuple] = {}
        self._runner_cancel_observations: dict[str, str | None] = {}
        self._own_depth_decision = OwnDepthDecision(None, "OWN_DEPTH_NOT_EVALUATED")
        self._protected_spot_sender: ProtectedSpotExecutorSender | None = None
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
        self.markout_reason_code = "MARKOUT_NOT_INSTALLED"
        self._quote_action_recovery_ready = False
        self._quote_action_recovery_records = None
        self.quote_action_recovery_reason_code = "QUOTE_ACTION_RECOVERY_NOT_CONFIGURED"
        self.order_safety_task: asyncio.Task | None = None
        self.order_safety_watchdog_task: asyncio.Task | None = None
        self.order_safety_reason_code = "ORDER_SAFETY_NOT_INSTALLED"
        self._pause_reconciliation_required = False
        self._observed_session_state: str | None = None

    def _restore_order_safety(self) -> None:
        """Restore existing journals only; no synthetic session or balances are created."""
        directory = Path(self.config.recovery_state_dir)
        if (not directory.is_absolute() or not directory.is_dir()
                or not isinstance(self.config.recovery_account_uid, str)
                or not self.config.recovery_account_uid.isascii()
                or not self.config.recovery_account_uid.isdecimal()
                or not isinstance(self.config.recovery_reconciliation_max_age_ms, int)
                or isinstance(self.config.recovery_reconciliation_max_age_ms, bool)
                or self.config.recovery_reconciliation_max_age_ms <= 0):
            raise ValueError("ORDER_SAFETY_RECOVERY_CONFIG_INVALID")
        paths = [directory / name for name in (
            "session.json", "intents.json", "reservations.json", "cashflows.json")]
        if any(not path.is_file() or path.is_symlink() for path in paths):
            raise ValueError("ORDER_SAFETY_JOURNAL_MISSING")
        risk = self.config.strategy.risk
        if any(value is None for value in (
                risk.min_inventory_base, risk.max_inventory_base,
                risk.max_gross_quote, risk.max_net_base)):
            raise ValueError("ORDER_SAFETY_RISK_LIMITS_MISSING")
        limits = RiskLimits(risk.min_inventory_base, risk.max_inventory_base,
                            risk.max_gross_quote, risk.max_net_base)
        account_ownership = AccountRiskPoolLock(
            self.config.recovery_account_uid, account_lock.ACCOUNT_LOCK_ROOT)
        account_ownership.acquire()
        try:
            self._restore_order_safety_journals(paths, limits)
        except BaseException:
            account_ownership.release()
            raise
        self._order_safety_account_lock = account_ownership

    def _restore_order_safety_journals(self, paths: list[Path], limits: RiskLimits) -> None:
        """Read and repair journals only while the account-wide lock is held."""
        manager = SessionManager(SessionStore(paths[0]),
                                 wall_clock=lambda: datetime.now(timezone.utc),
                                 max_reconciliation_age_ms=(
                                     self.config.recovery_reconciliation_max_age_ms))
        wal = IntentWAL(paths[1])
        reservations = ReservationLedger.restore(paths[2], limits=limits)
        approvals = CashflowApprovals.load(paths[3])
        records = wal.all_records()
        record_ids = {record.reservation_id for record in records}
        if (len({record.client_order_id for record in records}) != len(records)
                or any(record.reservation_id != record.intent_id for record in records)
                or any(record.state not in ("PREPARED", "ABORTED_BEFORE_SEND",
                                            "SEND_UNKNOWN", "ACKED", "TERMINAL")
                       for record in records)
                or not reservations.reservation_ids <= record_ids
                or any(record.state not in ("PREPARED", "ABORTED_BEFORE_SEND")
                       and record.reservation_id not in reservations.reservation_ids
                       for record in records)
                or any(record.reservation_id in reservations.reservation_ids
                       and not reservations.matches_identity(
                           record.reservation_id, record.session_id, record.epoch)
                       for record in records)
                or any(record.state in ("PREPARED", "ABORTED_BEFORE_SEND")
                       and record.reservation_id in reservations.reservation_ids
                       and not reservations.can_abort_unsent(
                           record.reservation_id, record.session_id, record.epoch)
                       for record in records)):
            raise ValueError("ORDER_SAFETY_JOURNALS_DISAGREE")
        if manager.current_session is None:
            raise ValueError("ORDER_SAFETY_SESSION_MISSING")
        connector = self.market_data_provider.get_connector_with_fallback(
            self.config.strategy.spot.connector)
        required = ("cancel_by_client_id", "get_order_by_client_id",
                    "cancel_by_exchange_order_id", "get_order_by_exchange_order_id",
                    "get_fills_by_exchange_order_id", "get_all_open_spot_orders_page",
                    "get_all_pending_spot_algo_orders_page",
                    "get_spot_order_history_page", "get_spot_fill_history_page",
                    "get_all_spot_fill_history_page",
                    "get_spot_cash_balances", "get_account_uid", "get_account_bills_page")
        if any(not callable(getattr(connector, name, None)) for name in required):
            raise ValueError("ORDER_SAFETY_CONNECTOR_UNAVAILABLE")
        reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
        bills = SpotBillReconciler(connector, reservations, wal, approvals)
        account = SpotAccountReconciler(connector, reservations, bills=bills)
        gateway = OkxSpotOrderGateway(
            connector, wal, trading_pair=self.config.strategy.spot.pair,
            clock=lambda: datetime.now(timezone.utc),
            apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
            on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
            account_check=account.check)
        # PREPARED is durable proof that the protected connector was never
        # called. Abort the WAL first, then release any matching unfilled
        # reservation; a crash between writes is safe to replay.
        for record in records:
            if record.state == "PREPARED":
                wal.abort_before_send(record.intent_id)
            if record.state in ("PREPARED", "ABORTED_BEFORE_SEND"):
                if record.reservation_id in reservations.reservation_ids:
                    reservations.abort_unsent(
                        record.reservation_id, session_id=record.session_id,
                        epoch=record.epoch, wal=wal)
        self.install_order_safety(manager, gateway, wal, reservations=reservations)
        self._verify_quote_action_recovery(paths[0].parent, manager, wal, reservations)

    def _verify_quote_action_recovery(self, directory: Path, manager: SessionManager,
                                      wal: IntentWAL, reservations: ReservationLedger) -> None:
        """Keep cancellation available while a bad action journal blocks quotes."""
        self._quote_action_recovery_ready = False
        self._quote_action_recovery_records = None
        self.quote_action_recovery_reason_code = "QUOTE_ACTION_RECOVERY_NOT_CONFIGURED"
        if self.config.require_quote_action_journal is not True:
            return
        path = directory / "quote_actions.json"
        if not path.is_file() or path.is_symlink():
            self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNAL_MISSING"
            return
        try:
            journal = QuoteActionJournal(path, account_uid=self.config.recovery_account_uid)
            claims = journal.verified_records()
        except (OSError, ValueError):
            self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNAL_INVALID"
            return
        wal_by_id = {record.intent_id: record for record in wal.all_records()}
        reservation_ids = reservations.reservation_ids
        claim_by_id = {claim.intent_id: claim for claim in claims}
        active_slots = set()
        unresolved_dispatch = False
        reconciled_claims = []
        current = manager.current_session
        for claim in claims:
            if (claim.controller_id != self.config.id
                    or claim.market != self.config.strategy.spot.pair):
                self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                return
            if claim.state in ("PROPOSED", "DISPATCHED"):
                if claim.slot in active_slots:
                    self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                    return
                active_slots.add(claim.slot)
            record = wal_by_id.get(claim.intent_id)
            if record is None:
                if claim.state in ("PROPOSED", "DISPATCHED"):
                    unresolved_dispatch = True
                elif claim.state == "RECONCILED" or claim.intent_id in reservation_ids:
                    self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                    return
                continue
            if (record.session_id, record.epoch, record.reservation_id,
                    record.slot_market, record.slot_side, record.slot_level) != (
                    claim.session_id, claim.epoch, claim.intent_id,
                    claim.market, claim.side, claim.level):
                self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                return
            if claim.state == "REJECTED":
                self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                return
            if (claim.state == "RECONCILED"
                    and record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")):
                self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                return
            if (record.state in ("TERMINAL", "ABORTED_BEFORE_SEND")
                    and claim.intent_id in reservation_ids
                    and not reservations.is_terminal_intent(claim.intent_id)):
                self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
                return
            if claim.state in ("PROPOSED", "DISPATCHED"):
                if record.state in ("TERMINAL", "ABORTED_BEFORE_SEND"):
                    reconciled_claims.append(claim)
                elif (current is None or (claim.session_id, claim.epoch, claim.config_version)
                      != (current.session_id, current.epoch, current.config_version)):
                    unresolved_dispatch = True
        if any(record.slot_market is not None and record.intent_id not in claim_by_id
               for record in wal_by_id.values()):
            self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
            return
        try:
            for claim in reconciled_claims:
                journal.transition(claim.intent_id, expected=claim.state, state="RECONCILED")
            claims = journal.verified_records()
        except (OSError, ValueError):
            self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNAL_INVALID"
            return
        if unresolved_dispatch:
            self.quote_action_recovery_reason_code = "QUOTE_ACTION_DISPATCH_UNRESOLVED"
            return
        self._quote_action_recovery_ready = True
        self._quote_action_recovery_records = claims
        self.quote_action_recovery_reason_code = "QUOTE_ACTION_JOURNAL_VERIFIED"

    def install_order_safety(self, manager: SessionManager, gateway: OkxSpotOrderGateway,
                             wal: IntentWAL, *, reservations: ReservationLedger | None = None) -> None:
        """Attach restored spot order state; this never enables order creation."""
        if (gateway.request_budget is not None
                and gateway.request_budget.account_uid != self.config.recovery_account_uid):
            raise ValueError("ORDER_SAFETY_REQUEST_BUDGET_UID_MISMATCH")
        if (manager.current_session is None or gateway.wal is not wal
                or gateway.trading_pair != self.config.strategy.spot.pair
                or gateway.apply_fills is None or gateway.confirm_terminal is None
                or gateway.on_cancel_requested is None or gateway.on_unknown is None
                or gateway.account_check is None
                or gateway.scope_check is not None):
            raise ValueError("ORDER_SAFETY_RECOVERY_INCOMPLETE")
        policy = self._configured_cancel_retry_policy()
        if (self.order_safety_watchdog_task is not None
                and not self.order_safety_watchdog_task.done() and policy is None):
            raise ValueError("ORDER_SAFETY_CANCEL_POLICY_UNCONFIGURED")
        if policy is not None:
            if gateway.cancel_retry_policy not in (None, policy):
                raise ValueError("ORDER_SAFETY_CANCEL_POLICY_MISMATCH")
            gateway.cancel_retry_policy = policy
        self._order_safety_manager = manager
        self._order_safety_gateway = gateway
        self._order_safety_wal = wal
        self._order_safety_reservations = reservations
        self.order_safety_reason_code = "ORDER_SAFETY_READY_TO_RECONCILE"
        self._pause_reconciliation_required = manager.state == "PAUSED"
        self._observed_session_state = manager.state
        if self.order_safety_watchdog_task is not None and not self.order_safety_watchdog_task.done():
            gateway.runner_scope_check = self._runner_executor_scope_complete

    def on_runner_order_event_failure(self) -> None:
        self._runner_scope_invalid = True

    def on_runner_order_filled(self, event: OrderFilledEvent) -> None:
        """Hold runner evidence until authenticated exchange reconciliation agrees."""
        if not isinstance(event, OrderFilledEvent):
            self._runner_scope_invalid = True
            return
        if event.trading_pair != self.config.strategy.spot.pair:
            return
        wal = self._order_safety_wal
        try:
            record = wal.find_by_client_order_id(event.order_id)
            fee = event.trade_fee
            if (record.state == "ABORTED_BEFORE_SEND"
                    or record.slot_market != event.trading_pair
                    or record.slot_side != event.trade_type.name
                    or event.trade_type not in (TradeType.BUY, TradeType.SELL)
                    or event.order_type != OrderType.LIMIT_MAKER
                    or not isinstance(event.exchange_trade_id, str)
                    or not event.exchange_trade_id
                    or not isinstance(event.exchange_order_id, str)
                    or not event.exchange_order_id
                    or (record.exchange_order_id is not None
                        and record.exchange_order_id != event.exchange_order_id)
                    or not isinstance(event.amount, Decimal)
                    or not event.amount.is_finite() or event.amount <= 0
                    or not isinstance(event.price, Decimal)
                    or not event.price.is_finite() or event.price <= 0
                    or not isinstance(fee, TradeFeeBase)
                    or fee.percent != 0
                    or len(fee.flat_fees) != 1):
                raise ValueError("RUNNER_FILL_IDENTITY_INVALID")
            flat_fee = fee.flat_fees[0]
            if (flat_fee.token not in ("LIFE", "USDT")
                    or fee.percent_token not in (None, flat_fee.token)
                    or not isinstance(flat_fee.amount, Decimal)
                    or not flat_fee.amount.is_finite()):
                raise ValueError("RUNNER_FILL_FEE_INVALID")
            observation = (record.intent_id, event.order_id, event.exchange_order_id,
                           event.amount, event.price, flat_fee.token, -flat_fee.amount)
            previous = self._runner_fill_observations.get(event.exchange_trade_id)
            if previous is not None and previous != observation:
                raise ValueError("RUNNER_FILL_CONFLICT")
            self._runner_fill_observations[event.exchange_trade_id] = observation
        except (AttributeError, KeyError, TypeError, ValueError):
            self._runner_scope_invalid = True

    def on_runner_order_canceled(self, event: OrderCancelledEvent) -> None:
        """A tracker cancel event is a hint, never exchange terminal proof."""
        if not isinstance(event, OrderCancelledEvent):
            self._runner_scope_invalid = True
            return
        wal = self._order_safety_wal
        if wal is None:
            return
        try:
            record = wal.find_by_client_order_id(event.order_id)
        except KeyError:
            return  # Another market/controller can cancel on the same runner.
        if (record.state == "ABORTED_BEFORE_SEND"
                or record.slot_market != self.config.strategy.spot.pair
                or event.exchange_order_id is not None
                and record.exchange_order_id is not None
                and event.exchange_order_id != record.exchange_order_id):
            self._runner_scope_invalid = True
            return
        previous = self._runner_cancel_observations.get(event.order_id)
        if previous is not None and event.exchange_order_id not in (None, previous):
            self._runner_scope_invalid = True
            return
        self._runner_cancel_observations[event.order_id] = (
            event.exchange_order_id or previous)

    def has_unverified_runner_order_events(self) -> bool:
        return (self._runner_scope_invalid or bool(self._runner_fill_observations)
                or bool(self._runner_cancel_observations))

    def verify_runner_fill_events(self) -> bool:
        if self._runner_scope_invalid:
            return False
        wal = self._order_safety_wal
        reservations = self._order_safety_reservations
        if self._runner_fill_observations and (wal is None or reservations is None):
            return False
        try:
            for trade_id, (intent_id, wire_id, exchange_id, quantity, price,
                           fee_currency, signed_fee) in self._runner_fill_observations.items():
                record = wal.find_by_client_order_id(wire_id)
                if (record.intent_id != intent_id
                        or record.exchange_order_id != exchange_id
                        or not reservations.matches_recorded_fill(
                            intent_id, trade_id, quantity, price, fee_currency, signed_fee)):
                    return False
            for wire_id, exchange_id in self._runner_cancel_observations.items():
                record = wal.find_by_client_order_id(wire_id)
                if (not record.exchange_order_id
                        or exchange_id is not None
                        and record.exchange_order_id != exchange_id):
                    return False
        except (AttributeError, KeyError, TypeError, ValueError):
            return False
        return True

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
        if new_config.strategy != self.config.strategy:
            self.config_update_state.reject("CONFIG_UPDATE_UNSUPPORTED")
            raise ValueError("LIFE nested hot reload is unavailable until the controller gate is implemented")
        super().update_config(new_config)

    def on_config_load_failure(self):
        return self.config_update_state.reject("CONFIG_LOAD_FAILED")

    def trading_permissions_ready(self) -> bool:
        # P4 will replace this with the final order-permission checks.
        return False

    def install_protected_spot_sender(self, sender: ProtectedSpotExecutorSender) -> None:
        """Attach an explicitly configured sender; this does not grant quote permission."""
        safety = self._order_safety_gateway
        if (self._protected_spot_sender is not None or not isinstance(sender, ProtectedSpotExecutorSender)
                or sender.controller is not self or sender.manager is not self._order_safety_manager
                or safety is None or sender.gateway.wal is not self._order_safety_wal
                or sender.reservations is not self._order_safety_reservations
                or sender.gateway.connector is not safety.connector
                or sender.gateway.request_budget is not safety.request_budget):
            raise ValueError("PROTECTED_SENDER_RECOVERY_MISMATCH")
        sender.gateway.arm_pair(self.config.strategy.spot.pair)
        self._protected_spot_sender = sender

    def install_quote_action_planner(self, planner: QuoteActionPlanner) -> None:
        """Attach an explicit quote source; production create permission stays disabled."""
        if not isinstance(planner, QuoteActionPlanner):
            raise ValueError("QUOTE_ACTION_RECOVERY_MISMATCH")
        if self.config.recovery_state_dir is not None:
            if (self.config.require_quote_action_journal is not True
                    or not self._quote_action_recovery_ready
                    or self._quote_action_recovery_records is None
                    or planner.action_journal.path != Path(self.config.recovery_state_dir) / "quote_actions.json"
                    or planner.action_journal.account_uid != self.config.recovery_account_uid
                    or not planner.action_journal.path.is_file()
                    or planner.action_journal.path.is_symlink()):
                raise ValueError("QUOTE_ACTION_RECOVERY_UNVERIFIED")
            try:
                if planner.action_journal.verified_records() != self._quote_action_recovery_records:
                    raise ValueError("QUOTE_ACTION_RECOVERY_UNVERIFIED")
            except (OSError, ValueError) as exc:
                raise ValueError("QUOTE_ACTION_RECOVERY_UNVERIFIED") from exc
        sender = self._protected_spot_sender
        if (self.config.strategy.economics.objective == "liquidity_service"
                and self._fill_attributor is not None
                and planner.subsidy_budget is not self._fill_attributor.subsidy_budget):
            raise ValueError("QUOTE_SUBSIDY_ATTRIBUTION_MISMATCH")
        if (self._quote_action_planner is not None or not isinstance(planner, QuoteActionPlanner)
                or planner.controller is not self or sender is None
                or planner.manager is not self._order_safety_manager
                or planner.wal is not self._order_safety_wal
                or planner.reservations is not self._order_safety_reservations
                or sender.manager is not planner.manager
                or sender.gateway.wal is not planner.wal
                or sender.reservations is not planner.reservations):
            raise ValueError("QUOTE_ACTION_RECOVERY_MISMATCH")
        self._quote_action_planner = planner

    def install_runtime_risk_gate(self, gate: SafetyGate, *,
                                  observation: Callable[[], SafetyObservation],
                                  monotonic_clock_ms: Callable[[], int],
                                  max_observation_age_ms: int) -> None:
        """Attach an explicit P4 observer to queue, session, and final-send checks."""
        if (self._runtime_risk_gate is not None or not isinstance(gate, SafetyGate)
                or not callable(observation) or not callable(monotonic_clock_ms)
                or not isinstance(max_observation_age_ms, int)
                or isinstance(max_observation_age_ms, bool)
                or max_observation_age_ms <= 0):
            raise ValueError("RUNTIME_RISK_BINDING_INVALID")
        if self.config.recovery_state_dir is not None and (
                gate.path != Path(self.config.recovery_state_dir) / "safety.json"
                or gate.path.is_symlink()):
            raise ValueError("RUNTIME_RISK_RECOVERY_MISMATCH")
        self._runtime_risk_gate = gate
        self._runtime_risk_observation = observation
        self._runtime_risk_clock_ms = monotonic_clock_ms
        self._runtime_risk_max_age_ms = max_observation_age_ms
        self.runtime_risk_reason_code = "RUNTIME_RISK_STARTUP_REVALIDATION"

    def install_joint_risk_gate(self, contract: LinearLifeContractSpec,
                                limits: JointRiskLimits, *,
                                observation: Callable[[], JointExposureObservation]) -> None:
        metadata = self.perpetual_contract
        if (not self.config.strategy.perpetual.enabled
                or self._joint_contract is not None
                or not isinstance(contract, LinearLifeContractSpec)
                or not isinstance(limits, JointRiskLimits)
                or not callable(observation)
                or metadata is None
                or metadata.instrument != f"{self.config.strategy.perpetual.pair}-SWAP"
                or metadata.contract_value_life != contract.ct_val_base
                or metadata.lot_size_contracts != contract.lot_contracts):
            raise ValueError("JOINT_RISK_BINDING_INVALID")
        self._joint_contract = contract
        self._joint_limits = limits
        self._joint_observation = observation

    def install_hedge_gate(self, policy: HedgePolicy, *,
                           observation: Callable[[], HedgeObservation]) -> None:
        metadata = self.perpetual_contract
        if (not self.config.strategy.perpetual.enabled
                or self._hedge_policy is not None
                or self._joint_contract is None
                or not isinstance(policy, HedgePolicy)
                or not callable(observation)
                or metadata is None or policy.life_swap_instrument != metadata.instrument):
            raise ValueError("HEDGE_GATE_BINDING_INVALID")
        self._hedge_policy = policy
        self._hedge_observation = observation

    def _joint_risk_ready(self) -> bool:
        self._last_joint_observation = None
        if not self.config.strategy.perpetual.enabled:
            return True
        contract = self._joint_contract
        metadata = self.perpetual_contract
        if (contract is None or self._joint_limits is None
                or self._joint_observation is None or metadata is None
                or metadata.instrument != f"{self.config.strategy.perpetual.pair}-SWAP"
                or metadata.contract_value_life != contract.ct_val_base
                or metadata.lot_size_contracts != contract.lot_contracts):
            self.joint_risk_reason_code = "JOINT_RISK_NOT_INSTALLED"
            return False
        try:
            observed = self._joint_observation()
            reservations = self._order_safety_reservations
            if reservations is None:
                self.joint_risk_reason_code = "JOINT_SPOT_RESERVATION_UNAVAILABLE"
                return False
            spot = reservations.preview()
            if (not isinstance(observed, JointExposureObservation)
                    or observed.spot_life_base != reservations.life_balance
                    or observed.spot_buy_pending_base != spot.unresolved_quantity_base("BUY")
                    or observed.spot_sell_pending_base != spot.unresolved_quantity_base("SELL")):
                self.joint_risk_reason_code = "JOINT_SPOT_RESERVATION_MISMATCH"
                return False
            decision = evaluate_joint_exposure(
                contract, observed, self._joint_limits)
            self.joint_risk_reason_code = decision.reason_code
            if decision.allowed:
                self._last_joint_observation = observed
            return decision.allowed
        except Exception:
            self.joint_risk_reason_code = "JOINT_RISK_OBSERVATION_UNAVAILABLE"
            return False

    def _hedge_ready(self) -> bool:
        if not self.config.strategy.perpetual.enabled:
            return True
        if (self._joint_contract is None or self._hedge_policy is None
                or self._hedge_observation is None):
            self.hedge_reason_code = "HEDGE_GATE_NOT_INSTALLED"
            return False
        try:
            observed = self._hedge_observation()
            joint = self._last_joint_observation
            if (not isinstance(observed, HedgeObservation) or joint is None
                    or observed.spot_life_base != joint.spot_life_base
                    or observed.perp_contracts_signed != joint.perp_contracts_signed
                    or observed.mark_price_usdt != joint.mark_price_usdt
                    or observed.index_price_usdt != joint.index_price_usdt
                    or observed.mark_source != joint.mark_source
                    or observed.index_source != joint.index_source):
                self.hedge_reason_code = "HEDGE_JOINT_SNAPSHOT_MISMATCH"
                return False
            decision = plan_life_hedge(
                self._hedge_policy, observed, self._joint_contract)
            self.hedge_reason_code = decision.reason_code
            return decision.allow_spot_risk_increase
        except Exception:
            self.hedge_reason_code = "HEDGE_OBSERVATION_UNAVAILABLE"
            return False

    def _runtime_risk_ready(self) -> bool:
        gate = self._runtime_risk_gate
        if gate is None:
            return True  # Production permission remains disabled separately.
        try:
            now = self._runtime_risk_clock_ms()
            observed = self._runtime_risk_observation()
            if (not isinstance(now, int) or isinstance(now, bool)
                    or not isinstance(observed, SafetyObservation)
                    or not isinstance(observed.observed_monotonic_ms, int)
                    or isinstance(observed.observed_monotonic_ms, bool)):
                decision = gate.invalidate("RISK_OBSERVATION_UNAVAILABLE")
            elif (now < observed.observed_monotonic_ms
                  or now - observed.observed_monotonic_ms > self._runtime_risk_max_age_ms):
                decision = gate.invalidate("RISK_OBSERVATION_STALE")
            else:
                monitor = self._capital_risk_monitor
                if monitor is None:
                    decision = gate.evaluate(observed)
                else:
                    capital = monitor.measure()
                    decision = (gate.invalidate("CAPITAL_VALUATION_UNAVAILABLE") if capital is None
                                else gate.evaluate(replace(observed, drawdown_bps=capital.drawdown_bps)))
        except Exception:
            decision = gate.invalidate("RISK_OBSERVATION_UNAVAILABLE")
        self.runtime_risk_reason_code = decision.reason_code
        # DEGRADED probe sizing is not wired into the runner yet. Stay blocked.
        return decision.state == "NORMAL"

    def install_execution_loss_budget(self, ledger: LossBudgetLedger, *,
                                      utc_clock: Callable[[], datetime]) -> None:
        """Bind an explicit durable P4 loss budget to runner and send checks."""
        configured_limit = self.config.strategy.risk.execution_loss_budget_quote
        if (self._execution_loss_budget is not None
                or not isinstance(ledger, LossBudgetLedger) or not callable(utc_clock)
                or configured_limit is not None
                and ledger.campaign_limit_quote != configured_limit):
            raise ValueError("EXECUTION_LOSS_BINDING_INVALID")
        if self.config.recovery_state_dir is not None and (
                ledger.path != Path(self.config.recovery_state_dir) / "loss_budget.json"
                or ledger.path.is_symlink()):
            raise ValueError("EXECUTION_LOSS_RECOVERY_MISMATCH")
        self._execution_loss_budget = ledger
        self._execution_loss_utc_clock = utc_clock
        self.execution_loss_status = None
        self.execution_loss_reason_code = "EXECUTION_LOSS_BUDGET_STARTUP_REVALIDATION"

    def _execution_loss_ready(self) -> bool:
        ledger = self._execution_loss_budget
        if ledger is None:
            return True  # Production order permission remains disabled separately.
        self.execution_loss_status = None
        current = self._order_safety_manager.current_session if self._order_safety_manager else None
        if current is None:
            self.execution_loss_reason_code = "EXECUTION_LOSS_SESSION_UNAVAILABLE"
            return False
        try:
            status = ledger.verified_status(
                session_id=current.session_id, at_utc=self._execution_loss_utc_clock())
        except Exception:
            self.execution_loss_reason_code = "EXECUTION_LOSS_BUDGET_UNAVAILABLE"
            return False
        self.execution_loss_status = status
        if status.exhausted:
            self.execution_loss_reason_code = "EXECUTION_LOSS_BUDGET_EXHAUSTED"
            return False
        self.execution_loss_reason_code = "EXECUTION_LOSS_BUDGET_READY"
        return True

    def install_fill_attributor(self, attributor: ReconciledFillAttributor) -> None:
        """Attach economics only after the gateway has applied exchange fills."""
        gateway = self._order_safety_gateway
        if (self._fill_attributor is not None or not isinstance(attributor, ReconciledFillAttributor)
                or gateway is None or gateway.apply_fills is None
                or attributor.wal is not self._order_safety_wal
                or attributor.reservations is not self._order_safety_reservations
                or attributor.loss_budget is not self._execution_loss_budget):
            raise ValueError("FILL_ATTRIBUTION_BINDING_INVALID")
        if self.config.recovery_state_dir is not None and (
                attributor.path != Path(self.config.recovery_state_dir) / "fill_attribution.json"
                or attributor.path.is_symlink()):
            raise ValueError("FILL_ATTRIBUTION_RECOVERY_MISMATCH")
        if self.config.strategy.economics.objective == "liquidity_service":
            subsidy = attributor.subsidy_budget
            configured = self.config.strategy.economics.subsidy_budget_quote
            planner = self._quote_action_planner
            if (subsidy is None or configured is None
                    or subsidy.campaign_limit_quote != configured.campaign
                    or subsidy.day_limit_quote != (configured.day or configured.campaign)
                    or subsidy.session_limit_quote != (
                        configured.session or configured.day or configured.campaign)
                    or planner is not None and planner.subsidy_budget is not subsidy
                    or self.config.recovery_state_dir is not None
                    and subsidy.path != Path(self.config.recovery_state_dir) / "subsidy_budget.json"
                    or gateway.on_terminal_reconciled is not None):
                raise ValueError("FILL_SUBSIDY_BINDING_INVALID")
        account = getattr(gateway.account_check, "__self__", None)
        bills = account.bills if isinstance(account, SpotAccountReconciler) else None
        if bills is None:
            if attributor.cashflow_approvals is not None:
                raise ValueError("FILL_ATTRIBUTION_BILLS_UNAVAILABLE")
        elif (attributor.cashflow_approvals != bills.approvals
              or bills.on_cashflows_applied is not None):
            raise ValueError("FILL_ATTRIBUTION_BILLS_MISMATCH")
        reservation_apply = gateway.apply_fills

        def apply_and_attribute(wire_id, fills, cumulative) -> bool:
            if reservation_apply(wire_id, fills, cumulative) is not True:
                return False
            return attributor.apply(wire_id, fills)

        gateway.apply_fills = apply_and_attribute
        if self.config.strategy.economics.objective == "liquidity_service":
            def settle_service_terminal(intent_id: str, cumulative: Decimal) -> bool:
                if not attributor.ready():
                    return False
                if cumulative == 0:
                    subsidy.settle_zero_fill_terminal(intent_id, wal=attributor.wal,
                                                      reservations=attributor.reservations)
                return True

            gateway.on_terminal_reconciled = settle_service_terminal
        if bills is not None:
            bills.on_cashflows_applied = attributor.apply_approved_cashflows
        self._fill_attributor = attributor
        self.fill_attribution_reason_code = "FILL_ATTRIBUTION_REVALIDATION_REQUIRED"

    def _fill_attribution_ready(self) -> bool:
        if self._fill_attributor is None:
            return self.config.strategy.economics.objective != "liquidity_service"
        if (self.config.strategy.economics.objective == "liquidity_service"
                and self._fill_attributor.subsidy_budget is None):
            self.fill_attribution_reason_code = "FILL_SUBSIDY_BINDING_INVALID"
            return False
        try:
            ready = self._fill_attributor.ready()
        except Exception:
            ready = False
        self.fill_attribution_reason_code = (
            "FILL_ATTRIBUTION_READY" if ready else "FILL_ATTRIBUTION_UNRESOLVED")
        return ready

    def install_capital_risk_monitor(self, monitor: CapitalRiskMonitor) -> None:
        """Use qualified, cash-flow-adjusted NAV for the opt-in safety gate."""
        if (self._capital_risk_monitor is not None or not isinstance(monitor, CapitalRiskMonitor)
                or self._runtime_risk_gate is None or monitor.attributor is not self._fill_attributor):
            raise ValueError("CAPITAL_RISK_BINDING_INVALID")
        if self.config.recovery_state_dir is not None and (
                monitor.path != Path(self.config.recovery_state_dir) / "capital_risk.json"
                or monitor.path.is_symlink()):
            raise ValueError("CAPITAL_RISK_RECOVERY_MISMATCH")
        self._capital_risk_monitor = monitor

    def install_markout_monitor(self, monitor: ReconciledMarkoutMonitor) -> None:
        """Bind independently observed markout cohorts to quote permissions."""
        if (self._markout_monitor is not None or not isinstance(monitor, ReconciledMarkoutMonitor)
                or monitor.attributor is not self._fill_attributor):
            raise ValueError("MARKOUT_BINDING_INVALID")
        if self.config.recovery_state_dir is not None and (
                monitor.path != Path(self.config.recovery_state_dir) / "markout_risk.json"
                or monitor.path.is_symlink()):
            raise ValueError("MARKOUT_RECOVERY_MISMATCH")
        self._markout_monitor = monitor
        self.markout_reason_code = "MARKOUT_STARTUP_REVALIDATION"

    def install_markout_probe_guard(self, guard: MarkoutProbeGuard) -> None:
        """Opt into a capped bootstrap only after both journals and a planner exist."""
        if (self._markout_probe_guard is not None or not isinstance(guard, MarkoutProbeGuard)
                or guard.monitor is not self._markout_monitor
                or guard.reservations is not self._order_safety_reservations
                or self._quote_action_planner is None):
            raise ValueError("MARKOUT_PROBE_BINDING_INVALID")
        self._markout_probe_guard = guard

    def markout_probe_active(self) -> bool:
        guard = self._markout_probe_guard
        return (guard is not None and self._quote_action_planner is not None
                and guard.capacity_available())

    def markout_probe_authorizes(self, side: str, quantity_base: Decimal, *,
                                 exclude_open_intent: SpotIntent | None = None) -> bool:
        guard = self._markout_probe_guard
        return (guard is not None and guard.authorizes(
            side, quantity_base, exclude_open_intent=exclude_open_intent))

    def _markout_risk_ready(self) -> bool:
        monitor = self._markout_monitor
        if monitor is None:
            return True  # Production order permission remains disabled separately.
        try:
            ready = monitor.evaluate()
        except Exception:
            ready = False
        self.markout_reason_code = monitor.reason_code
        return ready or self.markout_probe_active()

    def authorize_runner_create_action(self, action) -> bool:
        planner = self._quote_action_planner
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            return False
        return planner is None or planner.authorizes_config(action.executor_config)

    def suppress_create_for_stop_batch(self) -> bool:
        """A LIFE cancellation request takes priority over new risk in one runner batch."""
        return True

    def on_runner_create_action_rejected(self, action) -> bool:
        planner = self._quote_action_planner
        return planner.on_runner_action_rejected(action) if planner is not None else False

    def on_runner_create_action_dispatched(self, action) -> bool:
        planner = self._quote_action_planner
        return planner.on_runner_action_dispatched(action) if planner is not None else False

    def submit_executor_spot_order(self, config, *, amount: Decimal,
                                   price: Decimal, order_type) -> str:
        sender = self._protected_spot_sender
        if sender is None or self.allow_create_executor_actions() is not True:
            raise PermissionError("LIFE_TRADING_DISABLED")
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            raise PermissionError("QUOTE_ACTION_RECOVERY_UNVERIFIED")
        planner = self._quote_action_planner
        if (planner is not None
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

    def _pre_send_provenance_complete(self, missing_records) -> bool:
        """Verify account-bound journals when the late executor checkpoint is absent."""
        wal = self._order_safety_wal
        reservations = self._order_safety_reservations
        directory = self.config.recovery_state_dir
        if (not missing_records or not directory or wal is None or reservations is None
                or reservations.path is None or not self._account_uid_verified
                or not self._quote_action_recovery_ready
                or self._quote_action_recovery_records is None
                or self.config.require_quote_action_journal is not True
                or not self.config.recovery_account_uid):
            return False
        try:
            path = Path(directory)
            if (wal.path != path / "intents.json"
                    or reservations.path != path / "reservations.json"
                    or wal.path.is_symlink() or reservations.path.is_symlink()):
                return False
            claims = QuoteActionJournal(
                path / "quote_actions.json",
                account_uid=self.config.recovery_account_uid).verified_records()
            if claims != self._quote_action_recovery_records:
                return False
            if IntentWAL(wal.path).all_records() != wal.all_records():
                return False
            snapshot = reservations.reservation_snapshot()
            durable_reservations = ReservationLedger.restore(
                reservations.path, limits=reservations.limits)
            if durable_reservations.reservation_snapshot() != snapshot:
                return False
            by_intent = {claim.intent_id: claim for claim in claims}
            if len(by_intent) != len(claims):
                return False
            for record in missing_records:
                claim = by_intent.get(record.intent_id)
                reservation = snapshot.get(record.reservation_id)
                if (record.state not in ("SEND_UNKNOWN", "ACKED", "TERMINAL")
                        or record.reservation_id != record.intent_id
                        or record.slot_market != self.config.strategy.spot.pair
                        or claim is None or claim.controller_id != self.config.id
                        or claim.state not in ("PROPOSED", "DISPATCHED", "RECONCILED")
                        or (claim.state == "RECONCILED" and record.state != "TERMINAL")
                        or (claim.session_id, claim.epoch, claim.market,
                            claim.side, claim.level) != (
                            record.session_id, record.epoch, record.slot_market,
                            record.slot_side, record.slot_level)
                        or reservation is None
                        or reservation.intent.intent_id != record.intent_id
                        or reservation.intent.session_id != record.session_id
                        or reservation.intent.epoch != record.epoch
                        or reservation.intent.side != record.slot_side
                        or (record.state == "TERMINAL") != (reservation.state == "TERMINAL")):
                    return False
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _halt_runner_orders(self) -> bool:
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
            try:
                if executor.status in (RunnableStatus.NOT_STARTED, RunnableStatus.RUNNING):
                    executor.early_stop()
                if executor.status not in (RunnableStatus.SHUTTING_DOWN, RunnableStatus.TERMINATED):
                    safe = False
                if (not isinstance(executor.config.id, str) or not executor.config.id
                        or executor.config.controller_id != self.config.id
                        or executor.config.connector_name != self.config.strategy.spot.connector
                        or executor.config.trading_pair != self.config.strategy.spot.pair):
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

    def _runner_executor_scope_complete(self) -> bool:
        """Live and stored executor IDs must resolve to the persisted spot WAL."""
        from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
        from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
        from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

        executors = self._runner_executors()
        if (not self._runner_halt_ok or self._runner_scope_invalid
                or executors is None or self._order_safety_wal is None):
            return False
        if not self.verify_runner_fill_events():
            return False

        def reject() -> bool:
            self._runner_scope_invalid = True
            return False

        records = self._order_safety_wal.all_records()
        known = {record.client_order_id: record for record in records
                 if record.state != "ABORTED_BEFORE_SEND"}
        intent_records = {record.intent_id: record for record in records
                          if record.state != "ABORTED_BEFORE_SEND"}
        if not self._runner_wire_owners.keys() <= known.keys():
            return reject()
        wire_owners = dict(self._runner_wire_owners)
        active_seen = set()
        stored_seen = set()
        try:
            for executor in executors:
                if (not isinstance(executor, OrderExecutor)
                        or not isinstance(executor.config.id, str)
                        or not executor.config.id
                        or executor.config.controller_id != self.config.id
                        or executor.config.connector_name != self.config.strategy.spot.connector
                        or executor.config.trading_pair != self.config.strategy.spot.pair
                        or executor.status not in (RunnableStatus.SHUTTING_DOWN, RunnableStatus.TERMINATED)):
                    return reject()
                for wire_id in executor.recovery_order_ids():
                    record = known.get(wire_id)
                    if (record is None or record.intent_id != executor.config.id
                            or wire_id in active_seen
                            or wire_owners.get(wire_id, executor.config.id) != executor.config.id):
                        return reject()
                    active_seen.add(wire_id)
                    wire_owners[wire_id] = executor.config.id
            read_stored = getattr(self._runner_orchestrator,
                                  "get_stored_executors_by_controller", None)
            if not callable(read_stored):
                return reject()
            stored = read_stored(self.config.id)
            if not isinstance(stored, (tuple, list)):
                return reject()
            stored_ids = set()
            for info in stored:
                if (not isinstance(info, ExecutorInfo)
                        or not isinstance(info.config, OrderExecutorConfig)
                        or not isinstance(info.id, str) or not info.id
                        or info.id in stored_ids or info.config.id != info.id
                        or info.controller_id != self.config.id
                        or info.config.controller_id != self.config.id
                        or info.config.connector_name != self.config.strategy.spot.connector
                        or info.config.trading_pair != self.config.strategy.spot.pair
                        or info.status != RunnableStatus.TERMINATED
                        or not isinstance(info.custom_info, dict)):
                    return reject()
                if (self.config.recovery_account_uid is not None
                        and info.custom_info.get("recovery_account_uid")
                        != self.config.recovery_account_uid):
                    return reject()
                stored_ids.add(info.id)
                wire_ids = info.custom_info.get("recovery_order_ids")
                if (not isinstance(wire_ids, (tuple, list))
                        or any(not isinstance(wire_id, str) or not wire_id
                               for wire_id in wire_ids)
                        or len(wire_ids) != len(set(wire_ids))):
                    return reject()
                current_id = info.custom_info.get("order_id")
                if current_id is not None and current_id not in wire_ids:
                    return reject()
                held = info.custom_info.get("held_position_orders", [])
                if (not isinstance(held, list)
                        or any(not isinstance(order, dict)
                               or order.get("client_order_id") not in wire_ids
                               for order in held)):
                    return reject()
                persisted = intent_records.get(info.id)
                if persisted is None or persisted.client_order_id not in wire_ids:
                    return reject()
                for wire_id in wire_ids:
                    record = known.get(wire_id)
                    if (record is None or record.intent_id != info.id
                            or wire_owners.get(wire_id, info.id) != info.id):
                        return reject()
                    stored_seen.add(wire_id)
                    wire_owners[wire_id] = info.id
            if not self._runner_stored_executor_ids <= stored_ids:
                return reject()
            missing = tuple(record for record in records
                            if record.slot_market is not None
                            and record.state != "ABORTED_BEFORE_SEND"
                            and record.client_order_id not in active_seen | stored_seen)
            # The recorder row is written after the connector call. On a cold
            # restart, accept only a complete pre-send journal chain instead.
            if missing and (executors or not self._pre_send_provenance_complete(missing)):
                return reject()
            self._runner_stored_executor_ids.update(stored_ids)
            self._runner_wire_owners.update(wire_owners)
        except Exception:
            return reject()
        return True

    def on_runner_safety_tick(self, timestamp: float, orchestrator) -> None:
        """Attach the real executor scope before the readiness-independent safety tick."""
        self._runner_orchestrator = orchestrator
        try:
            permitted = self.trading_permissions_ready() is True
        except Exception:
            permitted = False
        manager = self._order_safety_manager
        self._runner_halt_ok = False
        if (manager is None or self._order_safety_gateway is None
                or manager.state != "ACTIVE" or not permitted):
            self._runner_halt_ok = self._halt_runner_orders()
        if self._order_safety_gateway is not None:
            self._order_safety_gateway.runner_scope_check = self._runner_executor_scope_complete
        self.on_safety_tick(timestamp)

    def on_safety_tick(self, timestamp: float) -> None:
        """Persist expiry and run order cancellation independent of quote readiness."""
        if self._order_safety_stopped:
            return
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
            risk_ready = (self._runtime_risk_ready() and self._joint_risk_ready()
                          and self._hedge_ready())
            loss_ready = risk_ready and self._execution_loss_ready()
            accounting_ready = loss_ready and self._fill_attribution_ready()
            markout_ready = accounting_ready and self._markout_risk_ready()
            planner = self._quote_action_planner
            qualified = planner.session_snapshot() if markout_ready and planner is not None else None
            ready = (markout_ready and qualified is not None and self._spot_quote_gates_ready()
                     and not self._pause_reconciliation_required)
            previous_state = manager.state
            manager.tick(reference_ready=ready, all_gates_ready=ready,
                         market_reference_ready=(ready and qualified.market_reference_ready is True))
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
        if manager.state not in ("PAUSED", "EXPIRED", "TRANSITIONING"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.order_safety_reason_code = "ORDER_SAFETY_LOOP_UNAVAILABLE"
            return
        self.order_safety_task = loop.create_task(self._cancel_and_reconcile_orders())
        self.order_safety_task.add_done_callback(self._on_order_safety_done)

    def _on_order_safety_done(self, task: asyncio.Task) -> None:
        if self._release_account_lock_on_task_done:
            self._release_account_lock()
        if task.cancelled():
            self.order_safety_reason_code = "ORDER_SAFETY_CANCELLED"
            return
        failure = task.exception()
        if failure is not None:
            self.order_safety_reason_code = "ORDER_SAFETY_FAILED"
            self.logger().error("LIFE safety order task failed", exc_info=(
                type(failure), failure, failure.__traceback__))

    async def _cancel_and_reconcile_orders(self) -> None:
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
        scopes = {(record.session_id, record.epoch) for record in wal.all_records()
                  if record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")}
        scopes.add((current.session_id, current.epoch))
        primary_result = None
        reason = "OLD_ORDERS_RECONCILED"
        bounded_cancel_failed = False
        if gateway.cancel_retry_policy is not None:
            try:
                await gateway.request_cancel_scopes(tuple(sorted(scopes)))
            except asyncio.CancelledError:
                raise
            except Exception:
                bounded_cancel_failed = True
                self.logger().exception("LIFE bounded order cancellation failed")
        for session_id, epoch in sorted(scopes):
            cancel_failed = bounded_cancel_failed
            if gateway.cancel_retry_policy is None:
                try:
                    await gateway.request_cancel(session_id, epoch)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    cancel_failed = True
                    self.logger().exception("LIFE order cancellation failed")
            try:
                result = await gateway.reconcile(session_id, epoch)
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
        super().stop()
        if self.order_safety_watchdog_task is not None and not self.order_safety_watchdog_task.done():
            self.order_safety_watchdog_task.cancel()
        if self.order_safety_task is not None and not self.order_safety_task.done():
            self._release_account_lock_on_task_done = True
            self.order_safety_task.cancel()
        else:
            self._release_account_lock()
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            self._perpetual_poll_task.cancel()

    def _release_account_lock(self) -> None:
        if self._order_safety_account_lock is not None:
            self._order_safety_account_lock.release()
            self._order_safety_account_lock = None
            self._account_uid_verified = False
            self._release_account_lock_on_task_done = False

    def benchmark_connector(self):
        if self.benchmark_route is None:
            raise ValueError("BENCHMARK_SOURCE_NOT_CONFIGURED")
        return self.benchmark_route.resolve(self.market_data_provider)

    def allow_create_executor_actions(self) -> bool:
        if not self._runtime_risk_ready():
            return False
        if not self._joint_risk_ready() or not self._hedge_ready():
            return False
        if not self._execution_loss_ready():
            return False
        if not self._fill_attribution_ready():
            return False
        if not self._markout_risk_ready():
            return False
        if self.has_unverified_runner_order_events():
            return False
        manager = self._order_safety_manager
        if manager is not None:
            watchdog = self.order_safety_watchdog_task
            safety_cycle_pending = (self.order_safety_task is not None
                                    and not self.order_safety_task.done())
            if (manager.state != "ACTIVE" or watchdog is None or watchdog.done()
                    or safety_cycle_pending):
                return False
        return self._spot_quote_gates_ready()

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
        if self.snapshot_gate.permit():
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
        self._own_depth_decision = self._evaluate_local_own_depth()
        if not self.config.strategy.spot.enabled:
            reason = "SPOT_DISABLED"
        elif not self.listing_gate.metadata_ready:
            reason = self.listing_gate.reason_code
        elif not self.spot_book_bootstrap_ready:
            reason = self.spot_book_bootstrap_reason_code
        elif not self.book_ready:
            reason = "ORDER_BOOK_NOT_READY"
        elif not self.continuous_gate.ready:
            reason = self.continuous_gate.reason_code
        elif not self.snapshot_gate.permit():
            reason = self.snapshot_gate.reason_code
        else:
            reason = self.continuity_gate.reason_code
        snapshot = self.snapshot_gate.snapshot
        contract = self.perpetual_contract
        session_status = self._session_status_fields()
        self.processed_data = {
            "state": self.listing_gate.state,
            "reason_code": reason,
            "instrument_found": self.listing_gate.instrument_found,
            "metadata_ready": self.listing_gate.metadata_ready,
            "spot_book_bootstrap_ready": self.spot_book_bootstrap_ready,
            "book_ready": self.book_ready,
            "continuous_trading_ready": self.continuous_gate.ready,
            "book_snapshot_ready": self.snapshot_gate.permit(),
            "book_continuity_ready": self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate),
            "spot_market_data_ready": bool(
                self.config.strategy.spot.enabled and self.listing_gate.metadata_ready
                and self.book_ready and self.continuous_gate.ready
                and self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate)),
            "perpetual_contract_listed": bool(
                self.config.strategy.perpetual.enabled and self.perpetual_listing_gate.instrument_found),
            "perpetual_metadata_ready": bool(
                self.config.strategy.perpetual.enabled and self.perpetual_listing_gate.metadata_ready
                and self.perpetual_contract is not None),
            "perpetual_contract_value_life": str(contract.contract_value_life) if contract else None,
            "perpetual_min_order_life": str(contract.minimum_order_life) if contract else None,
            "perpetual_order_step_life": str(contract.order_step_life) if contract else None,
            "perpetual_market_state": (self.perpetual_listing_gate.instrument_state
                                       if self.config.strategy.perpetual.enabled else None),
            "perpetual_reason_code": self.perpetual_contract_reason_code,
            "perpetual_quote_ready": False,
            "order_safety_reason_code": self.order_safety_reason_code,
            **session_status,
            "quote_action_recovery_reason_code": self.quote_action_recovery_reason_code,
            "last_book_exchange_timestamp_ms": snapshot.exchange_timestamp_ms if snapshot else None,
            "last_book_received_monotonic": snapshot.received_monotonic if snapshot else None,
            "last_book_source": snapshot.data_source if snapshot else None,
            "own_depth_reason_code": self._own_depth_decision.reason_code,
            "independent_life_mid_usdt": (
                str(self._own_depth_decision.evidence.mid_usdt)
                if self._own_depth_decision.evidence is not None else None),
        }

    def _evaluate_local_own_depth(self, *,
                                  pre_send_intent_id: str | None = None) -> OwnDepthDecision:
        """Surface local book independence; account-wide proof remains a live gate."""
        def unavailable(reason: str) -> OwnDepthDecision:
            return OwnDepthDecision(None, reason)

        config = self.config
        if any(value is None for value in (
                config.own_depth_max_observation_skew_ms,
                config.own_depth_max_book_age_ms,
                config.own_depth_max_distance_bps)):
            return unavailable("OWN_DEPTH_POLICY_UNCONFIGURED")
        if not self.listing_gate.metadata_ready or not self.continuous_gate.ready:
            return unavailable("LIFE_MARKET_NOT_READY")
        book = self.snapshot_gate.snapshot
        if book is None or not self.snapshot_gate.permit():
            return unavailable("BOOK_SNAPSHOT_UNAVAILABLE")
        gateway = self._order_safety_gateway
        if (self._order_safety_wal is None or self._order_safety_reservations is None
                or gateway is None or self._order_safety_account_lock is None
                or not self._account_uid_verified):
            return unavailable("OWN_ORDER_SCOPE_UNVERIFIED")
        try:
            connector = self.market_data_provider.get_connector_with_fallback(
                config.strategy.spot.connector)
            if connector is not gateway.connector:
                return unavailable("OWN_ORDER_CONNECTOR_MISMATCH")
            tracker = connector._order_tracker
            if not isinstance(tracker, ClientOrderTracker):
                return unavailable("OWN_ORDER_TRACKER_UNAVAILABLE")
            return separate_local_own_depth(
                book=book, wal=self._order_safety_wal,
                reservations=self._order_safety_reservations, tracker=tracker,
                orders_observed_monotonic=self.snapshot_gate.clock(),
                max_observation_skew_ms=config.own_depth_max_observation_skew_ms,
                max_book_age_ms=config.own_depth_max_book_age_ms,
                max_depth_distance_bps=config.own_depth_max_distance_bps,
                pre_send_intent_id=pre_send_intent_id)
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return unavailable("OWN_ORDER_TRACKER_UNAVAILABLE")

    def quote_reference_matches(self, observed: QuotePlanningSnapshot,
                                engine: ReferenceEngine | None, *,
                                pre_send_intent_id: str | None = None) -> bool:
        """Recheck the independent market anchor for opt-in quote proposals."""
        def reject(reason: str) -> bool:
            self._own_depth_decision = OwnDepthDecision(None, reason)
            return False

        policy = self.config
        strict = any(value is not None for value in (
            policy.own_depth_max_observation_skew_ms,
            policy.own_depth_max_book_age_ms,
            policy.own_depth_max_distance_bps))
        if not strict:
            return engine is None  # Legacy synthetic action-path tests only.
        if (not isinstance(observed, QuotePlanningSnapshot)
                or not isinstance(engine, ReferenceEngine)
                or engine.life_source_id != f"okx:{self.config.strategy.spot.pair}"
                or observed.reference_model_version != engine.model_version
                or self.config.strategy.reference.mode != "market"):
            return reject("LIFE_REFERENCE_ENGINE_UNAVAILABLE")
        book = self.snapshot_gate.snapshot
        if (book is None or observed.book_sequence_id != book.sequence_id
                or observed.best_bid_usdt != book.bid
                or observed.best_ask_usdt != book.ask):
            return reject("LIFE_REFERENCE_BOOK_CHANGED")
        current = self._evaluate_local_own_depth(
            pre_send_intent_id=pre_send_intent_id)
        self._own_depth_decision = current
        if current.evidence is None or self.snapshot_gate.snapshot is not book:
            return reject(current.reason_code if current.evidence is None
                          else "LIFE_REFERENCE_BOOK_CHANGED")
        exchange_now_ms = self.continuous_gate.exchange_time_ms
        if not isinstance(exchange_now_ms, int) or exchange_now_ms <= 0:
            return reject("LIFE_REFERENCE_TIME_UNAVAILABLE")
        decision = engine.evaluate(
            "market", market=current.evidence, exchange_now_ms=exchange_now_ms)
        if (decision.quality != "QUALIFIED"
                or decision.price_usdt != observed.qualified_reference_usdt):
            return reject(decision.reason_code if decision.price_usdt is None
                          else "LIFE_REFERENCE_PRICE_CHANGED")
        return True

    def determine_executor_actions(self):
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            return []
        planner = self._quote_action_planner
        return planner.propose() if planner is not None else []

    def _session_status_fields(self) -> dict:
        manager = self._order_safety_manager
        session = manager.current_session if manager is not None else None
        state = manager.state if manager is not None else "WAITING_READY"
        reason = manager.reason_code if manager is not None else "SESSION_NOT_STARTED"
        planner = self._quote_action_planner
        qualified = planner.last_qualified_snapshot if planner is not None else None
        return {
            "market": self.config.strategy.spot.pair,
            "reference_price_usdt": (str(qualified.qualified_reference_usdt)
                                     if qualified is not None else None),
            "session_state": state,
            "session_expires_at": session.expires_at.isoformat() if session is not None else None,
            "session_reason_code": reason,
            "pause_reason_code": reason if state == "PAUSED" else None,
            "transition_reason_code": reason if state == "TRANSITIONING" else None,
            "reconciliation_reason_code": self.order_safety_reason_code,
        }

    def to_format_status(self):
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
                "trading is disabled pending P2–P9 gates."]
