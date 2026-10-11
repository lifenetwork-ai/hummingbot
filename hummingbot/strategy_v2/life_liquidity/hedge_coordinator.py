"""Durable, opt-in LIFE hedge coordination; feed/recovery qualification is external.

Actual balances size orders. Pending hedges never count as completed positions.
All children use the protected maker sender and the shared capital journal.
"""

import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.hedge import HedgePolicy
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

ZERO = Decimal("0")
BPS = Decimal("10000")


def number(value, positive=False):
    return isinstance(value, Decimal) and value.is_finite() and (value > 0 if positive else value >= 0)


@dataclass(frozen=True)
class HedgeExecutionPolicy:
    hedge: HedgePolicy
    initial_target_base: Decimal
    cumulative_cost_budget_quote: Decimal
    max_market_age_ms: int
    urgent_policy: str

    def __post_init__(self):
        if (not isinstance(self.hedge, HedgePolicy) or self.hedge.life_swap_instrument != "LIFE-USDT-SWAP"
                or not number(self.initial_target_base) or not number(self.cumulative_cost_budget_quote)
                or type(self.max_market_age_ms) is not int or self.max_market_age_ms <= 0
                or self.urgent_policy != "bounded_maker_and_pause" or self.hedge.maker_fee_rate > 1):
            raise ValueError("HEDGE_EXECUTION_POLICY_INVALID")


@dataclass(frozen=True)
class HedgeMarket:
    account_uid: str
    instrument: str
    sequence: int
    observed_at_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    fee_rate: Decimal
    funding_cost_quote: Decimal
    available_edge_quote: Decimal
    scope_complete: bool
    source: str


@dataclass(frozen=True)
class HedgeTrade:
    trade_id: str
    quantity_base: Decimal
    price_usdt: Decimal
    fee_quote: Decimal


@dataclass(frozen=True)
class HedgeSettlement:
    account_uid: str
    intent_id: str
    wire_id: str
    exchange_order_id: str
    snapshot_sequence: int
    cumulative_base: Decimal
    trades: tuple[HedgeTrade, ...]
    terminal: bool
    scope_complete: bool
    source: str


