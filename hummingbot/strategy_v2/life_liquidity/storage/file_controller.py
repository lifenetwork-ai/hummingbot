"""Explicit legacy file-controller compatibility adapter.

These helpers validate native file installation, provenance and legacy repair.
Configured repository runtimes invoke them only on their owning file worker;
public controller callbacks use ControllerApplicationPort. PostgreSQL must supply
its own application and must never synthesize paths or install these native files.
The original non-repository controller retains its existing legacy behavior.
"""
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.account_lock import AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryMonitor
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.fees import CachedFeeRateSource, OkxFeeRateSource
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.hedge import HedgeObservation, HedgePolicy, plan_life_hedge
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
from hummingbot.strategy_v2.life_liquidity.joint_exposure import (
    JointExposureObservation,
    JointRiskLimits,
    LinearLifeContractSpec,
    evaluate_joint_exposure,
)
from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.markout_risk import MarkoutProbeGuard, ReconciledMarkoutMonitor
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnDepthDecision
from hummingbot.strategy_v2.life_liquidity.own_depth_runner import separate_local_own_depth
from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner, QuotePlanningSnapshot
from hummingbot.strategy_v2.life_liquidity.recovery_probe import RecoveryProbeGuard
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.session import SessionManager, SessionStore
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, SharedCapitalAuthority
from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotRiskBinding
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.controller_port import repository_context
from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction


