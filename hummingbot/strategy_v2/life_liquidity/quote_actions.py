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
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.slots import SpotQuoteSlots
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts, SpotQuotePlan, plan_spot_quotes
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


class QuoteActionPlanner:
    def __init__(self, controller, *, wal: IntentWAL, reservations: ReservationLedger,
                 snapshot: Callable[[], QuotePlanningSnapshot],
                 monotonic_clock: Callable[[], float],
                 intent_id_factory: Callable[[], str], max_actions_per_tick: int):
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
        self.slots = SpotQuoteSlots(wal, market=controller.config.strategy.spot.pair)
        self._proposed: dict[tuple[str, int, str, int], str] = {}
        self._issued: dict[str, tuple[str, int, int, OrderExecutorConfig]] = {}
        self.last_plan: SpotQuotePlan | None = None
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
        for key, intent_id in tuple(self._proposed.items()):
            try:
                record = self.wal.get(intent_id)
            except KeyError:
                continue  # The runner may still have an unaccepted action queued.
            if (record.state == "TERMINAL" and self.reservations.is_terminal_intent(intent_id)
                    or record.state == "ABORTED_BEFORE_SEND"
                    and (intent_id not in self.reservations.reservation_ids
                         or self.reservations.is_terminal_intent(intent_id))):
                del self._proposed[key]
                self._issued.pop(intent_id, None)

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
        if any(record.intent_id == config.id for record in self.wal.all_records()):
            return False
        observed = self._current_snapshot(current)
        if observed is None or not self._journal_consistent(current.session_id, current.epoch):
            return False
        try:
            level = int(config.level_id)
            quotes = self.controller.config.strategy.quotes
            single_level = QuotesConfig(spreads_bps=(quotes.spreads_bps[level],),
                                        sizes_base=(quotes.sizes_base[level],))
            plan = self._plan(observed, current, quotes=single_level,
                              sides=(config.side.name,))
        except Exception:
            return False
        # Recheck this incremental order against reservations already held by
        # earlier actions, without previewing those same levels a second time.
        return any(candidate.side == config.side.name
                   and candidate.price_usdt == config.price
                   and candidate.quantity_base == config.amount
                   for candidate in plan.candidates)

    def _current_snapshot(self, current) -> QuotePlanningSnapshot | None:
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
                    or not self.manager.can_quote(
                        reference_ready=observed.reference_ready is True,
                        all_gates_ready=observed.all_gates_ready is True,
                        market_reference_ready=observed.market_reference_ready is True)):
                return None
        except Exception:
            return None
        return observed

    def _plan(self, observed: QuotePlanningSnapshot, current,
              *, quotes: QuotesConfig | None = None,
              sides: tuple[str, ...] = ("BUY", "SELL")) -> SpotQuotePlan:
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
            sides=sides)

    def propose(self) -> list[CreateExecutorAction]:
        self.reason_code = "QUOTE_ACTION_PERMISSION_UNAVAILABLE"
        if (self.manager is None or self.controller.allow_create_executor_actions() is not True
                or self.controller._protected_spot_sender is None):
            return []
        current = self.manager.current_session
        if current is None:
            return []
        for key, intent_id in tuple(self._proposed.items()):
            if key[:2] != (current.session_id, current.epoch):
                del self._proposed[key]
                self._issued.pop(intent_id, None)
        observed = self._current_snapshot(current)
        if observed is None:
            self.reason_code = "QUOTE_ACTION_SNAPSHOT_INVALID"
            return []
        if not self._journal_consistent(current.session_id, current.epoch):
            self.reason_code = "QUOTE_ACTION_JOURNALS_DISAGREE"
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
        self._release_reconciled_proposals()
        actions = []
        proposed = {}
        used_ids = {record.intent_id for record in self.wal.all_records()} | set(self._issued)
        for candidate in self.last_plan.candidates:
            slot = (current.session_id, current.epoch, candidate.side, candidate.level)
            if slot in self._proposed or (candidate.side, candidate.level) in occupied:
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
        self._proposed.update(proposed)
        for action in actions:
            config = action.executor_config
            self._issued[config.id] = (current.session_id, current.epoch,
                                       current.config_version, config)
        self.reason_code = "QUOTE_ACTIONS_PROPOSED" if actions else "NO_NEW_QUOTE_ACTIONS"
        return actions
