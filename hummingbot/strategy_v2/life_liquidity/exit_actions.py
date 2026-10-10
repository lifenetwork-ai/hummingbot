"""Explicit spot exposure reduction with separate durable capacity and wire checks.

Only post-only SELL exits are supported. No fill or liquidation is guaranteed;
blocked or outstanding exits expose a residual-risk reason to the operator.
Production does not install this opt-in adapter.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.economics import ExitInputs, SubsidyBudgetLedger, evaluate_exit
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@dataclass(frozen=True)
class ExitObservation:
    observed_at_ms: int
    independent_value_usdt: Decimal
    limit_price_usdt: Decimal
    best_bid_usdt: Decimal
    depth_base: Decimal
    fee_rate: Decimal
    source_kind: str
    rules: InstrumentRules


class SpotExitPlanner:
    def __init__(self, controller, path: Path, *, budget: SubsidyBudgetLedger,
                 observation: Callable[[], ExitObservation], utc_clock_ms: Callable[[], int],
                 max_age_ms: int, max_slippage_bps: Decimal, target_base: Decimal, create: bool):
        if (not isinstance(budget, SubsidyBudgetLedger) or not callable(observation)
                or not callable(utc_clock_ms) or type(max_age_ms) is not int or max_age_ms <= 0
                or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
                       for v in (max_slippage_bps, target_base))):
            raise ValueError("EXIT_POLICY_INVALID")
        self.controller, self.budget = controller, budget
        self.observation, self.utc_clock_ms = observation, utc_clock_ms
        self.max_age_ms, self.max_slippage_bps, self.target_base = max_age_ms, max_slippage_bps, target_base
        self.reservations = controller._order_safety_reservations
        self.wal = controller._order_safety_wal
        if self.reservations is None or self.reservations.path is None or self.wal is None:
            raise ValueError("EXIT_RECOVERY_REQUIRED")
        quote = controller._quote_action_planner
        if quote is None or budget is quote.subsidy_budget or (
                quote.subsidy_budget is not None and budget.path == quote.subsidy_budget.path):
            raise ValueError("EXIT_BUDGET_MUST_BE_SEPARATE")
        self.journal = PolicyState(path, policy={
            "controller_id": controller.config.id, "account_uid": controller.config.recovery_account_uid,
            "reservation_path": str(self.reservations.path.resolve()),
            "budget_path": str(budget.path.resolve()), "budget_policy": budget._policy(),
            "campaign_id": budget.campaign_id, "target_base": str(target_base),
            "max_slippage_bps": str(max_slippage_bps), "max_age_ms": max_age_ms,
            "strategy": controller.config.strategy.model_dump(mode="json")},
            initial={"checked_at_ms": 0, "intents": {}}, create=create)
        self._configs = {}  # Restored issued actions require reconciliation, never resend.
        self.residual_reason_code = "EXIT_NOT_REQUESTED"

    def handles(self, config) -> bool:
        return getattr(config, "id", None) in self._configs

    def _deny(self, reason):
        self.residual_reason_code = reason
        return None

    def _check(self, config, *, exclude: SpotIntent | None = None):
        try:
            c = self.controller
            policy = self.journal.policy
            if (c.config.id != policy["controller_id"]
                    or c.config.recovery_account_uid != policy["account_uid"]
                    or str(self.target_base) != policy["target_base"]
                    or str(self.max_slippage_bps) != policy["max_slippage_bps"]
                    or self.max_age_ms != policy["max_age_ms"]
                    or self.budget._policy() != policy["budget_policy"]
                    or not c.allow_risk_reduction_actions()
                    or c.config.strategy.model_dump(mode="json") != self.journal.policy["strategy"]):
                return self._deny("EXIT_SAFETY_BLOCKED")
            with self.journal.locked() as state:
                now = self.utc_clock_ms()
                if type(now) is not int or now <= 0 or now < state["checked_at_ms"]:
                    return self._deny("EXIT_CLOCK_INVALID")
                observed = self.observation()
                if (not isinstance(observed, ExitObservation)
                        or type(observed.observed_at_ms) is not int
                        or not 0 <= now - observed.observed_at_ms <= self.max_age_ms
                        or observed.source_kind != "independent_market"
                        or any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0
                               for v in (observed.independent_value_usdt, observed.limit_price_usdt,
                                         observed.best_bid_usdt))
                        or not isinstance(observed.fee_rate, Decimal)
                        or not observed.fee_rate.is_finite() or observed.fee_rate < 0):
                    return self._deny("EXIT_VALUE_UNAVAILABLE")
                state["checked_at_ms"] = now
                self.journal.commit(state)
                preview = self.reservations.preview(exclude_open_intent=exclude)
                if preview.pending_orders():
                    return self._deny("EXIT_CANCEL_RECONCILIATION_REQUIRED")
                rules = observed.rules
                if (not isinstance(rules, InstrumentRules)
                        or any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0
                               for v in (rules.tick_size, rules.lot_size, rules.min_size))
                        or config.side != TradeType.SELL or config.price != observed.limit_price_usdt
                        or config.amount < rules.min_size or config.amount % rules.lot_size != 0
                        or config.price % rules.tick_size != 0 or config.price <= observed.best_bid_usdt):
                    return self._deny("EXIT_ORDER_INVALID")
                if (not isinstance(observed.depth_base, Decimal) or not observed.depth_base.is_finite()
                        or observed.depth_base < config.amount):
                    return self._deny("EXIT_DEPTH_UNAVAILABLE")
                # LIFE fees can also reduce inventory; conservatively allow the
                # full fee rate in base units when testing the target boundary.
                quantity_with_fee = config.amount * (1 + observed.fee_rate)
                if quantity_with_fee > preview.life_balance - self.target_base:
                    return self._deny("EXIT_WOULD_REVERSE_POSITION")
                quote = c._quote_action_planner
                if quote.fee_binding is not None:
                    costs = QuoteCosts(observed.fee_rate, observed.fee_rate,
                                       Decimal(0), Decimal(0), Decimal(0), Decimal(0))
                    if (not quote._fee_binding_matches_config(quote.fee_binding, c.config)
                            or not quote.fee_binding.evaluate(costs).allowed):
                        return self._deny("EXIT_FEE_UNAVAILABLE")
                session = c._order_safety_manager.current_session
                at = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
                status = self.budget.verified_status(session_id=session.session_id, at_utc=at)
                if (status.session_committed_quote > self.budget.session_limit_quote
                        or status.day_committed_quote > self.budget.day_limit_quote
                        or status.campaign_committed_quote > self.budget.campaign_limit_quote):
                    return self._deny("EXIT_LOSS_BUDGET_EXCEEDED")
                available = status.available_quote
                held = self.budget.reserved_for(config.id, session_id=session.session_id)
                if held is not None:
                    available += held
                decision = evaluate_exit(ExitInputs(
                    preview.life_balance - self.target_base, "SELL", config.amount, config.price,
                    observed.independent_value_usdt, self.max_slippage_bps, available))
                if not decision.allowed:
                    return self._deny(decision.reason_code)
                cost = decision.expected_loss_quote + config.amount * config.price * observed.fee_rate
                if cost > available or held is not None and cost > held:
                    return self._deny("EXIT_LOSS_BUDGET_EXCEEDED")
                self.residual_reason_code = "EXIT_READY"
                return cost
        except Exception:
            return self._deny("EXIT_EVIDENCE_UNAVAILABLE")

    async def propose(self, intent_id: str, quantity_base: Decimal):
        """Request one bounded exit only after authoritative cancellation/account proof."""
        c = self.controller
        try:
            session = c._order_safety_manager.current_session
            proof = await c._order_safety_gateway.reconcile(session.session_id, session.epoch)
            c._quote_action_planner._release_reconciled_proposals()
            if (not proof.scope_complete or not proof.trade_events_reconciled
                    or proof.open_order_ids or proof.pending_cancel_ids or proof.unknown_order_ids
                    or c._quote_action_planner.action_journal.active_records()):
                self._deny("EXIT_CANCEL_RECONCILIATION_REQUIRED")
                return []
            observed = self.observation()
            config = OrderExecutorConfig(
                id=intent_id, controller_id=c.config.id, side=TradeType.SELL,
                connector_name=c.config.strategy.spot.connector, trading_pair=c.config.strategy.spot.pair,
                amount=quantity_base, price=observed.limit_price_usdt,
                execution_strategy=ExecutionStrategy.LIMIT_MAKER, level_id="0")
            if self._check(config) is None:
                return []
            with self.journal.locked() as state:
                if intent_id in state["intents"] or any(
                        entry["state"] in ("PROPOSED", "DISPATCHED") for entry in state["intents"].values()):
                    self._deny("EXIT_RECONCILIATION_REQUIRED")
                    return []
                state["intents"][intent_id] = {
                    "config": config.model_dump(mode="json"), "state": "PROPOSED",
                    "session_id": session.session_id, "epoch": session.epoch,
                    "config_version": session.config_version,
                    "risk_epoch": c._protected_spot_sender.risk_epoch(),
                    "scope_at_ms": self.utc_clock_ms()}
                self.journal.commit(state)
            c._quote_action_planner.action_journal.claim_batch([QuoteActionRecord(
                intent_id, c.config.id, session.session_id, session.epoch, session.config_version,
                c.config.strategy.spot.pair, "SELL", 0)])
            self._configs[intent_id] = config.model_copy(deep=True)
            return [CreateExecutorAction(controller_id=c.config.id, executor_config=config)]
        except Exception:
            self._deny("EXIT_EVIDENCE_UNAVAILABLE")
            return []

    def authorizes_config(self, config, *, exclude: SpotIntent | None = None) -> bool:
        try:
            issued = self._configs.get(config.id)
            if issued is None or issued.model_dump(mode="json") != config.model_dump(mode="json"):
                return False
            claim = self.controller._quote_action_planner.action_journal.verified_get(config.id)
            if claim.state not in ("PROPOSED", "DISPATCHED"):
                return False
            with self.journal.locked() as state:
                entry = state["intents"][config.id]
                current = self.controller._order_safety_manager.current_session
                now = self.utc_clock_ms()
                if (entry["state"] != claim.state
                        or (claim.controller_id, claim.session_id, claim.epoch, claim.config_version,
                            claim.market, claim.side, claim.level)
                        != (self.controller.config.id, current.session_id, current.epoch,
                            current.config_version, config.trading_pair, "SELL", 0)
                        or entry["state"] not in ("PROPOSED", "DISPATCHED")
                        or not 0 <= now - entry["scope_at_ms"] <= self.max_age_ms
                        or (entry["session_id"], entry["epoch"], entry["config_version"])
                        != (current.session_id, current.epoch, current.config_version)
                        or entry["risk_epoch"] != self.controller._protected_spot_sender.risk_epoch()):
                    return False
            return self._check(config, exclude=exclude) is not None
        except Exception:
            self._deny("EXIT_EVIDENCE_UNAVAILABLE")
            return False

    def authorizes_permit(self, permit, intent: SpotIntent) -> bool:
        config = self._configs.get(permit.intent_id)
        if config is None:
            return False
        return self.authorizes_config(config, exclude=intent)

    def reserve_budget(self, config, intent: SpotIntent) -> bool:
        cost = self._check(config, exclude=intent)
        if cost is None:
            return False
        session = self.controller._order_safety_manager.current_session
        return self.budget.reserve(config.id, cost, session_id=session.session_id,
                                   at_utc=datetime.fromtimestamp(self.utc_clock_ms() / 1000, tz=timezone.utc))

    def action_transition(self, action, state: str) -> bool:
        config = action.executor_config
        with self.journal.locked() as data:
            entry = data["intents"][config.id]
            if entry["state"] == state:
                return True
            if entry["state"] != "PROPOSED":
                return False
            self.controller._quote_action_planner.action_journal.transition(
                config.id, expected="PROPOSED", state=state)
            data["intents"][config.id] = {**entry, "state": state}
            self.journal.commit(data)
        return True

    def pending_exit_ready(self) -> bool:
        for config in self._configs.values():
            intent = None
            if self.reservations.has_open_intent(config.id):
                current = self.controller._order_safety_manager.current_session
                intent = SpotIntent(config.id, "SELL", config.amount, config.price,
                                    current.session_id, current.epoch)
            if self.authorizes_config(config, exclude=intent):
                return True
        return False

    async def reconcile_budget(self) -> bool:
        """Release only proved terminal exits; unfilled/unknown quantities stay held."""
        try:
            c = self.controller
            session = c._order_safety_manager.current_session
            proof = await c._order_safety_gateway.reconcile(session.session_id, session.epoch)
            attributor = c._fill_attributor
            costs = attributor.verified_loss_by_intent() if attributor is not None else None
            if costs is None:
                return False
            with self.journal.locked() as state:
                for intent_id, entry in state["intents"].items():
                    if entry["state"] == "REJECTED":
                        continue
                    record = self.wal.get(intent_id)
                    if ((record.session_id, record.epoch) != (entry["session_id"], entry["epoch"])
                            or not self.budget.matches_intent_session(intent_id, record.session_id)):
                        return False
                    actual = costs.get(intent_id, Decimal("0"))
                    self.budget.record_fill_floor(intent_id, actual)
                    if (not proof.scope_complete or not proof.trade_events_reconciled
                            or record.state != "TERMINAL" or not record.exchange_terminal_observed
                            or not self.reservations.is_terminal_intent(intent_id)):
                        self._deny("EXIT_RESIDUAL_UNRESOLVED")
                        return False
                    # The durable reservation, WAL and independently attributed
                    # complete fill set prove this exact terminal cost floor.
                    self.budget._reconcile(intent_id, actual_cost_quote=actual, release_proven=True)
                    claims = self.controller._quote_action_planner.action_journal
                    claim = claims.verified_get(intent_id)
                    if claim.state != "RECONCILED":
                        claims.transition(intent_id, expected=entry["state"], state="RECONCILED")
                    state["intents"][intent_id] = {**entry, "state": "TERMINAL"}
                self.journal.commit(state)
            self._deny("EXIT_RESIDUAL_RECONCILED")
            return True
        except Exception:
            self._deny("EXIT_EVIDENCE_UNAVAILABLE")
            return False