class FileControllerCompatibility:
    @repository_context
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

    @repository_context
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

    @repository_context
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

    @repository_context
    def install_protected_swap_sender(self, sender: ProtectedSwapExecutorSender) -> None:
        """Install an explicit SWAP boundary without granting production permission."""
        spot_budget = getattr(self._order_safety_gateway, "request_budget", None)
        if (self._protected_swap_sender is not None or not isinstance(sender, ProtectedSwapExecutorSender)
                or sender.controller is not self or sender.manager is not self._order_safety_manager
                or self.config.strategy.perpetual.enabled is not True
                or sender.request_budget.account_uid != self.config.recovery_account_uid
                or spot_budget is not None and sender.request_budget is not spot_budget
                or self._order_safety_wal is not None and sender.wal.path == self._order_safety_wal.path):
            raise ValueError("PROTECTED_SWAP_RECOVERY_MISMATCH")
        capital = self._shared_capital_authority
        if capital is not None and (
                sender.contract.contract_value_life != capital.policy.contract_value_life
                or sender.contract.lot_size_contracts != capital.policy.lot_contracts
                or sender.account_mode != capital.policy.account_mode):
            raise ValueError("SHARED_CAPITAL_CONTRACT_MISMATCH")
        sender.connector.enable_protected_trading_pair(sender.contract.trading_pair)
        self._protected_swap_sender = sender

    @repository_context
    def install_shared_capital_authority(self, authority: SharedCapitalAuthority, *, recover: bool = False) -> None:
        """Opt into one capital journal for both routes; no production permission."""
        perp = self.config.strategy.perpetual
        if (self._shared_capital_authority is not None or not isinstance(authority, SharedCapitalAuthority)
                or not perp.enabled or authority.policy.account_uid != self.config.recovery_account_uid
                or authority.policy.position_mode != perp.position_mode or authority.policy.leverage != perp.leverage
                or perp.margin_mode != "cross" or self._order_safety_reservations is None):
            raise ValueError("SHARED_CAPITAL_BINDING_INVALID")
        if (self.config.recovery_state_dir is not None
                and authority.journal.path.resolve() != (Path(self.config.recovery_state_dir) / "shared_capital.json").resolve()):
            raise ValueError("SHARED_CAPITAL_PATH_MISMATCH")
        sender = self._protected_swap_sender
        if sender is not None and (sender.contract.contract_value_life != authority.policy.contract_value_life
                                   or sender.contract.lot_size_contracts != authority.policy.lot_contracts
                                   or sender.account_mode != authority.policy.account_mode):
            raise ValueError("SHARED_CAPITAL_CONTRACT_MISMATCH")
        # Existing order/claim adoption needs the joint recovery proof in O.7.5.
        records = self._order_safety_wal.all_records() if self._order_safety_wal is not None else ()
        if sender is not None:
            records += sender.wal.all_records()
        if type(recover) is not bool:
            raise ValueError("SHARED_CAPITAL_RECOVERY_MODE_INVALID")
        if not recover and (authority.claim_ids() or self._order_safety_reservations.reservation_ids
                            or any(r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") for r in records)):
            raise ValueError("SHARED_CAPITAL_RECOVERY_REQUIRED")
        self._shared_capital_authority = authority
        self._joint_recovery_restore_required = recover
        if recover and self._order_safety_manager._joint_reconciliation is None:
            self._order_safety_manager.bind_joint_reconciliation(self._joint_successor_ready)

    @repository_context
    def install_joint_recovery(self, recovery: JointRecovery) -> None:
        if (self._joint_recovery is not None or not isinstance(recovery, JointRecovery)
                or recovery.controller is not self or recovery.capital is not self._shared_capital_authority
                or recovery.sender is not self._protected_swap_sender
                or self._order_safety_manager is None):
            raise ValueError("JOINT_RECOVERY_CONTROLLER_BINDING_INVALID")
        if (self.config.recovery_state_dir is not None and recovery.journal.path.resolve()
                != (Path(self.config.recovery_state_dir) / "joint_recovery.json").resolve()):
            raise ValueError("JOINT_RECOVERY_PATH_MISMATCH")
        if self._order_safety_manager._joint_reconciliation != self._joint_successor_ready:
            self._order_safety_manager.bind_joint_reconciliation(self._joint_successor_ready)
        recovery.capital.bind_recovery(recovery)
        self._joint_recovery = recovery

    @repository_context
    def install_carry_monitor(self, monitor: CarryMonitor) -> None:
        """Attach qualified offline streams; joint recovery and live permission stay separate."""
        if (self._carry_monitor is not None or not isinstance(monitor, CarryMonitor)
                or not self.config.strategy.perpetual.enabled
                or monitor.capital is not self._shared_capital_authority
                or monitor.policy.account_uid != self.config.recovery_account_uid):
            raise ValueError("CARRY_CONTROLLER_BINDING_INVALID")
        if (self.config.recovery_state_dir is not None and monitor.journal.path.resolve()
                != (Path(self.config.recovery_state_dir) / "carry.json").resolve()):
            raise ValueError("CARRY_PATH_MISMATCH")
        self._carry_monitor = monitor

    @repository_context
    def install_hedge_coordinator(self, coordinator: HedgeCoordinator) -> None:
        if (self._hedge_coordinator is not None or not isinstance(coordinator, HedgeCoordinator)
                or coordinator.controller is not self or self._shared_capital_authority is None
                or coordinator.capital is not self._shared_capital_authority
                or coordinator.sender is not self._protected_swap_sender):
            raise ValueError("HEDGE_COORDINATOR_BINDING_INVALID")
        if (self.config.recovery_state_dir is not None and coordinator.journal.path.resolve()
                != (Path(self.config.recovery_state_dir) / "hedge_coordinator.json").resolve()):
            raise ValueError("HEDGE_COORDINATOR_PATH_MISMATCH")
        if coordinator.sender.unresolved_order_ids() and not self._joint_recovery_restore_required:
            raise ValueError("HEDGE_COORDINATOR_RECOVERY_REQUIRED")
        self._hedge_coordinator = coordinator

    @repository_context
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

    @repository_context
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
        gate.bind_reconciliation_authority(lambda: self._safety_rearm_verified is True)
        self._runtime_risk_gate = gate
        self._runtime_risk_observation = observation
        self._runtime_risk_clock_ms = monotonic_clock_ms
        self._runtime_risk_max_age_ms = max_observation_age_ms
        self.runtime_risk_reason_code = "RUNTIME_RISK_STARTUP_REVALIDATION"

    async def _reconcile_for_manual_rearm(self) -> None:
        if self._storage_runtime is not None:
            raise StorageError('RUNTIME_SIGNED_REARM_REQUIRED')
        manager, gateway, wal, reservations = (self._order_safety_manager,
                                               self._order_safety_gateway,
                                               self._order_safety_wal,
                                               self._order_safety_reservations)
        directory = self.config.recovery_state_dir
        uid = self.config.recovery_account_uid
        config_snapshot = self.config.model_dump_json()
        ownership = self._order_safety_account_lock
        if (not directory or not uid or manager is None or gateway is None or wal is None
                or reservations is None or ownership is None or ownership._fd is None
                or ownership.path.name != f"okx-{uid}.lock"
                or self.config.require_quote_action_journal is not True):
            raise ValueError("REARM_RECOVERY_BINDING_UNAVAILABLE")
        path = Path(directory)
        account = getattr(gateway.account_check, "__self__", None)
        if (not isinstance(account, SpotAccountReconciler)
                or account.connector is not gateway.connector or account.reservations is not reservations
                or not isinstance(account.bills, SpotBillReconciler)
                or account.bills.wal is not wal or account.bills.reservations is not reservations
                or account.bills.connector is not gateway.connector
                or account.bills.approvals != CashflowApprovals.load(path / "cashflows.json")
                or gateway.scope_check is not None):
            raise ValueError("REARM_ACCOUNT_BINDING_UNAVAILABLE")
        if await gateway.connector.get_account_uid() != uid:
            raise ValueError("ACCOUNT_UID_MISMATCH")
        self._account_uid_verified = True
        reservations.assert_healthy()
        if (wal.path != path / "intents.json" or reservations.path != path / "reservations.json"
                or wal.verified_disk().all_records() != wal.all_records()
                or reservations.verified_disk().reservation_snapshot()
                != reservations.reservation_snapshot()):
            raise ValueError("REARM_JOURNALS_DISAGREE")
        self._verify_quote_action_recovery(path, manager, wal, reservations)
        if not self._quote_action_recovery_ready:
            raise ValueError("REARM_ACTION_SCOPE_UNAVAILABLE")
        self._runner_halt_ok = self._halt_runner_orders()
        gateway.runner_scope_check = self._runner_executor_scope_complete
        current = manager.current_session
        if current is None:
            raise ValueError("REARM_SESSION_UNAVAILABLE")
        scopes = {(record.session_id, record.epoch) for record in wal.all_records()}
        scopes.add((current.session_id, current.epoch))
        started = time.monotonic()
        for session_id, epoch in sorted(scopes):
            result = await gateway.reconcile(session_id, epoch)
            age = (manager.wall_clock() - result.observed_at).total_seconds() * 1000
            if (not result.scope_complete or not result.trade_events_reconciled
                    or result.open_order_ids or result.pending_cancel_ids or result.unknown_order_ids
                    or (result.session_id, result.epoch) != (session_id, epoch)
                    or not 0 <= age <= manager.max_reconciliation_age_ms):
                raise ValueError("REARM_ACCOUNT_UNRESOLVED")
        if (await gateway.connector.get_account_uid() != uid
                or self.config.recovery_account_uid != uid or self.config.recovery_state_dir != directory
                or self.config.model_dump_json() != config_snapshot
                or self._order_safety_gateway is not gateway or self._order_safety_manager is not manager
                or self._order_safety_wal is not wal or self._order_safety_reservations is not reservations
                or self._order_safety_stopped
                or self.order_safety_task is not None and not self.order_safety_task.done()
                or ownership._fd is None or not manager.can_reduce()
                or account.bills.approvals != CashflowApprovals.load(path / "cashflows.json")
                or manager.store.load() != manager._journal
                or (time.monotonic() - started) * 1000 > manager.max_reconciliation_age_ms
                or any(record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") for record in wal.all_records())
                or any(not reservations.is_terminal_intent(key) for key in reservations.reservation_ids)
                or not self.verify_runner_fill_events() or not self._fill_attribution_ready()):
            raise ValueError("REARM_PROOF_INVALIDATED")
        reservations.assert_healthy()
        if (wal.verified_disk().all_records() != wal.all_records()
                or reservations.verified_disk().reservation_snapshot()
                != reservations.reservation_snapshot()):
            raise ValueError("REARM_JOURNALS_DISAGREE")
        self._verify_quote_action_recovery(path, manager, wal, reservations)
        if not self._quote_action_recovery_ready or not self._runner_executor_scope_complete():
            raise ValueError("REARM_RUNNER_SCOPE_UNAVAILABLE")

    @repository_context
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

    @repository_context
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
        if (self._spot_exit_planner is not None
                and attributor.exit_budget is not self._spot_exit_planner.budget):
            raise ValueError("FILL_EXIT_BINDING_INVALID")
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
                    selected = (attributor.exit_budget if attributor.exit_budget is not None
                                and attributor.exit_budget.matches_intent_session(
                                    intent_id, attributor.wal.get(intent_id).session_id) else subsidy)
                    selected.settle_zero_fill_terminal(intent_id, wal=attributor.wal,
                                                       reservations=attributor.reservations)
                return True

            gateway.on_terminal_reconciled = settle_service_terminal
        if bills is not None:
            bills.on_cashflows_applied = attributor.apply_approved_cashflows
        self._fill_attributor = attributor
        self.fill_attribution_reason_code = "FILL_ATTRIBUTION_REVALIDATION_REQUIRED"

    @repository_context
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

    @repository_context
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

    @repository_context
    def install_inventory_executor(self, execution) -> None:
        from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryExecutor
        if (self._inventory_executor is not None or self._spot_exit_planner is not None
                or not isinstance(execution, InventoryExecutor) or execution.controller is not self
                or self._fill_attributor is None or self._fill_attributor.exit_budget is not execution.budget):
            raise ValueError("INVENTORY_EXECUTOR_BINDING_INVALID")
        if (self.config.recovery_state_dir is not None and execution.journal.path.resolve()
                != (Path(self.config.recovery_state_dir) / "inventory_mode.json").resolve()):
            raise ValueError("INVENTORY_RECOVERY_PATH_INVALID")
        manager = self._order_safety_manager
        if manager._inventory_reconciliation != self._inventory_successor_ready:
            manager.bind_inventory_reconciliation(self._inventory_successor_ready)
        self._inventory_executor = execution
        self._fill_attributor.inventory_intent = execution.owns_intent

    @repository_context
    def _pre_send_provenance_complete(self, missing_records) -> bool:
        """Verify account-bound journals when the late executor checkpoint is absent."""
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.pre_send_provenance_complete(missing_records)
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
            wal.assert_verified()
            reservations.verify_memory()
            by_intent = {claim.intent_id: claim for claim in claims}
            if len(by_intent) != len(claims):
                return False
            for record in missing_records:
                claim = by_intent.get(record.intent_id)
                reservation = reservations.reservation_state(record.reservation_id)
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

    @repository_context
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

    @repository_context
    def _on_runner_order_filled_sync(self, event):
        if not isinstance(event, OrderFilledEvent):
            self._runner_scope_invalid = True
            return
        if event.trading_pair != self.config.strategy.spot.pair:
            return
        if self._joint_recovery is not None:
            try:
                self._protected_swap_sender.wal.find_by_client_order_id(event.order_id)
            except KeyError:
                pass
            else:
                try:
                    self._joint_recovery.observe_runner_fill(event)
                except Exception:
                    self._runner_scope_invalid = True
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

    @repository_context
    def _on_runner_order_canceled_sync(self, event):
        if not isinstance(event, OrderCancelledEvent):
            self._runner_scope_invalid = True
            return
        if self._joint_recovery is not None:
            try:
                self._protected_swap_sender.wal.find_by_client_order_id(event.order_id)
            except KeyError:
                pass
            else:
                try:
                    self._joint_recovery.observe_runner_cancel(event)
                except Exception:
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

    @repository_context
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

    @repository_context
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

    @repository_context
    def _joint_successor_ready(self, session_id, epoch):
        return self._joint_recovery is not None and self._joint_recovery.successor_ready(session_id, epoch)

    @repository_context
    def _joint_recovery_ready(self):
        if self._joint_recovery is not None:
            return self._joint_recovery.allocation_ready()
        return not self._joint_recovery_restore_required

    @repository_context
    def _carry_ready(self, prospective: CapitalClaim | None = None) -> bool:
        if self._carry_monitor is None:
            return True  # Legacy offline fixtures; production permission remains disabled.
        decision = self._carry_monitor.check(prospective)
        self.carry_reason_code = decision.reason_code
        return decision.allowed

    @repository_context
    def _carry_stops(self):
        """Request cancellation without inferring position closure or releasing any hold."""
        records = self._order_safety_wal.all_records() if self._order_safety_wal is not None else ()
        if self._protected_swap_sender is not None:
            records += self._protected_swap_sender.wal.all_records()
        return [StopExecutorAction(controller_id=self.config.id, executor_id=r.intent_id)
                for r in records if r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") and not r.cancel_requested]

    @repository_context
    def install_spot_risk_binding(self, binding: SpotRiskBinding) -> None:
        if (self._spot_risk_binding is not None or not isinstance(binding, SpotRiskBinding)
                or binding.attributor is not self._fill_attributor
                or not binding.matches_config(self.config)):
            raise ValueError("SPOT_RISK_BINDING_INVALID")
        self._spot_risk_binding = binding

    @repository_context
    def install_fee_rate_source(self, cache: CachedFeeRateSource) -> None:
        planner = self._quote_action_planner
        binding = planner.fee_binding if planner is not None else None
        if (self._fee_rate_cache is not None or not isinstance(cache, CachedFeeRateSource)
                or not isinstance(cache.source, OkxFeeRateSource) or binding is None
                or getattr(binding.snapshot, "__self__", None) is not cache
                or not planner._fee_binding_matches_config(binding, self.config)
                or cache.refresh_interval_ms > binding.max_age_ms
                or cache.source.market_data_provider is not self.market_data_provider
                or cache.source.account_id != binding.account_id
                or cache.source.connector_name != binding.connector_name
                or cache.source.instrument.get("instType") != "SPOT"
                or cache.source.notional_currency != "USDT"
                or cache.source.instrument.get("instId") != binding.instrument_id
                or cache.source.instrument.get("groupId") != binding.group_id):
            raise ValueError("FEE_REFRESH_BINDING_INVALID")
        self._fee_rate_cache = cache

    @repository_context
    def _spot_risk_ready(self) -> bool:
        binding = self._spot_risk_binding
        configured = self.config.strategy.risk
        if binding is None:
            return configured.rolling_fill_window is None and configured.stress_loss_budget_quote is None
        try:
            return (binding.matches_config(self.config)
                    and binding.check(None, binding.attributor.reservations.preview()) is None)
        except Exception:
            return False

    @repository_context
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

    @repository_context
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

    @repository_context
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

    @repository_context
    def _hedge_ready(self) -> bool:
        if not self.config.strategy.perpetual.enabled:
            return True
        if self._hedge_coordinator is not None:
            ready = self._hedge_coordinator.allows_spot()
            self.hedge_reason_code = self._hedge_coordinator.reason_code
            return ready
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

    @repository_context
    def _runtime_risk_ready(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('risk')
        gate = self._runtime_risk_gate
        if gate is None:
            return True  # Production permission remains disabled separately.
        try:
            if not gate.journal_verified():
                decision = gate.invalidate("SAFETY_JOURNAL_UNAVAILABLE")
                self.runtime_risk_reason_code = decision.reason_code
                return False
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
                monitor = self._joint_recovery or self._capital_risk_monitor
                if monitor is None:
                    decision = gate.evaluate(observed)
                else:
                    capital = monitor.measure()
                    decision = (gate.invalidate("CAPITAL_VALUATION_UNAVAILABLE") if capital is None
                                else gate.evaluate(replace(observed, drawdown_bps=capital.drawdown_bps)))
        except Exception:
            decision = gate.invalidate("RISK_OBSERVATION_UNAVAILABLE")
        self.runtime_risk_reason_code = decision.reason_code
        return decision.state == "NORMAL" or (
            decision.state == "DEGRADED" and self.recovery_probe_active()
            and self._recovery_probe_guard.capacity_available())

    @repository_context
    def install_recovery_probe_guard(self, guard: RecoveryProbeGuard) -> None:
        if (self._recovery_probe_guard is not None or not isinstance(guard, RecoveryProbeGuard)
                or guard.reservations is not self._order_safety_reservations):
            raise ValueError("RECOVERY_PROBE_BINDING_INVALID")
        self._recovery_probe_guard = guard
        if not self._recovery_probe_policy_matches():
            self._recovery_probe_guard = None
            raise ValueError("RECOVERY_PROBE_BINDING_INVALID")

    @repository_context
    def _execution_loss_ready(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('loss')
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

    @repository_context
    def _fill_attribution_ready(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('attribution')
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

    @repository_context
    def install_markout_probe_guard(self, guard: MarkoutProbeGuard) -> None:
        """Opt into a capped bootstrap only after both journals and a planner exist."""
        if (self._markout_probe_guard is not None or not isinstance(guard, MarkoutProbeGuard)
                or guard.monitor is not self._markout_monitor
                or guard.reservations is not self._order_safety_reservations
                or self._quote_action_planner is None):
            raise ValueError("MARKOUT_PROBE_BINDING_INVALID")
        self._markout_probe_guard = guard

    @repository_context
    def _markout_risk_ready(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('markout')
        monitor = self._markout_monitor
        if monitor is None:
            return True  # Production order permission remains disabled separately.
        try:
            ready = monitor.evaluate()
        except Exception:
            ready = False
        self.markout_reason_code = monitor.reason_code
        return ready or self.markout_probe_active()

    @repository_context
    def _inventory_successor_ready(self, session_id, epoch):
        return self._inventory_executor is not None and self._inventory_executor.allows_mm()

    @repository_context
    def _mm_mode_ready(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('mm')
        return self._inventory_executor is None or self._inventory_executor.allows_mm()

    @repository_context
    def _bounded_pending_ready(self):
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('bounded')
        return ((self._spot_exit_planner is not None and self._spot_exit_planner.pending_exit_ready())
                or (self._inventory_executor is not None and self._inventory_executor.pending_exit_ready()))

    @repository_context
    def allow_inventory_execution_actions(self):
        try:
            self._runtime_risk_ready()
            gate = self._runtime_risk_gate
            execution = self._inventory_executor
            watchdog = self.order_safety_watchdog_task
            return (execution is not None and not self._order_safety_stopped and not self._safety_rearm_in_progress
                    and gate is not None and gate.state in (("NORMAL", "DEGRADED") if execution.ledger.policy.side == "SELL" else ("NORMAL",))
                    and self._fill_attributor is not None and self._fill_attributor.exit_budget is execution.budget
                    and (execution.ledger.policy.side == "SELL" or self._spot_risk_ready() and self._markout_risk_ready())
                    and not self._runner_stops_pending() and not self._runner_scope_invalid
                    and self._joint_recovery_ready() and self._joint_risk_ready() and self._carry_ready()
                    and self._hedge_ready() and self._execution_loss_ready() and self._fill_attribution_ready()
                    and not self.has_unverified_runner_order_events() and self._spot_quote_gates_ready()
                    and (self._telemetry is None or self._telemetry.healthy)
                    and self._order_safety_manager.can_reduce()
                    and watchdog is not None and not watchdog.done()
                    and (self.order_safety_task is None or self.order_safety_task.done()))
        except Exception:
            return False

    @repository_context
    def install_spot_exit_planner(self, planner) -> None:
        from hummingbot.strategy_v2.life_liquidity.exit_actions import SpotExitPlanner
        if (self._spot_exit_planner is not None or self._inventory_executor is not None or not isinstance(planner, SpotExitPlanner)
                or planner.controller is not self
                or self._fill_attributor is not None and self._fill_attributor.exit_budget is not planner.budget):
            raise ValueError("EXIT_BINDING_INVALID")
        self._spot_exit_planner = planner

    @repository_context
    def _runner_stops_pending(self) -> bool:
        if not self._runner_stop_revoked_ids:
            return False
        if self._order_safety_wal is None:
            return True
        records = self._order_safety_wal.active_records()
        if self._protected_swap_sender is not None:
            records += self._protected_swap_sender.wal.active_records()
        return any(record.intent_id in self._runner_stop_revoked_ids
                   and record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")
                   for record in records)

    @repository_context
    def allow_risk_reduction_actions(self) -> bool:
        runtime = self._storage_runtime
        if runtime is not None and runtime.application is not None:
            return runtime.application.check_financial_gate('reduction')
        try:
            self._runtime_risk_ready()  # Revalidate observations/journal and latch any HALT.
            gate = self._runtime_risk_gate
            watchdog = self.order_safety_watchdog_task
            return (not self._runner_stops_pending()
                    and gate is not None and gate.state in ("NORMAL", "DEGRADED")
                    and self._fill_attributor is not None and self._spot_exit_planner is not None
                    and self._fill_attributor.exit_budget is self._spot_exit_planner.budget
                    and self._spot_quote_gates_ready() and self._fill_attribution_ready()
                    and self._joint_risk_ready() and self._hedge_ready()
                    and not self.has_unverified_runner_order_events()
                    and self._order_safety_manager.can_reduce()
                    and watchdog is not None and not watchdog.done()
                    and (self.order_safety_task is None or self.order_safety_task.done()))
        except Exception:
            return False

    @repository_context
    def allow_executor_action(self, action) -> bool:
        config = action.executor_config
        if getattr(config, "id", None) in self._runner_stop_revoked_ids:
            return False
        if self._is_swap_config(config):
            return self._protected_swap_sender is not None and self._protected_swap_sender.authorizes_config(config)
        if self.bounded_spot_planner(config) is not None:
            return self.bounded_spot_planner(config).authorizes_config(config)
        return self.allow_create_executor_actions()

    @repository_context
    def authorize_runner_create_action(self, action) -> bool:
        if self._is_swap_config(action.executor_config):
            return self._protected_swap_sender is not None and self._protected_swap_sender.authorizes_config(action.executor_config)
        if self.bounded_spot_planner(action.executor_config) is not None:
            return self.bounded_spot_planner(action.executor_config).authorizes_config(action.executor_config)
        planner = self._quote_action_planner
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            return False
        return planner is None or planner.authorizes_config(action.executor_config)

    @repository_context
    def _runner_executor_scope_complete(self, *, quiescent=True, verify_financial_events=True) -> bool:
        """Live and stored children must resolve to their protected product WAL."""
        from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
        from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
        from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

        executors = self._runner_executors()
        if (quiescent and not self._runner_halt_ok or self._runner_scope_invalid
                or executors is None or self._order_safety_wal is None):
            return False
        if verify_financial_events and not self.verify_runner_fill_events():
            return False

        def reject() -> bool:
            self._runner_scope_invalid = True
            return False

        records = self._order_safety_wal.all_records()
        if self._joint_recovery is not None:
            records += self._protected_swap_sender.wal.all_records()
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
                        or not self._runner_product_config_valid(executor.config)
                        or executor.status not in ((RunnableStatus.SHUTTING_DOWN, RunnableStatus.TERMINATED)
                                                   if quiescent else tuple(RunnableStatus))):
                    return reject()
                wire_ids = executor.recovery_order_ids()
                if (not quiescent and not wire_ids
                        and executor.config.connector_name == self.config.strategy.spot.connector):
                    planner = self._quote_action_planner
                    current = self._order_safety_manager.current_session
                    if planner is None or current is None:
                        return reject()
                    issued = planner._issued.get(executor.config.id)
                    bounded = self.bounded_spot_planner(executor.config)
                    captured = (issued == (current.session_id, current.epoch, current.config_version, executor.config)
                                or bounded is not None and bounded._configs.get(executor.config.id) == executor.config)
                    claim = planner.action_journal.verified_get(executor.config.id)
                    if (not captured or claim.state not in ("PROPOSED", "DISPATCHED")
                            or not planner._claim_matches_config(claim, executor.config,
                                                                 current.session_id, current.epoch, current.config_version)):
                        return reject()
                for wire_id in wire_ids:
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
                        or not self._runner_product_config_valid(info.config)
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
            seen_wires = active_seen | stored_seen
            missing = tuple(record for record in records
                            if record.slot_market is not None
                            and record.state != "ABORTED_BEFORE_SEND"
                            and record.client_order_id not in seen_wires)
            # The recorder row is written after the connector call. On a cold
            # restart, accept only a complete pre-send journal chain instead.
            if missing:
                spot_missing = tuple(r for r in missing if r.slot_market == self.config.strategy.spot.pair)
                swap_missing = tuple(r for r in missing if r.slot_market == "LIFE-USDT-SWAP")
                if (executors or len(spot_missing) + len(swap_missing) != len(missing)
                        or spot_missing and not self._pre_send_provenance_complete(spot_missing)
                        or swap_missing and (self._joint_recovery is None
                                             or not self._joint_recovery.runner_provenance_complete(swap_missing))):
                    return reject()
            self._runner_stored_executor_ids.update(stored_ids)
            self._runner_wire_owners.update(wire_owners)
        except Exception:
            return reject()
        return True

    @repository_context
    def install_telemetry(self, recorder: TelemetryRecorder, *, independent_value,
                          utc_clock_ms, max_value_age_ms: int) -> None:
        """Opt-in diagnostics with buffered writes and memory-only cancel events.

        Explicit SNAPSHOT events read financial journals; order hooks record decision facts.
        The adapter must flush outside the safety loop. Missing observations
        stay explicit. Installing diagnostics never enables production orders.
        """
        sender, gateway = self._protected_spot_sender, self._order_safety_gateway
        if (self._telemetry is not None or not isinstance(recorder, TelemetryRecorder)
                or not recorder.healthy or not callable(independent_value) or not callable(utc_clock_ms)
                or type(max_value_age_ms) is not int or max_value_age_ms < 0
                or sender is None or gateway is None or gateway.event_observer is not None
                or sender.gateway.event_observer is not None):
            raise ValueError("TELEMETRY_BINDING_INVALID")
        self._telemetry = recorder
        self._telemetry_value = independent_value
        self._telemetry_utc_ms = utc_clock_ms
        self._telemetry_max_value_age_ms = max_value_age_ms
        gateway.event_observer = self._record_telemetry
        sender.gateway.event_observer = self._record_telemetry
        self.telemetry_reason_code = "TELEMETRY_READY"

    @repository_context
    def _session_create_ready(self) -> bool:
        manager = self._order_safety_manager
        if manager is None:
            return True
        watchdog = self.order_safety_watchdog_task
        return bool(manager.state == "ACTIVE" and watchdog is not None and not watchdog.done()
                    and not (self.order_safety_task is not None and not self.order_safety_task.done()))

    @repository_context
    def allow_create_executor_actions(self) -> bool:
        if not self._mm_mode_ready() or not self._joint_recovery_ready():
            return False
        checks = (
            ("telemetry", lambda: self._telemetry is None or self._telemetry.healthy, "TELEMETRY_UNAVAILABLE"),
            ("runtime", self._runtime_risk_ready, "RUNTIME_RISK_UNAVAILABLE"),
            ("stops", lambda: not self._runner_stops_pending(), "RUNNER_STOP_PENDING"),
            ("spot_risk", self._spot_risk_ready, "SPOT_RISK_UNAVAILABLE"),
            ("joint", self._joint_risk_ready, "JOINT_RISK_UNAVAILABLE"),
            ("hedge", self._hedge_ready, "HEDGE_UNAVAILABLE"),
            ("loss", self._execution_loss_ready, "EXECUTION_LOSS_UNAVAILABLE"),
            ("accounting", self._fill_attribution_ready, "ATTRIBUTION_UNAVAILABLE"),
            ("markout", self._markout_risk_ready, "MARKOUT_UNAVAILABLE"),
            ("runner_events", lambda: not self.has_unverified_runner_order_events(), "RUNNER_EVENTS_UNVERIFIED"),
            ("session", self._session_create_ready, "SESSION_OR_WATCHDOG_UNAVAILABLE"),
            ("market", self._spot_quote_gates_ready, "SPOT_DATA_UNAVAILABLE"),
        )
        if self._carry_monitor is not None:
            checks = (("carry", self._carry_ready, "CARRY_UNAVAILABLE"), *checks)
        self._permission_gates = {}
        for name, check, reason in checks:
            passed = check() is True
            self._permission_gates[name] = passed
            if not passed:
                if name == "runtime":
                    reason = self.runtime_risk_reason_code
                self._record_telemetry("PERMISSION", allowed=False, reason_code=reason)
                return False
        return self._record_telemetry("PERMISSION", allowed=True, reason_code="QUOTE_PERMISSION_READY")

    @repository_context
    def _update_processed_data_sync(self):
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

    @repository_context
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

    @repository_context
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
        if (observed.market_anchor_usdt is not None
                and observed.market_anchor_usdt != decision.price_usdt):
            return reject("LIFE_MARKET_ANCHOR_CHANGED")
        return True

    @repository_context
    def determine_executor_actions(self):
        if not self._carry_ready():
            return self._carry_stops()
        if self.config.recovery_state_dir is not None and not self._quote_action_recovery_ready:
            return []
        planner = self._quote_action_planner
        coordinator = self._hedge_coordinator
        if coordinator is not None:
            actions = coordinator.propose()
            if actions or not coordinator.allows_spot():
                return actions
        return planner.propose() if planner is not None else []

    @repository_context
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

    @repository_context
    def bounded_probe_active(self) -> bool:
        return self.recovery_probe_active() or self.markout_probe_active()

    @repository_context
    def quote_probe_authorizes(self, side: str, quantity: Decimal, *,
                               exclude_open_intent: SpotIntent | None = None) -> bool:
        recovery_allowed = (not self.recovery_probe_active() or self._recovery_probe_guard.authorizes(
            side, quantity, exclude_open_intent=exclude_open_intent))
        markout_allowed = (not self.markout_probe_active() or self.markout_probe_authorizes(
            side, quantity, exclude_open_intent=exclude_open_intent))
        return recovery_allowed and markout_allowed

    @repository_context
    def markout_probe_active(self) -> bool:
        guard = self._markout_probe_guard
        return (guard is not None and self._quote_action_planner is not None
                and guard.capacity_available())

    @repository_context
    def markout_probe_authorizes(self, side: str, quantity_base: Decimal, *,
                                 exclude_open_intent: SpotIntent | None = None) -> bool:
        guard = self._markout_probe_guard
        return (guard is not None and guard.authorizes(
            side, quantity_base, exclude_open_intent=exclude_open_intent))
