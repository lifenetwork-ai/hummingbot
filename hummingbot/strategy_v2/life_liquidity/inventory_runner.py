"""Opt-in bounded spot inventory execution; normalized offline source contracts.

The inventory campaign has its own immutable target/benchmark/deadline. Children
retain the protected account session/epoch and cannot extend that deadline.
Source labels do not authenticate a feed. Real feed/runner assembly stays D/R.
"""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.inventory_execution import (
    InventoryExecutionLedger,
    InventoryExecutionSnapshot,
)
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

ZERO = Decimal("0")


def number(v, *, positive=False):
    return isinstance(v, Decimal) and v.is_finite() and (v > 0 if positive else v >= 0)


@dataclass(frozen=True)
class InventoryMarketObservation:
    observed_at_ms: int
    best_bid_usdt: Decimal
    best_ask_usdt: Decimal
    public_volume_base: Decimal
    known_own_volume_base: Decimal
    independent_depth_base: Decimal
    expected_impact_quote: Decimal
    fee_rate: Decimal
    source_kind: str
    volume_anchor: str


class InventoryExecutor:
    def __init__(self, controller, path, *, ledger, budget, observation, clock_ms, max_age_ms, create):
        self.controller, self.ledger, self.budget = controller, ledger, budget
        self.observation, self.clock_ms, self.max_age_ms = observation, clock_ms, max_age_ms
        self.wal, self.reservations = controller._order_safety_wal, controller._order_safety_reservations
        self.quote = controller._quote_action_planner
        if (not isinstance(ledger, InventoryExecutionLedger) or not isinstance(budget, SubsidyBudgetLedger)
                or self.quote is None or self.wal is None or self.reservations is None
                or not callable(observation) or not callable(clock_ms) or type(max_age_ms) is not int or max_age_ms <= 0
                or type(create) is not bool or controller._fill_attributor is None
                or create and ledger._entries
                or controller._fill_attributor.exit_budget is not budget
                or budget is self.quote.subsidy_budget
                or self.quote.subsidy_budget is not None and budget.path == self.quote.subsidy_budget.path):
            raise ValueError("INVENTORY_RUNNER_BINDING_INVALID")
        self.target_base = max(ZERO, self.reservations.life_balance - ledger.policy.target_base)
        self.journal = PolicyState(path, policy={
            "controller": controller.config.id, "uid": controller.config.recovery_account_uid,
            "strategy": controller.config.strategy.model_dump(mode="json"),
            "ledger": str(ledger.path.resolve()), "inventory_session": ledger.session_id, "policy": ledger._policy(),
            "budget": str(budget.path.resolve()), "budget_policy": budget._policy(),
            "wal": str(self.wal.path.resolve()), "reservations": str(self.reservations.path.resolve()),
            "max_age_ms": max_age_ms}, initial={"mode": "MM", "last_ms": None, "proof_at_ms": None,
                                                "parent": None, "children": {}, "phase": "EMPTY",
                                                "terminal_ids": [], "proof_id": None, "operators": [],
                                                "balance_floor": str(self.target_base), "volume_from_ms": clock_ms()}, create=create)
        self.target_base = Decimal(self.journal._read()["balance_floor"])
        self._configs, self._armed, self._settling = {}, create, False
        self._mutex = asyncio.Lock()
        self.active_task = None
        self.reason_code = "INVENTORY_NOT_REQUESTED" if create else "INVENTORY_OPERATOR_REARM_REQUIRED"
        ledger.bind_executor(self)

    @asynccontextmanager
    async def _operation(self):
        async with self._mutex:
            self.active_task = asyncio.current_task()
            try:
                yield
            finally:
                self.active_task = None

    def _state(self):
        if self.controller._order_safety_stopped:
            raise ValueError("INVENTORY_STOPPED")
        with self.journal.locked() as state:
            p, c = self.journal.policy, self.controller
            if (p["strategy"] != c.config.strategy.model_dump(mode="json")
                    or p["controller"] != c.config.id or p["uid"] != c.config.recovery_account_uid
                    or p["policy"] != self.ledger._policy() or p["budget_policy"] != self.budget._policy()
                    or p["max_age_ms"] != self.max_age_ms
                    or state["mode"] not in ("MM", "DRAINING", "INVENTORY", "RETURNING")
                    or self.ledger._executor is not self or self.controller._inventory_executor is not self):
                raise ValueError("INVENTORY_POLICY_CHANGED")
            if (set(state) != {"mode", "last_ms", "proof_at_ms", "parent", "children", "phase",
                               "terminal_ids", "proof_id", "operators", "balance_floor", "volume_from_ms"}
                    or state["phase"] not in ("EMPTY", "APPLYING", "COMMITTED")
                    or not isinstance(state["children"], dict) or not isinstance(state["operators"], list)
                    or not isinstance(state["terminal_ids"], list)
                    or any(state[k] is not None and (type(state[k]) is not int or state[k] < 0)
                           for k in ("last_ms", "proof_at_ms", "volume_from_ms"))
                    or not number(Decimal(state["balance_floor"]))
                    or Decimal(state["balance_floor"]) != self.target_base
                    or set(self.ledger._entries) - set(state["children"])
                    or any(not isinstance(k, str) or not k or set(e) != {"state", "config"}
                           or e["state"] not in ("PREPARING", "PROPOSED", "DISPATCHED", "REJECTED", "TERMINAL")
                           for k, e in state["children"].items())
                    or state["mode"] == "MM" and any(e["state"] != "TERMINAL" for e in state["children"].values())):
                raise ValueError("INVENTORY_STATE_INVALID")
            ownership = self.controller._order_safety_account_lock
            if self.controller.config.recovery_state_dir is not None and (
                    ownership is None or ownership._fd is None
                    or ownership.path.name != f"okx-{self.controller.config.recovery_account_uid}.lock"):
                raise ValueError("INVENTORY_ACCOUNT_OWNERSHIP_REQUIRED")
            now = self.clock_ms()
            if type(now) is not int or now < 0 or state["last_ms"] is not None and now < state["last_ms"]:
                self._armed = False
                raise ValueError("INVENTORY_CLOCK_ROLLBACK")
            state["last_ms"] = now
            self.journal.commit(state)
            self.ledger._verified()
            self.reservations.assert_healthy()
            return state

    def owns_intent(self, intent_id):
        try:
            return intent_id in self._state()["children"]
        except Exception:
            return False

    def handles(self, config):
        return getattr(config, "id", None) in self._configs

    def allows_mm(self):
        try:
            return self._armed and self.active_task is None and self._state()["mode"] == "MM"
        except Exception:
            return False

    def _market(self):
        state = self._state()
        now, obs, p = self.clock_ms(), self.observation(), self.ledger.policy
        c = self.controller
        current = c._order_safety_manager.current_session
        if (not self._armed or self.active_task is not None or state["mode"] != "INVENTORY" or state["phase"] != "COMMITTED"
                or state["proof_at_ms"] is None or not 0 <= now - state["proof_at_ms"] <= self.max_age_ms
                or abs(int(c._order_safety_manager.wall_clock().timestamp() * 1000) - now) > self.max_age_ms
                or current is None or state["parent"] != [current.session_id, current.epoch, current.config_version]
                or datetime.fromtimestamp(now / 1000, timezone.utc) >= p.deadline_utc
                or not c.allow_inventory_execution_actions()
                or not isinstance(obs, InventoryMarketObservation) or obs.source_kind != "independent_market"
                or obs.volume_anchor != self.ledger.session_id
                or type(obs.observed_at_ms) is not int or not 0 <= now - obs.observed_at_ms <= self.max_age_ms
                or not all(number(v) for v in (obs.public_volume_base, obs.known_own_volume_base,
                                               obs.independent_depth_base, obs.expected_impact_quote, obs.fee_rate))
                or not all(number(v, positive=True) for v in (obs.best_bid_usdt, obs.best_ask_usdt))
                or obs.known_own_volume_base > obs.public_volume_base):
            raise ValueError("INVENTORY_PERMISSION_UNAVAILABLE")
        # Instrument precision comes from the qualified metadata gate.
        rules = c.listing_gate.instrument_rules
        if rules is None or (rules.tick_size, rules.lot_size, rules.min_size) != (p.tick_size, p.lot_size, p.min_size_base):
            raise ValueError("INVENTORY_INSTRUMENT_RULES_CHANGED")
        session = current.session_id
        status = self.budget.verified_status(session_id=session, at_utc=datetime.fromtimestamp(now / 1000, timezone.utc))
        if (status.campaign_committed_quote > self.budget.campaign_limit_quote
                or status.day_committed_quote > self.budget.day_limit_quote
                or status.session_committed_quote > self.budget.session_limit_quote):
            raise ValueError("INVENTORY_EXIT_BUDGET_BREACHED")
        known_own = max(obs.known_own_volume_base, sum((Decimal(event["quantity_base"])
                        for event in c._fill_attributor._events.values()
                        if event["fill_at_ms"] >= state["volume_from_ms"]), ZERO))
        snap = InventoryExecutionSnapshot(datetime.fromtimestamp(now / 1000, timezone.utc),
                                          obs.best_bid_usdt, obs.best_ask_usdt, obs.public_volume_base,
                                          known_own, obs.independent_depth_base, obs.expected_impact_quote,
                                          (max(ZERO, self.reservations.life_balance - self.target_base) / (1 + obs.fee_rate)
                                           if p.side == "SELL" else self.reservations.life_balance), self.reservations.usdt_balance,
                                          ZERO, ZERO, ZERO, ZERO, ZERO, True, True)
        return snap, obs, status

    async def _proof(self, *, cancel):
        c, manager, gateway = self.controller, self.controller._order_safety_manager, self.controller._order_safety_gateway
        c._runner_halt_ok = c._halt_runner_orders()
        if not c._runner_halt_ok:
            raise ValueError("INVENTORY_RUNNER_SCOPE_UNAVAILABLE")
        gateway.runner_scope_check = c._runner_executor_scope_complete
        # Exact queued MM actions can be rejected before executor creation.
        records = {r.intent_id for r in self.wal.all_records()}
        for key, issued in tuple(self.quote._issued.items()):
            if key not in records:
                self.quote.on_runner_action_rejected(CreateExecutorAction(controller_id=c.config.id, executor_config=issued[3]))
        joint = c._joint_recovery
        if (c._shared_capital_authority is not None or c._protected_swap_sender is not None) and joint is None:
            raise ValueError("INVENTORY_JOINT_RECOVERY_REQUIRED")
        if joint is not None:
            if cancel:
                await joint.cancel_working_swaps()
            if not await joint.reconcile():
                raise ValueError("INVENTORY_JOINT_PROOF_REQUIRED")
        current = manager.current_session
        if current is None:
            raise ValueError("INVENTORY_PARENT_SESSION_REQUIRED")
        scopes = {(r.session_id, r.epoch) for r in self.wal.all_records()}
        scopes.add((current.session_id, current.epoch))
        if cancel:
            if gateway.cancel_retry_policy is not None:
                await gateway.request_cancel_scopes(tuple(sorted(scopes)))
            else:
                for scope in sorted(scopes):
                    await gateway.request_cancel(*scope)
        clear = True
        for scope in sorted(scopes):
            result = await gateway.reconcile(*scope)
            age = (manager.wall_clock() - result.observed_at).total_seconds() * 1000
            if (not result.scope_complete or not result.trade_events_reconciled
                    or not 0 <= age <= self.max_age_ms or (result.session_id, result.epoch) != scope):
                raise ValueError("INVENTORY_ACCOUNT_PROOF_UNAVAILABLE")
            clear = clear and not (result.open_order_ids or result.pending_cancel_ids or result.unknown_order_ids)
        if not c.verify_runner_fill_events() or c._runner_scope_invalid or not c._fill_attributor.ready():
            raise ValueError("INVENTORY_FILLS_UNAVAILABLE")
        self.quote._release_reconciled_proposals()
        if joint is not None and not joint.successor_ready(current.session_id, current.epoch):
            clear = False
        return clear

    async def enter_inventory(self, *, operator_id):
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ValueError("INVENTORY_OPERATOR_REQUIRED")
        async with self._operation():
            try:
                self._state()
                with self.journal.locked() as state:
                    if state["mode"] not in ("MM", "DRAINING", "INVENTORY"):
                        raise ValueError("INVENTORY_MODE_INVALID")
                    state["mode"] = "DRAINING"
                    self.journal.commit(state)  # Revoke queued/final MM before any await.
                if not await asyncio.wait_for(self._proof(cancel=True), self.max_age_ms / 1000):
                    raise ValueError("INVENTORY_MM_RECONCILIATION_REQUIRED")
                await self._settle_children()
                if self.quote.action_journal.active_records() or self.ledger.pending_base:
                    raise ValueError("INVENTORY_ACTIONS_UNRESOLVED")
                current = self.controller._order_safety_manager.current_session
                with self.journal.locked() as state:
                    parent = [current.session_id, current.epoch, current.config_version]
                    if state["parent"] not in (None, parent):
                        raise ValueError("INVENTORY_PARENT_CHANGED")
                    state.update(mode="INVENTORY", parent=parent, proof_at_ms=self.clock_ms(), phase="COMMITTED")
                    state["operators"].append({"operator": operator_id, "at_ms": self.clock_ms(), "mode": "INVENTORY"})
                    self.journal.commit(state)
                self._armed = True
                self.reason_code = "INVENTORY_ACTIVE"
                return True
            except asyncio.CancelledError:
                self._armed = False
                raise
            except Exception as exc:
                self._armed = False
                self.reason_code = str(exc)
                return False

    def propose(self, intent_id):
        try:
            snap, obs, _ = self._market()
            if not isinstance(intent_id, str) or not intent_id or self.quote.action_journal.active_records():
                raise ValueError("INVENTORY_ACTIONS_UNRESOLVED")
            with self.journal.locked() as state:
                if intent_id in state["children"]:
                    raise ValueError("INVENTORY_INTENT_REUSED")
                state["children"][intent_id] = {"state": "PREPARING", "config": None}
                self.journal.commit(state)
            decision = self.ledger.reserve_child(intent_id, snap, fee_rate=obs.fee_rate)
            if not decision.allowed:
                raise ValueError(decision.reason_code)
            current = self.controller._order_safety_manager.current_session
            config = OrderExecutorConfig(id=intent_id, controller_id=self.controller.config.id,
                                         connector_name=self.controller.config.strategy.spot.connector,
                                         trading_pair=self.controller.config.strategy.spot.pair,
                                         side=TradeType[self.ledger.policy.side], amount=decision.quantity_base,
                                         price=decision.price_usdt, level_id="0", execution_strategy=ExecutionStrategy.LIMIT_MAKER)
            self.quote.action_journal.claim_batch([QuoteActionRecord(intent_id, self.controller.config.id,
                                                                     current.session_id, current.epoch, current.config_version,
                                                                     config.trading_pair, config.side.name, 0)])
            with self.journal.locked() as state:
                state["children"][intent_id] = {"state": "PROPOSED", "config": config.model_dump(mode="json")}
                self.journal.commit(state)
            self._configs[intent_id] = config.model_copy(deep=True)
            self.reason_code = "INVENTORY_CHILD_READY"
            return [CreateExecutorAction(controller_id=self.controller.config.id, executor_config=config)]
        except Exception as exc:
            self.reason_code = str(exc)
            return []

    def _cost(self, config, *, exclude=None):
        snap, obs, status = self._market()
        state = self._state()
        entry = state["children"][config.id]
        claim = self.quote.action_journal.verified_get(config.id)
        if (self._configs.get(config.id) != config or entry["config"] != config.model_dump(mode="json")
                or entry["state"] not in ("PROPOSED", "DISPATCHED") or claim.state != entry["state"]
                or (claim.controller_id, claim.session_id, claim.epoch, claim.config_version, claim.market, claim.side, claim.level)
                != (self.controller.config.id, *state["parent"], config.trading_pair, config.side.name, 0)
                or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                or self.reservations.preview(exclude_open_intent=exclude).pending_orders()):
            raise ValueError("INVENTORY_CHILD_CHANGED_OR_COMPETING")
        decision = self.ledger.preview_child(snap, fee_rate=obs.fee_rate, exclude_intent=config.id)
        if not decision.allowed or config.amount > decision.quantity_base or config.price != decision.price_usdt:
            raise ValueError(decision.reason_code if not decision.allowed else "INVENTORY_FINAL_LIMIT_CHANGED")
        cost = max(ZERO, (config.price - self.ledger.policy.benchmark_price_usdt)
                   * config.amount * (1 if config.side == TradeType.BUY else -1))
        cost += config.amount * config.price * obs.fee_rate + obs.expected_impact_quote
        held = self.budget.reserved_for(config.id, session_id=state["parent"][0])
        if cost > status.available_quote + (held or ZERO) or held is not None and cost > held:
            raise ValueError("INVENTORY_EXIT_BUDGET_EXHAUSTED")
        return cost

    def authorizes_config(self, config, *, exclude=None):
        try:
            self._cost(config, exclude=exclude)
            return True
        except Exception as exc:
            self.reason_code = str(exc)
            return False

    def authorizes_permit(self, permit, intent):
        config = self._configs.get(permit.intent_id)
        return config is not None and self.authorizes_config(config, exclude=intent)

    def reserve_budget(self, config, intent):
        try:
            cost = self._cost(config, exclude=intent)
            session = self.controller._order_safety_manager.current_session
            return self.budget.reserve(config.id, cost, session_id=session.session_id,
                                       at_utc=datetime.fromtimestamp(self.clock_ms() / 1000, timezone.utc))
        except Exception:
            return False

    def action_transition(self, action, target):
        try:
            return self._action_transition(action, target)
        except Exception:
            self._armed = False
            self.reason_code = "INVENTORY_ACTION_JOURNAL_UNAVAILABLE"
            return False

    def _action_transition(self, action, target):
        config = action.executor_config
        with self.journal.locked() as state:
            entry = state["children"][config.id]
            if entry["config"] != config.model_dump(mode="json"):
                return False
            if entry["state"] == target:
                return True
            if entry["state"] != "PROPOSED":
                return False
            self.quote.action_journal.transition(config.id, expected="PROPOSED", state=target)
            entry["state"] = target
            self.journal.commit(state)
        return True

    def pending_exit_ready(self):
        from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
        current = self.controller._order_safety_manager.current_session
        for config in self._configs.values():
            intent = SpotIntent(config.id, config.side.name, config.amount, config.price, current.session_id, current.epoch)
            if self.authorizes_config(config, exclude=intent if self.reservations.has_open_intent(config.id) else None):
                return True
        return False

    def authorizes_terminal(self, intent_id, proof_id):
        try:
            state = self.journal._read()
            return (self._settling and state == self.journal._state and state["phase"] == "APPLYING"
                    and state["proof_id"] == proof_id and intent_id in state["terminal_ids"])
        except Exception:
            return False

    async def _settle_children(self):
        attributor = self.controller._fill_attributor
        if not attributor.ready():
            raise ValueError("INVENTORY_ATTRIBUTION_UNAVAILABLE")
        costs = attributor.verified_loss_by_intent()
        state = self._state()
        terminal = []
        records = {r.intent_id: r for r in self.wal.all_records()}
        claims = {r.intent_id: r for r in self.quote.action_journal.verified_records()}
        for key, entry in state["children"].items():
            r = records.get(key)
            if r is None:
                # All runner children have been halted and account scope is complete.
                # Protected spot has no await between WAL creation and arming a send.
                executors = self.controller._runner_executors()
                if executors is None or any(e.config.id == key and e.recovery_order_ids() for e in executors):
                    continue
                if key in claims and claims[key].state == "PROPOSED":
                    self.quote.action_journal.transition(key, expected="PROPOSED", state="REJECTED")
                terminal.append(key)
            else:
                for trade_id, event in sorted(attributor._events.items()):
                    if event["intent_id"] == key:
                        self.ledger.record_fill(key, trade_id, Decimal(event["quantity_base"]),
                                                Decimal(event["price_usdt"]), Decimal(event["fee_cost_quote"]))
                if (r.state == "TERMINAL" and r.exchange_terminal_observed and self.reservations.is_terminal_intent(key)
                        or r.state == "ABORTED_BEFORE_SEND" and (key not in self.reservations.reservation_ids
                                                                 or self.reservations.is_terminal_intent(key))):
                    terminal.append(key)
        proof_id = hashlib.sha256(json.dumps({"terminals": terminal, "children": state["children"],
                                              "records": [asdict(r) for r in self.wal.all_records()]}, sort_keys=True).encode()).hexdigest()
        self._settling = True
        try:
            with self.journal.locked() as current:
                current.update(phase="APPLYING", terminal_ids=terminal, proof_id=proof_id)
                self.journal.commit(current)
                for key in terminal:
                    if key in self.ledger._entries:
                        self.ledger.confirm_terminal(key, authority=self, proof_id=proof_id)
                    if key in records and self.budget.matches_intent_session(key, records[key].session_id):
                        actual = max(self.ledger.gross_cost(key), costs.get(key, ZERO))
                        self.budget._reconcile(key, actual_cost_quote=actual, release_proven=True)
                    claim = self.quote.action_journal.verified_get(key) if key in claims else None
                    if claim is not None and claim.state in ("PROPOSED", "DISPATCHED"):
                        self.quote.action_journal.transition(key, expected=claim.state, state="RECONCILED")
                    current["children"][key]["state"] = "TERMINAL"
                current["phase"] = "COMMITTED"
                self.journal.commit(current)
        finally:
            self._settling = False

    async def reconcile(self, *, cancel=False):
        async with self._operation():
            return await self._reconcile(cancel=cancel)

    async def _reconcile(self, *, cancel=False):
        try:
            self._state()
            clear = await asyncio.wait_for(self._proof(cancel=cancel), self.max_age_ms / 1000)
            await self._settle_children()
            with self.journal.locked() as state:
                state["proof_at_ms"] = self.clock_ms()
                self.journal.commit(state)
            self.reason_code = "INVENTORY_RECONCILED" if clear else "INVENTORY_PENDING"
            return clear and self.ledger.pending_base == 0 and not self.quote.action_journal.active_records()
        except asyncio.CancelledError:
            self._armed = False
            raise
        except Exception as exc:
            self._armed = False
            self.reason_code = str(exc)
            return False

    async def return_to_mm(self, *, operator_id):
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ValueError("INVENTORY_OPERATOR_REQUIRED")
        async with self._operation():
            self._state()
            with self.journal.locked() as state:
                state["mode"] = "RETURNING"
                self.journal.commit(state)
            if not await self._reconcile(cancel=True):
                return False
            with self.journal.locked() as state:
                state["mode"] = "MM"
                state["operators"].append({"operator": operator_id, "at_ms": self.clock_ms(), "mode": "MM"})
                self.journal.commit(state)
            self._armed = True
            self._configs.clear()
            return True  # Existing MM gates/deadline still decide permission.

    def on_safety_tick(self):
        try:
            if datetime.fromtimestamp(self.clock_ms() / 1000, timezone.utc) >= self.ledger.policy.deadline_utc:
                with self.journal.locked() as state:
                    if state["mode"] == "INVENTORY":
                        state["mode"] = "RETURNING"
                        self.journal.commit(state)
        except Exception:
            self._armed = False