class HedgeCoordinator:
    def __init__(self, controller, path: Path, *, policy: HedgeExecutionPolicy,
                 market, settlement, create: bool):
        self.controller, self.policy = controller, policy
        self.sender = controller._protected_swap_sender
        self.capital = controller._shared_capital_authority
        self.market, self.settlement = market, settlement
        if (not isinstance(policy, HedgeExecutionPolicy) or self.sender is None or self.capital is None
                or not callable(market) or not callable(settlement)):
            raise ValueError("HEDGE_COORDINATOR_BINDING_INVALID")
        self._binding = {"policy": _json(asdict(policy)), "sender_policy": self.sender.journal.policy,
                         "capital_policy": self.capital.journal.policy,
                         "capital_path": str(self.capital.journal.path.resolve()),
                         "swap_path": str(self.sender.journal.path.resolve())}
        self.journal = PolicyState(path, policy=self._binding, initial={
            "target": str(policy.initial_target_base), "target_version": 0, "updates": {},
            "orders": {}, "trades": {}, "attempts": 0, "cost_hold": "0",
            "since_ms": None, "last_ms": None, "market_sequence": None, "market": None,
            "fault": None,
            "episode_start": 0,
        }, create=create)
        self._live = set()  # Restore retains claims, never send authority.
        self._runtime_enabled = create
        self._faulted = False
        self.reason_code = "HEDGE_COORDINATOR_REVALIDATION_REQUIRED"
        with self.journal.locked() as state:
            self._validate_state(state)

    def _validate_state(self, state):
        if (set(state) != {"target", "target_version", "updates", "orders", "trades", "attempts", "cost_hold",
                           "since_ms", "last_ms", "market_sequence", "market", "fault", "episode_start"}
                or not isinstance(state["target"], str) or not number(Decimal(state["target"]))
                or not isinstance(state["cost_hold"], str) or not number(Decimal(state["cost_hold"]))
                or any(type(state[k]) is not int or state[k] < 0 for k in ("target_version", "attempts", "episode_start"))
                or any(state[k] is not None and (type(state[k]) is not int or state[k] < 0)
                       for k in ("since_ms", "last_ms", "market_sequence"))
                or any(not isinstance(state[k], dict) for k in ("updates", "orders", "trades"))
                or len(state["updates"]) != state["target_version"]
                or (state["market"] is None) != (state["market_sequence"] is None)
                or state["fault"] is not None and not isinstance(state["fault"], str)):
            raise ValueError("HEDGE_STATE_INVALID")
        for key, order in state["orders"].items():
            cfg = OrderExecutorConfig.model_validate(order["config"])
            self.sender._validate(cfg)
            if (cfg.id != key or order["status"] not in ("PREPARING", "ISSUED", "TERMINAL")
                    or not number(Decimal(order["filled"])) or Decimal(order["filled"]) > cfg.amount
                    or not number(Decimal(order["cost"])) or not number(Decimal(order["mark"]), True)
                    or not isinstance(order["trades"], dict)):
                raise ValueError("HEDGE_STATE_INVALID")
        versions = sorted(state["updates"].values(), key=lambda u: u["expected_version"])
        if (any(update["expected_version"] != i or not number(Decimal(update["target"]))
                for i, update in enumerate(versions))
                or state["target"] != (versions[-1]["target"] if versions else str(self.policy.initial_target_base))
                or state["attempts"] != len(state["orders"]) - state["episode_start"]
                or state["since_ms"] is not None and (state["last_ms"] is None or state["since_ms"] > state["last_ms"])):
            raise ValueError("HEDGE_STATE_INVALID")
        if (self._binding != self.journal.policy
                or _json(asdict(self.policy)) != self._binding["policy"]
                or Decimal(state["cost_hold"]) != sum((Decimal(o["cost"]) for o in state["orders"].values()), ZERO)
                or self.sender is not self.controller._protected_swap_sender
                or self.capital is not self.controller._shared_capital_authority
                or self.sender._policy() != self._binding["sender_policy"]
                or self.capital.journal.policy != self._binding["capital_policy"]):
            raise ValueError("HEDGE_BINDING_CHANGED")

    def _clock(self, state):
        self._validate_state(state)
        if state["fault"] is not None or self._faulted:
            raise ValueError("HEDGE_RECONCILIATION_FAULT_LATCHED")
        now = self.sender.clock_ms()
        if (type(now) is not int or now < 0 or state["last_ms"] is not None and now < state["last_ms"]):
            raise ValueError("HEDGE_CLOCK_ROLLBACK")
        state["last_ms"] = now
        return now

    def update_target(self, update_id, *, expected_version, target_base):
        if (not isinstance(update_id, str) or not update_id or type(expected_version) is not int
                or expected_version < 0 or not number(target_base)):
            raise ValueError("HEDGE_TARGET_INVALID")
        update = {"expected_version": expected_version, "target": str(target_base)}
        with self.journal.locked() as state:
            self._clock(state)
            old = state["updates"].get(update_id)
            if old is not None:
                if old != update:
                    raise ValueError("HEDGE_TARGET_ID_CONFLICT")
                return False
            if not self._runtime_enabled:
                raise ValueError("HEDGE_RECOVERY_REQUIRED")
            if (state["target_version"] != expected_version
                    or any(o["status"] != "TERMINAL" for o in state["orders"].values())):
                raise ValueError("HEDGE_TARGET_VERSION_OR_PENDING")
            state["updates"][update_id] = update
            state["target"], state["target_version"] = str(target_base), expected_version + 1
            self.journal.commit(state)
        return True

    def _market(self, state, now):
        m = self.market()
        p = self.policy
        if (not isinstance(m, HedgeMarket) or m.account_uid != self.capital.policy.account_uid
                or m.instrument != "LIFE-USDT-SWAP" or m.scope_complete is not True or m.source != "okx_depth_fee"
                or type(m.observed_at_ms) is not int or not 0 <= now - m.observed_at_ms <= p.max_market_age_ms
                or type(m.sequence) is not int or m.sequence < 1
                or not number(m.fee_rate) or m.fee_rate > 1
                or not number(m.funding_cost_quote) or not number(m.available_edge_quote)):
            raise ValueError("HEDGE_MARKET_UNQUALIFIED")
        for levels, reverse in ((m.bids, True), (m.asks, False)):
            if (not isinstance(levels, tuple) or not levels
                    or any(not isinstance(row, tuple) or len(row) != 2
                           or not all(number(v, True) for v in row) for row in levels)
                    or tuple(sorted(levels, reverse=reverse)) != levels
                    or len({price for price, _ in levels}) != len(levels)):
                raise ValueError("HEDGE_DEPTH_INVALID")
        if m.bids[0][0] >= m.asks[0][0]:
            raise ValueError("HEDGE_DEPTH_CROSSED")
        blob = _json(asdict(m))
        if (state["market_sequence"] is not None and (
                m.sequence < state["market_sequence"]
                or m.sequence == state["market_sequence"] and blob != state["market"])):
            raise ValueError("HEDGE_MARKET_REPLAY_OR_MUTATION")
        if state["market"] is not None and m.observed_at_ms < state["market"]["observed_at_ms"]:
            raise ValueError("HEDGE_MARKET_CLOCK_ROLLBACK")
        state["market_sequence"], state["market"] = m.sequence, blob
        self.journal.commit(state)  # A financial refusal must not permit older, cheaper data.
        return m

    def _residual(self, state, obs, now):
        residual = obs.spot_life_base + obs.long_base - obs.short_base - Decimal(state["target"])
        if residual == 0:
            state["since_ms"] = None
            if all(o["status"] == "TERMINAL" for o in state["orders"].values()):
                state["attempts"] = 0
                state["episode_start"] = len(state["orders"])
        elif state["since_ms"] is None:
            state["since_ms"] = now
        urgent = residual != 0 and (abs(residual) > self.policy.hedge.max_unhedged_base
                                    or now - state["since_ms"] >= self.policy.hedge.max_unhedged_ms)
        self.journal.commit(state)  # Missing market data must not reset exposure age.
        return residual, urgent

    def _cost(self, m, obs, side, base):
        remaining, total = base, ZERO
        for price, quantity in (m.bids if side == TradeType.SELL else m.asks):
            take = min(remaining, quantity)
            total += take * price
            remaining -= take
            if remaining == 0:
                break
        if remaining > 0:
            raise ValueError("HEDGE_DEPTH_INSUFFICIENT")
        executable = total / base
        mark = obs.mark_price_usdt
        adverse = max(ZERO, mark - executable if side == TradeType.SELL else executable - mark)
        basis = abs(mark - obs.index_price_usdt)
        if basis / obs.index_price_usdt * BPS > self.policy.hedge.max_basis_bps:
            raise ValueError("HEDGE_BASIS_LIMIT")
        limit = m.asks[0][0] if side == TradeType.SELL else m.bids[0][0]
        fee = max(m.fee_rate, self.policy.hedge.maker_fee_rate)
        funding_cost = m.funding_cost_quote
        carry = self.controller._carry_monitor
        if carry is not None:
            decision = carry.check()
            if not decision.allowed:
                raise ValueError("HEDGE_CARRY_UNAVAILABLE")
            fee = max(fee, decision.fee_rate)
            # Reserve the child's own limit-price bound before publishing its claim.
            # Otherwise that claim can raise the monitor's price floor and revoke itself.
            funding_cost = max(funding_cost, base * decision.funding_cost_per_base * max(
                Decimal(1), limit / decision.funding_reference_price_usdt))
        cost = (adverse + basis) * base + max(limit, executable, mark) * base * fee + funding_cost
        if cost > self.policy.hedge.max_hedge_cost_quote or cost > m.available_edge_quote:
            raise ValueError("HEDGE_COST_OR_NET_EDGE_LIMIT")
        return limit, cost

    def _snapshot(self):
        obs = self.capital.qualified_snapshot()
        ledger = self.controller._order_safety_reservations
        if ledger is None:
            raise ValueError("HEDGE_SPOT_LEDGER_UNAVAILABLE")
        ledger.assert_healthy()
        if ledger.path is None or not ledger.path.is_file() or ledger.path.is_symlink():
            raise ValueError("HEDGE_SPOT_LEDGER_UNAVAILABLE")
        restored = ReservationLedger.restore(ledger.path, limits=ledger.limits)
        if (restored.life_balance != ledger.life_balance or restored.usdt_balance != ledger.usdt_balance
                or restored._reservations != ledger._reservations or restored._trades != ledger._trades
                or restored._account_events != ledger._account_events):
            raise ValueError("HEDGE_SPOT_LEDGER_MISMATCH")
        wal = self.controller._order_safety_wal
        if (wal is None or wal._uncertain or not wal.path.is_file() or wal.path.is_symlink()
                or IntentWAL(wal.path).all_records() != wal.all_records()):
            raise ValueError("HEDGE_SPOT_WAL_UNAVAILABLE")
        account = self.sender.account_observation()
        ct = self.sender.contract.contract_value_life
        long = max(account.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else account.long_contracts
        short = max(-account.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else account.short_contracts
        if (ledger is None or (ledger.life_balance, ledger.usdt_balance) != (obs.spot_life_base, obs.spot_cash_quote)
                or account.connector_ready is not True or account.snapshot_sequence != obs.sequence
                or account.account_uid != obs.account_uid or account.account_mode != obs.account_mode
                or account.position_mode.name != obs.position_mode or account.margin_mode != "cross"
                or account.leverage != obs.leverage or type(account.observed_at_ms) is not int
                or not 0 <= self.sender.clock_ms() - account.observed_at_ms <= self.sender.max_age_ms
                or (long * ct, short * ct) != (obs.long_base, obs.short_base)):
            raise ValueError("HEDGE_ACCOUNT_SNAPSHOT_MISMATCH")
        return obs

    def allows_spot(self):
        try:
            if not self._runtime_enabled:
                raise ValueError("HEDGE_RECOVERY_REQUIRED")
            with self.journal.locked() as state:
                now = self._clock(state)
                obs = self._snapshot()
                residual, urgent = self._residual(state, obs, now)
                self._market(state, now)
                allowed = (not urgent and abs(residual) <= self.policy.hedge.deadband_base
                           and all(o["status"] == "TERMINAL" for o in state["orders"].values())
                           and not obs.pending and self.capital.check().allowed)
                self.journal.commit(state)
                self.reason_code = "HEDGE_BALANCED" if allowed else "HEDGE_PAUSE_SPOT"
                return allowed
        except Exception:
            self.reason_code = "HEDGE_EVIDENCE_UNAVAILABLE"
            return False

    def _stops(self):
        records = self.controller._order_safety_wal.all_records() + self.sender.wal.all_records()
        return [StopExecutorAction(controller_id=self.controller.config.id, executor_id=r.intent_id)
                for r in records
                if r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") and not r.cancel_requested]

    def _poll_settlements(self):
        if self.controller._joint_recovery is not None:
            return  # One joint authority applies complete history and account facts.
        with self.journal.locked() as state:
            self._clock(state)
            pending = tuple(key for key, o in state["orders"].items() if o["status"] == "ISSUED")
        for key in pending:
            if self.settlement(key) is not None and not self.reconcile(key):
                raise ValueError("HEDGE_RECONCILIATION_REQUIRED")

    def propose(self):
        try:
            if not self._runtime_enabled:
                raise ValueError("HEDGE_RECOVERY_REQUIRED")
            self._poll_settlements()
            with self.journal.locked() as state:
                now = self._clock(state)
                obs = self._snapshot()
                residual, urgent = self._residual(state, obs, now)
                m = self._market(state, now)
                if any(o["status"] != "TERMINAL" for o in state["orders"].values()):
                    self.reason_code = "HEDGE_IN_FLIGHT"
                    self.journal.commit(state)
                    stops = []
                    for key, order in state["orders"].items():
                        if order["status"] == "TERMINAL":
                            continue
                        cfg = OrderExecutorConfig.model_validate(order["config"])
                        remaining = cfg.amount - Decimal(order["filled"])
                        if remaining > 0:
                            price, cost = self._cost(m, obs, cfg.side, remaining)
                            signed = remaining if cfg.side == TradeType.BUY else -remaining
                            if residual * signed >= 0 or remaining > abs(residual) or price != cfg.price or cost > Decimal(order["cost"]):
                                stops = self._stops()
                    # Spot quotes pause while a hedge exists; safe hedges keep working.
                    return [s for s in self._stops() if s.executor_id not in state["orders"]] + [
                        s for s in stops if s.executor_id in state["orders"]]
                if abs(residual) <= self.policy.hedge.deadband_base and not urgent:
                    self.reason_code = "HEDGE_DEADBAND"
                    self.journal.commit(state)
                    return []
                stops = self._stops()
                if stops or obs.pending or self.controller._runner_stops_pending():
                    self.reason_code = "HEDGE_WAIT_SPOT_RECONCILIATION"
                    self.journal.commit(state)
                    return stops
                if state["attempts"] >= self.policy.hedge.max_retry_attempts:
                    raise ValueError("HEDGE_RETRY_CAP")
                side = TradeType.SELL if residual > 0 else TradeType.BUY
                close = obs.long_base if side == TradeType.SELL else obs.short_base
                amount = min(abs(residual), self.policy.hedge.max_child_base)
                action = PositionAction.CLOSE if close > 0 else PositionAction.OPEN
                if close > 0:
                    amount = min(amount, close)
                contract = self.sender.contract
                contracts = (amount / contract.contract_value_life // contract.lot_size_contracts) * contract.lot_size_contracts
                amount = contracts * contract.contract_value_life
                if (not contract.valid_order_contracts(contracts)
                        or amount < self.policy.hedge.batch_base and not urgent):
                    raise ValueError("HEDGE_DUST_OR_BATCH_LIMIT")
                price, cost = self._cost(m, obs, side, amount)
                if Decimal(state["cost_hold"]) + cost > self.policy.cumulative_cost_budget_quote:
                    raise ValueError("HEDGE_CAMPAIGN_COST_LIMIT")
                cfg = OrderExecutorConfig(id="hedge-" + uuid.uuid4().hex, controller_id=self.controller.config.id,
                                          connector_name=self.controller.config.strategy.perpetual.connector,
                                          trading_pair=contract.trading_pair, side=side, amount=amount, price=price,
                                          execution_strategy=ExecutionStrategy.LIMIT_MAKER, position_action=action,
                                          leverage=self.capital.policy.leverage, level_id="0")
                state["orders"][cfg.id] = {"config": cfg.model_dump(mode="json"), "status": "PREPARING",
                                           "filled": "0", "cost": str(cost), "mark": str(obs.mark_price_usdt),
                                           "long": str(obs.long_base), "short": str(obs.short_base), "trades": {}}
                state["attempts"] += 1
                state["cost_hold"] = str(Decimal(state["cost_hold"]) + cost)
                self.journal.commit(state)  # Persist ownership before capital/WAL writes.
            self._live.add(cfg.id)
            action = self.sender.propose(cfg)
            with self.journal.locked() as state:
                self._clock(state)
                state["orders"][cfg.id]["status"] = "ISSUED"
                self.journal.commit(state)
            self.reason_code = "HEDGE_URGENT_BOUNDED_MAKER" if urgent else "HEDGE_ACTION_READY"
            return [action]
        except Exception as exc:
            self.reason_code = str(exc) if str(exc).startswith("HEDGE_") else "HEDGE_COORDINATOR_UNAVAILABLE"
            return self._stops()

    def authorizes(self, config, *, issued):
        try:
            if not self._runtime_enabled:
                return False
            with self.journal.locked() as state:
                now = self._clock(state)
                order = state["orders"].get(config.id)
                if (config.id not in self._live or order is None or order["config"] != config.model_dump(mode="json")
                        or order["status"] != ("ISSUED" if issued else "PREPARING") or order["filled"] != "0"):
                    return False
                obs = self._snapshot()
                residual, _ = self._residual(state, obs, now)
                m = self._market(state, now)
                if (any(r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")
                        for r in self.controller._order_safety_wal.all_records())
                        or any(p.product == "SPOT" for p in obs.pending)):
                    return False
                signed = config.amount if config.side == TradeType.BUY else -config.amount
                price, cost = self._cost(m, obs, config.side, config.amount)
                if (abs(residual + signed) > abs(residual) or residual * signed >= 0
                        or config.amount > abs(residual) or price != config.price or cost > Decimal(order["cost"])):
                    return False
                self.journal.commit(state)
                return True
        except Exception:
            return False

    def reconcile(self, intent_id):
        """Consume an explicit qualified full fill set; never infer terminal from ACK."""
        try:
            with self.journal.locked() as state:
                self._clock(state)
                order = state["orders"][intent_id]
                cfg = OrderExecutorConfig.model_validate(order["config"])
                proof = self.settlement(intent_id)
                wal = self.sender.wal.get(intent_id)
                obs = self._snapshot()
                if (not isinstance(proof, HedgeSettlement) or proof.source != "okx_reconciled"
                        or proof.scope_complete is not True or type(proof.terminal) is not bool
                        or proof.account_uid != obs.account_uid or proof.intent_id != intent_id
                        or proof.wire_id != wal.client_order_id or not proof.exchange_order_id
                        or proof.exchange_order_id != wal.exchange_order_id
                        or type(proof.snapshot_sequence) is not int or proof.snapshot_sequence != obs.sequence
                        or not number(proof.cumulative_base) or proof.cumulative_base > cfg.amount
                        or proof.cumulative_base < Decimal(order["filled"])
                        or not isinstance(proof.trades, tuple)
                        or proof.terminal != (wal.state == "TERMINAL")
                        or wal.state not in ("ACKED", "TERMINAL")):
                    raise ValueError("HEDGE_SETTLEMENT_UNQUALIFIED")
                trades, total, realized = {}, ZERO, ZERO
                for trade in proof.trades:
                    if (not isinstance(trade, HedgeTrade) or not isinstance(trade.trade_id, str) or not trade.trade_id
                            or not number(trade.quantity_base, True) or not number(trade.price_usdt, True)
                            or not number(trade.fee_quote) or trade.trade_id in trades
                            or trade.quantity_base % (self.sender.contract.contract_value_life
                                                      * self.sender.contract.lot_size_contracts) != 0
                            or (trade.price_usdt < cfg.price if cfg.side == TradeType.SELL else trade.price_usdt > cfg.price)):
                        raise ValueError("HEDGE_FILL_INVALID")
                    blob = _json(asdict(trade))
                    global_key = "SWAP:" + trade.trade_id
                    stable = {"intent": intent_id, "trade": blob}
                    if global_key in state["trades"] and state["trades"][global_key] != stable:
                        raise ValueError("HEDGE_TRADE_ID_CONFLICT")
                    trades[trade.trade_id] = blob
                    total += trade.quantity_base
                    mark = Decimal(order["mark"])
                    adverse = (mark - trade.price_usdt if cfg.side == TradeType.SELL else trade.price_usdt - mark)
                    realized += max(ZERO, adverse) * trade.quantity_base + trade.fee_quote
                if total != proof.cumulative_base or any(trades.get(k) != v for k, v in order["trades"].items()):
                    raise ValueError("HEDGE_FILL_SET_INCOMPLETE")
                long, short = Decimal(order["long"]), Decimal(order["short"])
                if cfg.position_action == PositionAction.CLOSE:
                    if cfg.side == TradeType.SELL:
                        long -= total
                    else:
                        short -= total
                elif cfg.side == TradeType.BUY:
                    long += total
                else:
                    short += total
                if min(long, short) < 0 or (long, short) != (obs.long_base, obs.short_base):
                    raise ValueError("HEDGE_FILL_POSITION_MISMATCH")
                if realized > Decimal(order["cost"]):
                    raise ValueError("HEDGE_REALIZED_COST_BREACH")
                for key, blob in trades.items():
                    state["trades"]["SWAP:" + key] = {"intent": intent_id, "trade": blob}
                order["filled"], order["trades"] = str(total), trades
                if proof.terminal:
                    order["status"] = "TERMINAL"
                self.journal.commit(state)
                self.reason_code = "HEDGE_TERMINAL_RECONCILED" if proof.terminal else "HEDGE_PARTIAL_RECONCILED"
                return True
        except Exception:
            self._faulted = True
            try:
                with self.journal.locked() as state:
                    state["fault"] = "HEDGE_RECONCILIATION_REQUIRED"
                    self.journal.commit(state)
            except Exception:
                pass  # Missing/uncertain storage already fences further actions.
            self.reason_code = "HEDGE_RECONCILIATION_REQUIRED"
            return False

    def reconcile_joint_order(self, order, *, authority, proof_id):
        """Use a complete joint-history proof; preserve retry/cost/target history."""
        if not authority.authorizes_order(order, proof_id) or authority.controller is not self.controller:
            raise ValueError("HEDGE_JOINT_PROOF_REQUIRED")
        with self.journal.locked() as state:
            self._validate_state(state)
            entry = state["orders"].get(order.claim.intent_id)
            if entry is None:
                raise ValueError("HEDGE_JOINT_OWNERSHIP_MISSING")
            cfg = OrderExecutorConfig.model_validate(entry["config"])
            c = order.claim
            if (cfg.amount, cfg.price, cfg.side.name, cfg.position_action.name) != (
                    c.quantity_base, c.price_usdt, c.side, c.position_action):
                raise ValueError("HEDGE_JOINT_ORDER_CHANGED")
            trades, cost = {}, ZERO
            for t in order.trades:
                blob = _json(asdict(HedgeTrade(t.trade_id, t.quantity_base, t.price_usdt, max(ZERO, -t.signed_fee))))
                key = "SWAP:" + t.trade_id
                stable = {"intent": cfg.id, "trade": blob}
                if key in state["trades"] and state["trades"][key] != stable:
                    raise ValueError("HEDGE_JOINT_TRADE_CHANGED")
                trades[t.trade_id] = blob
                mark = Decimal(entry["mark"])
                adverse = mark - t.price_usdt if c.side == "SELL" else t.price_usdt - mark
                cost += max(ZERO, adverse) * t.quantity_base + max(ZERO, -t.signed_fee)
                state["trades"][key] = stable
            if any(trades.get(k) != v for k, v in entry["trades"].items()):
                raise ValueError("HEDGE_JOINT_FILL_MISSING")
            entry["trades"] = trades
            entry["filled"] = str(sum((t.quantity_base for t in order.trades), ZERO))
            if order.state in ("filled", "canceled", "unsent"):
                entry["status"] = "TERMINAL"
            if cost > Decimal(entry["cost"]):
                state["fault"] = "HEDGE_REALIZED_COST_BREACH"
                self._runtime_enabled = False
            self.journal.commit(state)

    def rearm_joint(self, *, authority):
        if authority is not self.controller._joint_recovery or authority._fresh() is None:
            raise ValueError("HEDGE_JOINT_REARM_REQUIRED")
        with self.journal.locked() as state:
            self._validate_state(state)
            if (state["fault"] not in (None, "HEDGE_RECONCILIATION_REQUIRED")
                    or any(o["status"] != "TERMINAL" for o in state["orders"].values())):
                raise ValueError("HEDGE_JOINT_REARM_REQUIRED")
            state["fault"] = None
            self.journal.commit(state)
        self._faulted = False
        self._runtime_enabled = True

    def reconcile_joint_preparations(self, *, authority, proof_id):
        if (authority is not self.controller._joint_recovery or not authority._busy
                or authority.journal._state["proof_id"] != proof_id
                or authority.journal._state["phase"] != "APPLYING"):
            raise ValueError("HEDGE_JOINT_PROOF_REQUIRED")
        claims = self.capital.claims(include_settled=True)
        wal_ids = {r.intent_id for r in self.sender.wal.all_records()}
        with self.journal.locked() as state:
            self._validate_state(state)
            for key, entry in state["orders"].items():
                if entry["status"] == "PREPARING" and key not in claims and key not in wal_ids:
                    entry["status"] = "TERMINAL"  # No allocation/armed WAL; preserve attempt/cost holds.
            self.journal.commit(state)
