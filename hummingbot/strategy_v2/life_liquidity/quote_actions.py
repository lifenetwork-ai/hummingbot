"""Opt-in conversion of qualified spot quote plans into bounded runner actions.

This adapter accepts an explicitly supplied, short-lived planning snapshot. The
production LIFE controller does not install a snapshot source or enable order
creation. A proposal is advisory until the protected sender repeats its gates.
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal, QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetStatus
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.slots import SpotQuoteSlots
from hummingbot.strategy_v2.life_liquidity.spot_quotes import (
    AdaptiveQuotePolicy,
    AdaptiveQuoteSignals,
    QuoteCosts,
    SpotQuotePlan,
    plan_spot_quotes,
)
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@dataclass(frozen=True)
class QuotePlanningSnapshot:
    session_id: str
    epoch: int
    config_version: int
    observed_monotonic: float
    expires_monotonic: float
    reference_ready: bool
    all_gates_ready: bool
    market_reference_ready: bool
    qualified_reference_usdt: Decimal
    qualified_exit_value_usdt: Decimal
    best_bid_usdt: Decimal
    best_ask_usdt: Decimal
    rules: InstrumentRules
    costs: QuoteCosts
    policy: EconomicPolicy
    subsidy_remaining_quote: Decimal | None = None
    min_depth_base_per_side: Decimal | None = None
    adaptive_policy: AdaptiveQuotePolicy | None = None
    adaptive_signals: AdaptiveQuoteSignals | None = None
    max_campaign_filled_base: Decimal | None = None
    loss_budget_status: LossBudgetStatus | None = None
    book_sequence_id: int | None = None
    reference_model_version: str | None = None


class QuoteActionPlanner:
    def __init__(self, controller, *, wal: IntentWAL, reservations: ReservationLedger,
                 snapshot: Callable[[], QuotePlanningSnapshot],
                 monotonic_clock: Callable[[], float],
                 intent_id_factory: Callable[[], str], max_actions_per_tick: int,
                 reference_engine: ReferenceEngine | None = None):
        if (not isinstance(max_actions_per_tick, int) or isinstance(max_actions_per_tick, bool)
                or max_actions_per_tick <= 0):
            raise ValueError("QUOTE_ACTION_LIMIT_INVALID")
        self.controller = controller
        self.manager = controller._order_safety_manager
        self.wal = wal
        self.reservations = reservations
        self.snapshot = snapshot
        self.monotonic_clock = monotonic_clock
        self.intent_id_factory = intent_id_factory
        self.max_actions_per_tick = max_actions_per_tick
        self.reference_engine = reference_engine
        self.slots = SpotQuoteSlots(wal, market=controller.config.strategy.spot.pair)
        self.action_journal = QuoteActionJournal(
            wal.path.with_name("quote_actions.json"),
            account_uid=controller.config.recovery_account_uid)
        self._proposed: dict[tuple[str, int, str, int], str] = {}
        self._issued: dict[str, tuple[str, int, int, OrderExecutorConfig]] = {}
        self.last_plan: SpotQuotePlan | None = None
        self.last_qualified_snapshot: QuotePlanningSnapshot | None = None
        self.reason_code = "QUOTE_ACTIONS_NOT_EVALUATED"

    def _journal_consistent(self, session_id: str, epoch: int) -> bool:
        records = self.wal.all_records()
        reservation_ids = self.reservations.reservation_ids
        expected_ids = set()
        for record in records:
            active = record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")
            if active and (record.slot_market is None or record.session_id != session_id
                           or record.epoch != epoch or record.state == "PREPARED"):
                return False
            if record.state == "ABORTED_BEFORE_SEND":
                if record.reservation_id in reservation_ids:
                    if not self.reservations.is_terminal_intent(record.reservation_id):
                        return False
                    expected_ids.add(record.reservation_id)
            else:
                expected_ids.add(record.reservation_id)
                if record.reservation_id not in reservation_ids:
                    return False
                if self.reservations.is_terminal_intent(record.reservation_id) != (record.state == "TERMINAL"):
                    return False
        return expected_ids == reservation_ids

    def _runner_occupied(self) -> set[tuple[str, int]] | None:
        executors = self.controller._runner_executors()
        if executors is None or self.controller._runner_scope_invalid:
            return None
        occupied = set()
        record_by_id = {record.intent_id: record for record in self.wal.all_records()}
        for executor in executors:
            config = getattr(executor, "config", None)
            status = getattr(executor, "status", None)
            if (not isinstance(config, OrderExecutorConfig)
                    or config.controller_id != self.controller.config.id
                    or config.connector_name != self.controller.config.strategy.spot.connector
                    or config.trading_pair != self.slots.market
                    or config.side not in (TradeType.BUY, TradeType.SELL)
                    or not isinstance(config.level_id, str)
                    or re.fullmatch(r"0|[1-9][0-9]*", config.level_id) is None
                    or status not in tuple(RunnableStatus)):
                return None
            try:
                level = int(config.level_id)
            except ValueError:
                return None
            record = record_by_id.get(config.id)
            if record is None and config.id not in self._issued:
                return None
            if (record is not None and (record.slot_market, record.slot_side, record.slot_level)
                    != (self.slots.market, config.side.name, level)):
                return None
            if status != RunnableStatus.TERMINATED:
                occupied.add((config.side.name, level))
        return occupied

    def _release_reconciled_proposals(self) -> None:
        for claim in self.action_journal.active_records():
            intent_id = claim.intent_id
            try:
                record = self.wal.get(intent_id)
            except KeyError:
                continue  # The runner may still have an unaccepted action queued.
            if (record.state == "TERMINAL" and self.reservations.is_terminal_intent(intent_id)
                    or record.state == "ABORTED_BEFORE_SEND"
                    and (intent_id not in self.reservations.reservation_ids
                         or self.reservations.is_terminal_intent(intent_id))):
                self.action_journal.transition(intent_id, expected=claim.state, state="RECONCILED")
                self._proposed.pop((claim.session_id, claim.epoch, claim.side, claim.level), None)
                self._issued.pop(intent_id, None)

    def _claim_matches_config(self, claim: QuoteActionRecord, config: OrderExecutorConfig,
                              session_id: str, epoch: int, config_version: int) -> bool:
        return (claim.controller_id == self.controller.config.id
                and claim.market == self.slots.market
                and (claim.session_id, claim.epoch, claim.config_version)
                == (session_id, epoch, config_version)
                and claim.side == config.side.name
                and str(claim.level) == config.level_id)

    def _claims_consistent(self, claims: tuple[QuoteActionRecord, ...]) -> bool:
        wal_by_id = {record.intent_id: record for record in self.wal.all_records()}
        reservation_ids = self.reservations.reservation_ids
        for claim in claims:
            if claim.controller_id != self.controller.config.id or claim.market != self.slots.market:
                return False
            record = wal_by_id.get(claim.intent_id)
            if claim.state == "REJECTED" and (record is not None or claim.intent_id in reservation_ids):
                return False
            if record is not None and (record.session_id, record.epoch, record.reservation_id,
                                       record.slot_market, record.slot_side, record.slot_level) != (
                    claim.session_id, claim.epoch, claim.intent_id,
                    claim.market, claim.side, claim.level):
                return False
            if claim.state == "RECONCILED" and record is not None and record.state not in (
                    "TERMINAL", "ABORTED_BEFORE_SEND"):
                return False
        return True

    def on_runner_action_rejected(self, action: CreateExecutorAction) -> bool:
        """Release only an exact action discarded by the runner before creation."""
        if not isinstance(action, CreateExecutorAction):
            return False
        config = action.executor_config
        issued = self._issued.get(getattr(config, "id", None))
        if issued is None or issued[3] != config or action.controller_id != self.controller.config.id:
            return False
        try:
            claim = self.action_journal.verified_get(config.id)
            executors = self.controller._runner_executors()
            if (claim.state != "PROPOSED"
                    or not self._claim_matches_config(claim, config, *issued[:3])
                    or executors is None
                    or self.controller._runner_scope_invalid
                    or config.id in self.reservations.reservation_ids
                    or any(record.intent_id == config.id for record in self.wal.all_records())
                    or any(getattr(getattr(executor, "config", None), "id", None) == config.id
                           for executor in executors)):
                return False
            self.action_journal.transition(config.id, expected="PROPOSED", state="REJECTED")
        except (KeyError, OSError, ValueError):
            return False
        self._proposed.pop((claim.session_id, claim.epoch, claim.side, claim.level), None)
        self._issued.pop(config.id, None)
        return True

    def on_runner_action_dispatched(self, action: CreateExecutorAction) -> bool:
        """Record observed executor creation; uncertainty remains claimed."""
        if not isinstance(action, CreateExecutorAction):
            return False
        config = action.executor_config
        issued = self._issued.get(getattr(config, "id", None))
        if issued is None or issued[3] != config or action.controller_id != self.controller.config.id:
            return False
        try:
            executors = self.controller._runner_executors()
            if (executors is None or self.controller._runner_scope_invalid
                    or not any(getattr(getattr(executor, "config", None), "id", None) == config.id
                               for executor in executors)):
                return False
            claim = self.action_journal.verified_get(config.id)
            if not self._claim_matches_config(claim, config, *issued[:3]):
                return False
            self.action_journal.transition(config.id, expected="PROPOSED", state="DISPATCHED")
            return True
        except (KeyError, OSError, ValueError):
            return False

    def authorizes_config(self, config: OrderExecutorConfig) -> bool:
        if not isinstance(config, OrderExecutorConfig):
            return False
        current = self.manager.current_session if self.manager is not None else None
        issued = self._issued.get(config.id)
        if (current is None or issued is None
                or issued != (current.session_id, current.epoch,
                              current.config_version, config)
                or self.controller.allow_create_executor_actions() is not True):
            return False
        try:
            claim = self.action_journal.verified_get(config.id)
            if (claim.state not in ("PROPOSED", "DISPATCHED")
                    or not self._claim_matches_config(claim, config, *issued[:3])):
                return False
        except (KeyError, OSError, ValueError):
            return False
        if any(record.intent_id == config.id for record in self.wal.all_records()):
            return False
        observed = self._current_snapshot(current)
        if observed is None or not self._journal_consistent(current.session_id, current.epoch):
            return False
        try:
            level = int(config.level_id)
            quotes = self.controller.config.strategy.quotes
            single_level = QuotesConfig(
                spreads_bps=(quotes.spreads_bps[level],),
                sizes_base=(quotes.sizes_base[level],),
                buy_taper_start_base=quotes.buy_taper_start_base,
                buy_block_base=quotes.buy_block_base)
            plan = self._plan(observed, current, quotes=single_level,
                              sides=(config.side.name,))
        except Exception:
            return False
        # Recheck this incremental order against reservations already held by
        # earlier actions, without previewing those same levels a second time.
        matches = any(candidate.side == config.side.name
                      and candidate.price_usdt == config.price
                      and candidate.quantity_base == config.amount
                      for candidate in plan.candidates)
        return (matches and (not self.controller.markout_probe_active()
                             or self.controller.markout_probe_authorizes(
                                 config.side.name, config.amount)))

    def authorizes_permit(self, permit: SendPermit) -> bool:
        """Reprice one already-reserved quote at the final network boundary."""
        if not isinstance(permit, SendPermit):
            return False
        current = self.manager.current_session if self.manager is not None else None
        issued = self._issued.get(permit.intent_id)
        if (current is None or issued is None
                or issued[:3] != (current.session_id, current.epoch,
                                  current.config_version)
                or (permit.session_id, permit.epoch, permit.config_version)
                != issued[:3] or permit.reservation_id != permit.intent_id):
            return False
        try:
            claim = self.action_journal.verified_get(permit.intent_id)
            if (claim.state not in ("PROPOSED", "DISPATCHED")
                    or not self._claim_matches_config(claim, issued[3], *issued[:3])):
                return False
        except (KeyError, OSError, ValueError):
            return False
        config = issued[3]
        if (permit.price_usdt != config.price or permit.quantity_base != config.amount
                or config.side not in (TradeType.BUY, TradeType.SELL)):
            return False
        observed = self._current_snapshot(current, pre_send_intent_id=permit.intent_id)
        if observed is None:
            return False
        try:
            level = int(config.level_id)
            quotes = self.controller.config.strategy.quotes
            single_level = QuotesConfig(
                spreads_bps=(quotes.spreads_bps[level],),
                sizes_base=(quotes.sizes_base[level],),
                buy_taper_start_base=quotes.buy_taper_start_base,
                buy_block_base=quotes.buy_block_base)
            intent = SpotIntent(config.id, config.side.name, config.amount,
                                config.price, current.session_id, current.epoch)
            if not self.reservations.matches_open_intent(intent):
                return False
            plan = self._plan(observed, current, quotes=single_level,
                              sides=(config.side.name,), exclude_open_intent=intent)
        except Exception:
            return False
        matches = any(candidate.side == config.side.name
                      and candidate.price_usdt == permit.price_usdt
                      and candidate.quantity_base == permit.quantity_base
                      for candidate in plan.candidates)
        return (matches and (not self.controller.markout_probe_active()
                             or self.controller.markout_probe_authorizes(
                                 config.side.name, permit.quantity_base,
                                 exclude_open_intent=intent)))

    def session_snapshot(self) -> QuotePlanningSnapshot | None:
        """Qualified snapshot for the safety tick, even after a reversible pause."""
        current = self.manager.current_session if self.manager is not None else None
        if current is None:
            self.last_qualified_snapshot = None
            return None
        return self._current_snapshot(current, require_active=False)

    def _current_snapshot(self, current, *, pre_send_intent_id: str | None = None,
                          require_active: bool = True) -> QuotePlanningSnapshot | None:
        self.last_qualified_snapshot = None
        try:
            observed = self.snapshot()
            now = self.monotonic_clock()
            if (not isinstance(observed, QuotePlanningSnapshot)
                    or not isinstance(now, (int, float)) or isinstance(now, bool)
                    or not math.isfinite(now)
                    or not isinstance(observed.observed_monotonic, (int, float))
                    or not isinstance(observed.expires_monotonic, (int, float))
                    or not math.isfinite(observed.observed_monotonic)
                    or not math.isfinite(observed.expires_monotonic)
                    or observed.observed_monotonic > now
                    or now >= observed.expires_monotonic
                    or (observed.session_id, observed.epoch, observed.config_version)
                    != (current.session_id, current.epoch, current.config_version)
                    or not isinstance(observed.policy, EconomicPolicy)
                    or observed.policy.objective != self.controller.config.strategy.economics.objective
                    or not isinstance(observed.rules, InstrumentRules)
                    or not isinstance(observed.costs, QuoteCosts)
                    or (self.controller._execution_loss_budget is not None
                        and observed.loss_budget_status != self.controller.execution_loss_status)
                    or not self.controller.quote_reference_matches(
                        observed, self.reference_engine,
                        pre_send_intent_id=pre_send_intent_id)
                    or observed.reference_ready is not True
                    or observed.all_gates_ready is not True
                    or (require_active and not self.manager.can_quote(
                        reference_ready=True, all_gates_ready=True,
                        market_reference_ready=observed.market_reference_ready is True))):
                return None
        except Exception:
            return None
        self.last_qualified_snapshot = observed
        return observed

    def _plan(self, observed: QuotePlanningSnapshot, current,
              *, quotes: QuotesConfig | None = None,
              sides: tuple[str, ...] = ("BUY", "SELL"),
              exclude_open_intent: SpotIntent | None = None) -> SpotQuotePlan:
        return plan_spot_quotes(
            session_id=current.session_id, epoch=current.epoch,
            qualified_reference_usdt=observed.qualified_reference_usdt,
            qualified_exit_value_usdt=observed.qualified_exit_value_usdt,
            best_bid_usdt=observed.best_bid_usdt, best_ask_usdt=observed.best_ask_usdt,
            quotes=quotes or self.controller.config.strategy.quotes,
            rules=observed.rules, costs=observed.costs, policy=observed.policy,
            reservations=self.reservations,
            subsidy_remaining_quote=observed.subsidy_remaining_quote,
            min_depth_base_per_side=observed.min_depth_base_per_side,
            adaptive_policy=observed.adaptive_policy,
            adaptive_signals=observed.adaptive_signals,
            max_campaign_filled_base=observed.max_campaign_filled_base,
            loss_budget_status=observed.loss_budget_status,
            sides=sides, exclude_open_intent=exclude_open_intent)

    def propose(self) -> list[CreateExecutorAction]:
        self.reason_code = "QUOTE_ACTION_PERMISSION_UNAVAILABLE"
        if (self.manager is None or self.controller.allow_create_executor_actions() is not True
                or self.controller._protected_spot_sender is None):
            return []
        current = self.manager.current_session
        if current is None:
            return []
        observed = self._current_snapshot(current)
        if observed is None:
            self.reason_code = "QUOTE_ACTION_SNAPSHOT_INVALID"
            return []
        if not self._journal_consistent(current.session_id, current.epoch):
            self.reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
            return []
        try:
            self._release_reconciled_proposals()
            claims = self.action_journal.verified_records()
        except (OSError, ValueError, KeyError):
            self.reason_code = "QUOTE_ACTION_JOURNAL_UNAVAILABLE"
            return []
        if not self._claims_consistent(claims):
            self.reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
            return []
        active_claims = tuple(claim for claim in claims
                              if claim.state in ("PROPOSED", "DISPATCHED"))
        probe_mode = self.controller.markout_probe_active()
        if probe_mode and active_claims:
            self.reason_code = "MARKOUT_PROBE_IN_FLIGHT"
            return []
        if any((claim.session_id, claim.epoch, claim.config_version)
               != (current.session_id, current.epoch, current.config_version)
               for claim in active_claims):
            self.reason_code = "QUOTE_ACTION_DISPATCH_UNRESOLVED"
            return []
        occupied = self._runner_occupied()
        if occupied is None:
            self.reason_code = "QUOTE_ACTION_RUNNER_SCOPE_UNAVAILABLE"
            return []
        try:
            self.last_plan = self._plan(observed, current)
        except Exception:
            self.reason_code = "QUOTE_ACTION_PLAN_INVALID"
            return []
        actions = []
        proposed = {}
        used_ids = ({record.intent_id for record in self.wal.all_records()}
                    | {record.intent_id for record in claims}
                    | set(self._issued))
        active_slots = {claim.slot for claim in active_claims}
        for candidate in self.last_plan.candidates:
            if probe_mode and (actions or not self.controller.markout_probe_authorizes(
                    candidate.side, candidate.quantity_base)):
                continue
            slot = (current.session_id, current.epoch, candidate.side, candidate.level)
            if ((self.slots.market, candidate.side, candidate.level) in active_slots
                    or (candidate.side, candidate.level) in occupied):
                continue
            if self.slots.status(current.session_id, current.epoch,
                                 candidate.side, candidate.level).state != "FREE":
                continue
            if len(actions) >= self.max_actions_per_tick:
                break
            try:
                intent_id = self.intent_id_factory()
            except Exception:
                self.reason_code = "QUOTE_ACTION_ID_UNAVAILABLE"
                return []
            if not isinstance(intent_id, str) or not intent_id or intent_id in used_ids:
                self.reason_code = "QUOTE_ACTION_ID_INVALID"
                return []
            used_ids.add(intent_id)
            config = OrderExecutorConfig(
                id=intent_id, controller_id=self.controller.config.id,
                connector_name=self.controller.config.strategy.spot.connector,
                trading_pair=self.slots.market,
                side=TradeType.BUY if candidate.side == "BUY" else TradeType.SELL,
                amount=candidate.quantity_base, price=candidate.price_usdt,
                execution_strategy=ExecutionStrategy.LIMIT_MAKER,
                level_id=str(candidate.level))
            actions.append(CreateExecutorAction(controller_id=self.controller.config.id,
                                                executor_config=config))
            proposed[slot] = intent_id
        if actions:
            claims = [QuoteActionRecord(
                intent_id=action.executor_config.id, controller_id=self.controller.config.id,
                session_id=current.session_id, epoch=current.epoch,
                config_version=current.config_version, market=self.slots.market,
                side=action.executor_config.side.name,
                level=int(action.executor_config.level_id)) for action in actions]
            try:
                self.action_journal.claim_batch(claims)
            except (OSError, ValueError):
                self.reason_code = "QUOTE_ACTION_JOURNAL_UNAVAILABLE"
                return []
        self._proposed.update(proposed)
        for action in actions:
            config = action.executor_config
            self._issued[config.id] = (current.session_id, current.epoch,
                                       current.config_version, config.model_copy(deep=True))
        self.reason_code = "QUOTE_ACTIONS_PROPOSED" if actions else "NO_NEW_QUOTE_ACTIONS"
        return actions
