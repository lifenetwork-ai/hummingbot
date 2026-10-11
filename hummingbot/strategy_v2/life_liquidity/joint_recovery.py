"""Offline joint settlement/recovery contract for a dedicated LIFE account.

The injected collector must authenticate UID and return complete, consistently
cut regular/algo order, trade, bill and runner scope. Source strings are contract
labels, not authentication. Real collection/assembly is a D/R acceptance gate.
No order absence, ACK or cancellation ACK is evidence of a sent order's end.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from hummingbot.core.data_type.common import OrderType
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, _json
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

ZERO = Decimal("0")
TERMINAL = ("filled", "canceled", "unsent")


def finite(v, *, positive=False):
    return isinstance(v, Decimal) and v.is_finite() and (not positive or v > 0)


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RecoveryTrade:
    trade_id: str
    quantity_base: Decimal
    price_usdt: Decimal
    fee_currency: str
    signed_fee: Decimal
    realized_pnl_quote: Decimal
    at_ms: int


@dataclass(frozen=True)
class RecoveryPayment:
    bill_id: str
    signed_amount_quote: Decimal
    at_ms: int


@dataclass(frozen=True)
class RecoveryOrder:
    claim: CapitalClaim
    exchange_order_id: str | None
    state: str
    trades: tuple[RecoveryTrade, ...]


@dataclass(frozen=True)
class RecoveryBundle:
    account_uid: str
    coverage_from_ms: int
    observed_at_ms: int
    snapshot_sequence: int
    orders: tuple[RecoveryOrder, ...]
    pending_wire_ids: tuple[str, ...]
    algo_wire_ids: tuple[str, ...]
    scope_complete: bool
    runner_scope_complete: bool
    source: str
    payments: tuple[RecoveryPayment, ...] = ()


@dataclass(frozen=True)
class JointCapitalMeasurement:
    nav_quote: Decimal
    adjusted_nav_quote: Decimal
    highwater_quote: Decimal
    drawdown_bps: Decimal
    unrealized_quote: Decimal


class JointRecovery:
    def __init__(self, path: Path, *, controller, collect, cancel_swap, clock_ms,
                 max_age_ms: int, cancel_retry_ms: int, max_cancels_per_cycle: int, poll_interval_ms: int,
                 opening_long_entry: Decimal, opening_short_entry: Decimal, create: bool):
        self.controller = controller
        self.capital, self.sender = controller._shared_capital_authority, controller._protected_swap_sender
        self.spot_wal, self.reservations = controller._order_safety_wal, controller._order_safety_reservations
        self.collect, self.cancel_swap, self.clock_ms = collect, cancel_swap, clock_ms
        self.max_age_ms, self.cancel_retry_ms, self.max_cancels_per_cycle = max_age_ms, cancel_retry_ms, max_cancels_per_cycle
        self.poll_interval_ms = poll_interval_ms
        if (self.capital is None or self.sender is None or self.spot_wal is None or self.reservations is None
                or any(not callable(f) for f in (collect, cancel_swap, clock_ms))
                or any(type(n) is not int or n <= 0 for n in (max_age_ms, cancel_retry_ms, max_cancels_per_cycle, poll_interval_ms))
                or poll_interval_ms > max_age_ms
                or not finite(opening_long_entry) or not finite(opening_short_entry)
                or min(opening_long_entry, opening_short_entry) < 0):
            raise ValueError("JOINT_RECOVERY_BINDING_INVALID")
        self._binding = {"capital_policy": self.capital.journal.policy,
                         "capital_path": str(self.capital.journal.path.resolve()),
                         "swap_policy": self.sender.journal.policy,
                         "swap_wal": str(self.sender.wal.path.resolve()),
                         "spot_wal": str(self.spot_wal.path.resolve()),
                         "reservations": str(self.reservations.path.resolve()),
                         "max_age_ms": max_age_ms, "cancel_retry_ms": cancel_retry_ms,
                         "max_cancels_per_cycle": max_cancels_per_cycle,
                         "poll_interval_ms": poll_interval_ms,
                         "opening_long_entry": str(opening_long_entry), "opening_short_entry": str(opening_short_entry)}
        initial = {}
        if create:
            obs = self.capital.qualified_snapshot()
            if (self.capital.claim_ids() or any(r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")
                                                for r in self._records()) or obs.pending
                    or (obs.spot_life_base, obs.spot_cash_quote)
                    != (self.reservations.life_balance, self.reservations.usdt_balance)
                    or obs.long_base > 0 and opening_long_entry <= 0
                    or obs.short_base > 0 and opening_short_entry <= 0):
                raise ValueError("JOINT_RECOVERY_ANCHOR_REQUIRED")
            initial = {"anchor": _json(asdict(obs)), "excluded": [r.intent_id for r in self._records()],
                       "opening_cashflows": _json(self.reservations.cashflow_events),
                       "opening_joint": _json(self.reservations.joint_cash_events),
                       "last_ms": None, "last_poll_ms": None, "sequence": None, "bundle": None, "proof_id": None,
                       "phase": "EMPTY", "rearms": [], "valuation": None, "runner_fills": {}, "runner_cancels": {},
                       "highwater": str(obs.spot_cash_quote + obs.spot_life_base * obs.index_price_usdt
                                        + obs.long_base * (obs.mark_price_usdt - opening_long_entry)
                                        + obs.short_base * (opening_short_entry - obs.mark_price_usdt))}
            if Decimal(initial["highwater"]) <= 0:
                raise ValueError("JOINT_OPENING_NAV_INVALID")
        self.journal = PolicyState(path, policy=self._binding, initial=initial, create=create)
        self._armed, self._healthy, self._busy = create, False, False
        self.reason_code = "JOINT_RECOVERY_REQUIRED"
        self._mutex = asyncio.Lock()
        with self.journal.locked() as state:
            self._validate_state(state)

    def _records(self):
        return self.spot_wal.all_records() + self.sender.wal.all_records()

    def _validate_state(self, state):
        if (set(state) != {"anchor", "excluded", "opening_cashflows", "opening_joint", "last_ms", "last_poll_ms", "sequence",
                           "bundle", "proof_id", "phase", "rearms", "valuation", "highwater", "runner_fills", "runner_cancels"}
                or self.journal.policy != self._binding
                or self.capital.journal.policy != self._binding["capital_policy"]
                or self.sender.journal.policy != self._binding["swap_policy"]
                or self.controller._shared_capital_authority is not self.capital
                or self.controller._protected_swap_sender is not self.sender
                or state["phase"] not in ("EMPTY", "APPLYING", "COMMITTED")
                or not isinstance(state["excluded"], list) or not isinstance(state["rearms"], list)
                or any(state[k] is not None and (type(state[k]) is not int or state[k] < 0)
                       for k in ("last_ms", "last_poll_ms", "sequence"))
                or (state["bundle"] is None) != (state["proof_id"] is None)
                or state["bundle"] is not None and digest(state["bundle"]) != state["proof_id"]):
            raise ValueError("JOINT_RECOVERY_STATE_INVALID")
        if not finite(Decimal(state["highwater"]), positive=True):
            raise ValueError("JOINT_HIGHWATER_INVALID")
        anchor = state["anchor"]
        if (anchor["account_uid"] != self.capital.policy.account_uid
                or any(not finite(Decimal(anchor[k])) or Decimal(anchor[k]) < 0 for k in (
                    "long_base", "short_base", "spot_life_base", "spot_cash_quote"))):
            raise ValueError("JOINT_RECOVERY_ANCHOR_INVALID")

    def _verify_storage(self):
        for wal in (self.spot_wal, self.sender.wal):
            if (wal._uncertain or not wal.path.is_file() or wal.path.is_symlink()
                    or wal.all_records() != IntentWAL(wal.path).all_records()):
                raise ValueError("JOINT_WAL_UNVERIFIED")
        self.reservations.assert_healthy()
        disk = ReservationLedger.restore(self.reservations.path, limits=self.reservations.limits)
        if (disk.life_balance != self.reservations.life_balance or disk.usdt_balance != self.reservations.usdt_balance
                or disk._trades != self.reservations._trades
                or disk.reservation_snapshot() != self.reservations.reservation_snapshot()
                or disk.joint_cash_events != self.reservations.joint_cash_events
                or disk.cashflow_events != self.reservations.cashflow_events):
            raise ValueError("JOINT_RESERVATIONS_UNVERIFIED")
        ownership = self.controller._order_safety_account_lock
        if self.controller.config.recovery_state_dir is not None and (
                ownership is None or ownership._fd is None
                or ownership.path.name != f"okx-{self.capital.policy.account_uid}.lock"):
            raise ValueError("JOINT_ACCOUNT_OWNERSHIP_REQUIRED")

    def _qualify(self, bundle, state, now):
        obs = self.capital.qualified_snapshot()
        if (not isinstance(bundle, RecoveryBundle) or bundle.source != "okx_joint_reconciled"
                or bundle.account_uid != self.capital.policy.account_uid
                or bundle.scope_complete is not True or bundle.runner_scope_complete is not True
                or type(bundle.coverage_from_ms) is not int or bundle.coverage_from_ms < 0
                or bundle.coverage_from_ms > state["anchor"]["observed_at_ms"]
                or type(bundle.observed_at_ms) is not int or not 0 <= now - bundle.observed_at_ms <= self.max_age_ms
                or bundle.observed_at_ms != obs.observed_at_ms
                or type(bundle.snapshot_sequence) is not int or bundle.snapshot_sequence != obs.sequence
                or any(not isinstance(v, tuple) for v in (
                    bundle.orders, bundle.pending_wire_ids, bundle.algo_wire_ids, bundle.payments))
                or bundle.algo_wire_ids):
            raise ValueError("JOINT_SCOPE_UNQUALIFIED")
        if (self.controller._runner_scope_invalid
                or self.controller._runner_orchestrator is not None
                and not self.controller._runner_executor_scope_complete()):
            raise ValueError("JOINT_RUNNER_SCOPE_UNVERIFIED")
        encoded = _json(asdict(bundle))
        if state["sequence"] is not None and (
                bundle.snapshot_sequence < state["sequence"]
                or bundle.snapshot_sequence == state["sequence"] and encoded != state["bundle"]
                or bundle.observed_at_ms < state["bundle"]["observed_at_ms"]):
            raise ValueError("JOINT_HISTORY_REPLAY_OR_MUTATION")
        a = self.sender.account_observation()
        ct = self.capital.policy.contract_value_life
        long = max(a.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else a.long_contracts
        short = max(-a.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else a.short_contracts
        if (a.account_uid != obs.account_uid or a.account_mode != obs.account_mode
                or a.position_mode.name != obs.position_mode or a.margin_mode != obs.margin_mode
                or a.leverage != obs.leverage or a.connector_ready is not True
                or type(a.snapshot_sequence) is not int or a.snapshot_sequence != obs.sequence
                or type(a.observed_at_ms) is not int or not 0 <= now - a.observed_at_ms <= self.max_age_ms
                or any(not finite(v) for v in (a.net_contracts, a.long_contracts, a.short_contracts,
                                               a.pending_close_buy_contracts, a.pending_close_sell_contracts))
                or min(a.long_contracts, a.short_contracts, a.pending_close_buy_contracts, a.pending_close_sell_contracts) < 0
                or obs.position_mode == "ONEWAY" and (a.long_contracts != 0 or a.short_contracts != 0)
                or obs.position_mode == "HEDGE" and a.net_contracts != 0
                or (long * ct, short * ct) != (obs.long_base, obs.short_base)):
            raise ValueError("JOINT_SWAP_ACCOUNT_MISMATCH")
        claims = self.capital.claims(include_settled=True)
        records = {r.intent_id: r for r in self._records() if r.intent_id not in state["excluded"]}
        if len(records) != len([r for r in self._records() if r.intent_id not in state["excluded"]]):
            raise ValueError("JOINT_INTENT_COLLISION")
        orders = {}
        trades = {}
        old = {o["claim"]["intent_id"]: o for o in (state["bundle"] or {}).get("orders", [])}
        for order in bundle.orders:
            if not isinstance(order, RecoveryOrder) or not isinstance(order.claim, CapitalClaim):
                raise ValueError("JOINT_ORDER_INVALID")
            c = order.claim
            self.capital._validate_claim(c)
            if c.intent_id in orders or claims.get(c.intent_id) != c or not isinstance(order.trades, tuple):
                raise ValueError("JOINT_ORDER_IDENTITY_MISMATCH")
            r = records.get(c.intent_id)
            if order.state == "unsent":
                # The allocation precedes WAL.begin. No WAL or PREPARED WAL
                # proves the boundary was never armed, under one account owner.
                if (order.exchange_order_id is not None or order.trades
                        or r is not None and r.state not in ("PREPARED", "ABORTED_BEFORE_SEND")):
                    raise ValueError("JOINT_UNSENT_PROOF_INVALID")
            elif (order.state not in ("live", "partially_filled", "filled", "canceled") or r is None
                  or r.state in ("PREPARED", "ABORTED_BEFORE_SEND")
                  or not isinstance(order.exchange_order_id, str) or not order.exchange_order_id
                  or r.exchange_order_id not in (None, order.exchange_order_id)
                  or r.state == "TERMINAL" and order.state not in TERMINAL):
                raise ValueError("JOINT_ORDER_STATE_CONFLICT")
            if r is not None and (r.client_order_id, r.session_id, r.epoch, r.reservation_id, r.slot_market, r.slot_side) != (
                    c.wire_id, c.session_id, c.epoch, c.intent_id,
                    "LIFE-USDT" if c.product == "SPOT" else "LIFE-USDT-SWAP", c.side):
                raise ValueError("JOINT_WAL_IDENTITY_MISMATCH")
            total = ZERO
            for t in order.trades:
                if (not isinstance(t, RecoveryTrade) or not isinstance(t.trade_id, str) or not t.trade_id
                        or ":" in t.trade_id or not finite(t.quantity_base, positive=True)
                        or not finite(t.price_usdt, positive=True) or not finite(t.signed_fee)
                        or not finite(t.realized_pnl_quote) or t.fee_currency not in ("LIFE", "USDT")
                        or c.product == "SWAP" and (t.fee_currency != "USDT"
                                                    or t.quantity_base % (ct * self.capital.policy.lot_contracts) != 0)
                        or c.product == "SPOT" and t.realized_pnl_quote != 0
                        or type(t.at_ms) is not int or not state["anchor"]["observed_at_ms"] <= t.at_ms <= bundle.observed_at_ms
                        or (t.price_usdt > c.price_usdt if c.side == "BUY" else t.price_usdt < c.price_usdt)
                        or (c.product, t.trade_id) in trades):
                    raise ValueError("JOINT_TRADE_INVALID")
                total += t.quantity_base
                trades[c.product, t.trade_id] = (c, t)
            if total > c.quantity_base or order.state == "filled" and total != c.quantity_base:
                raise ValueError("JOINT_CUMULATIVE_INVALID")
            prior = old.get(c.intent_id)
            if prior is not None and (
                    prior["claim"] != _json(asdict(c))
                    or prior["exchange_order_id"] != order.exchange_order_id
                    or prior["state"] in TERMINAL and prior != _json(asdict(order))
                    or any(t not in _json(asdict(order))["trades"] for t in prior["trades"])):
                raise ValueError("JOINT_FILL_HISTORY_CHANGED")
            orders[c.intent_id] = order
        if set(orders) != set(claims) or set(records) - set(orders) or set(old) - set(orders):
            raise ValueError("JOINT_ORDER_SCOPE_INCOMPLETE")
        sent_swaps = tuple(r for r in self.sender.wal.all_records() if r.intent_id not in state["excluded"]
                           and r.state not in ("PREPARED", "ABORTED_BEFORE_SEND"))
        if sent_swaps and not self.runner_provenance_complete(sent_swaps):
            raise ValueError("JOINT_SWAP_PROVENANCE_UNVERIFIED")
        pending = tuple(o.claim.wire_id for o in orders.values() if o.state not in TERMINAL)
        if (len(set(bundle.pending_wire_ids)) != len(bundle.pending_wire_ids)
                or set(pending) != set(bundle.pending_wire_ids)
                or {c.wire_id for c in obs.pending} != set(pending)
                or any(claims.get(c.intent_id) != c for c in obs.pending)):
            raise ValueError("JOINT_PENDING_SCOPE_MISMATCH")
        for trade_id, hint in state["runner_fills"].items():
            c, t = trades["SWAP", trade_id]
            order = orders[c.intent_id]
            if hint != [c.intent_id, c.wire_id, order.exchange_order_id, str(t.quantity_base),
                        str(t.price_usdt), str(t.signed_fee)]:
                raise ValueError("JOINT_RUNNER_FILL_CONFLICT")
        for intent_id, exchange_id in state["runner_cancels"].items():
            order = orders[intent_id]
            if order.state not in TERMINAL or exchange_id not in (None, order.exchange_order_id):
                raise ValueError("JOINT_RUNNER_CANCEL_UNPROVEN")
        cash_events, valuation = self._financial_replay(bundle, state, obs, trades)
        coordinator = self.controller._hedge_coordinator
        if coordinator is not None:
            with coordinator.journal.locked() as hedge:
                coordinator._validate_state(hedge)
                for key, entry in hedge["orders"].items():
                    if key not in orders and entry["status"] != "TERMINAL":
                        if (entry["status"] != "PREPARING" or key in records
                                or key in self.sender.journal._read()["actions"]):
                            raise ValueError("JOINT_HEDGE_OWNERSHIP_INCOMPLETE")
        return obs, orders, cash_events, encoded, valuation

    def runner_provenance_complete(self, records):
        try:
            claims = self.capital.claims(include_settled=True)
            with self.sender.journal.locked() as state:
                for r in records:
                    c = claims[r.intent_id]
                    entry = state["actions"][r.intent_id]
                    p = entry["permit"]
                    cfg = OrderExecutorConfig.model_validate(entry["config"])
                    self.sender._validate(cfg)
                    if (c.product != "SWAP" or c.wire_id != r.client_order_id
                            or c.session_id != r.session_id or c.epoch != r.epoch
                            or p["intent_id"] != c.intent_id or p["client_order_id"] != c.wire_id
                            or p["account_uid"] != c.account_uid or p["session_id"] != c.session_id
                            or p["epoch"] != c.epoch or p["risk_epoch"] != c.risk_epoch
                            or p["config_version"] != c.config_version
                            or p["quantity_base"] != str(c.quantity_base) or p["price_usdt"] != str(c.price_usdt)
                            or p["side"] != c.side or p["position_action"] != c.position_action
                            or p["reservation_id"] != c.intent_id or p["instrument"] != self.sender.contract.instrument
                            or p["position_mode"] != self.capital.policy.position_mode or p["leverage"] != c.leverage
                            or Decimal(p["contract_value_life"]) != self.capital.policy.contract_value_life
                            or Decimal(p["contracts"]) * self.capital.policy.contract_value_life != c.quantity_base
                            or (cfg.id, cfg.amount, cfg.price, cfg.side.name, cfg.position_action.name, int(cfg.level_id))
                            != (c.intent_id, c.quantity_base, c.price_usdt, c.side, c.position_action, r.slot_level)):
                        return False
            return True
        except Exception:
            return False

    def _financial_replay(self, bundle, state, obs, trades):
        anchor = state["anchor"]
        life, cash = Decimal(anchor["spot_life_base"]), Decimal(anchor["spot_cash_quote"])
        long, short = Decimal(anchor["long_base"]), Decimal(anchor["short_base"])
        entries = {"long": Decimal(self._binding["opening_long_entry"]), "short": Decimal(self._binding["opening_short_entry"])}
        events = {}
        for c, t in sorted(trades.values(), key=lambda v: (v[1].at_ms, v[0].product, v[1].trade_id)):
            if c.product == "SPOT":
                sign = 1 if c.side == "BUY" else -1
                life += sign * t.quantity_base + (t.signed_fee if t.fee_currency == "LIFE" else ZERO)
                cash -= sign * t.quantity_base * t.price_usdt
                cash += t.signed_fee if t.fee_currency == "USDT" else ZERO
            else:
                leg = ("long" if c.side == "BUY" else "short") if c.position_action == "OPEN" else (
                    "long" if c.side == "SELL" else "short")
                size = long if leg == "long" else short
                if c.position_action == "OPEN":
                    if t.realized_pnl_quote != 0:
                        raise ValueError("JOINT_OPEN_PNL_INVALID")
                    entries[leg] = (entries[leg] * size + t.price_usdt * t.quantity_base) / (size + t.quantity_base)
                    size += t.quantity_base
                else:
                    pnl = (t.price_usdt - entries[leg]) * t.quantity_base * (1 if leg == "long" else -1)
                    if size < t.quantity_base or pnl != t.realized_pnl_quote:
                        raise ValueError("JOINT_CLOSE_PNL_INVALID")
                    size -= t.quantity_base
                if leg == "long":
                    long = size
                else:
                    short = size
                if obs.position_mode == "ONEWAY" and long > 0 and short > 0:
                    raise ValueError("JOINT_ONEWAY_REVERSE_INVALID")
                amount = t.realized_pnl_quote + t.signed_fee
                events[f"JOINT:{t.at_ms}:TRADE:{t.trade_id}"] = amount
                cash += amount
        payments = {}
        for p in bundle.payments:
            if (not isinstance(p, RecoveryPayment) or not isinstance(p.bill_id, str) or not p.bill_id.isascii()
                    or not p.bill_id.isdecimal() or p.bill_id in payments or not finite(p.signed_amount_quote)
                    or type(p.at_ms) is not int or not anchor["observed_at_ms"] <= p.at_ms <= bundle.observed_at_ms):
                raise ValueError("JOINT_FUNDING_INVALID")
            payments[p.bill_id] = _json(asdict(p))
            events[f"JOINT:{p.at_ms}:FUNDING:{p.bill_id}"] = p.signed_amount_quote
            cash += p.signed_amount_quote
        old_payments = {p["bill_id"]: p for p in (state["bundle"] or {}).get("payments", [])}
        if any(payments.get(k) != v for k, v in old_payments.items()):
            raise ValueError("JOINT_FUNDING_HISTORY_CHANGED")
        monitor = self.controller._carry_monitor
        if monitor is not None:
            monitor.check()  # Financial denial does not prevent qualifying facts.
            with monitor.journal.locked() as carry:
                funding = carry["streams"]["funding"]["blob"]
                account = carry["streams"]["account"]["blob"]
                expected = {p["bill_id"]: {"bill_id": p["bill_id"], "at_ms": p["settled_at_ms"],
                                           "signed_amount_quote": p["signed_payment_quote"]}
                            for p in funding["payments"] if p["settled_at_ms"] >= anchor["observed_at_ms"]}
                if (payments != expected or account["snapshot"] != _json(asdict(obs))
                        or funding["coverage_through_ms"] < bundle.observed_at_ms):
                    raise ValueError("JOINT_CARRY_HISTORY_MISMATCH")
        external_flows = ZERO
        for key, value in self.reservations.cashflow_events.items():
            encoded = _json(value)
            old = state["opening_cashflows"].get(key)
            if old is not None:
                if old != encoded:
                    raise ValueError("JOINT_CASHFLOW_CHANGED")
                continue
            currency, amount = value
            attributor = self.controller._fill_attributor
            if (attributor is None or attributor.cashflow_approvals is None
                    or attributor.cashflow_approvals.approved.get(key) != value):
                raise ValueError("JOINT_TRANSFER_APPROVAL_REQUIRED")
            if currency == "USDT":
                cash += amount
                external_flows += amount
            else:
                life += amount
                attributor = self.controller._fill_attributor
                transfer = None if attributor is None else attributor._cashflows.get(key)
                if transfer is None or not attributor._cashflow_matches(key, transfer):
                    raise ValueError("JOINT_TRANSFER_VALUE_REQUIRED")
                external_flows += amount * Decimal(transfer["independent_value_usdt"])
        if not set(state["opening_cashflows"]) <= set(self.reservations.cashflow_events):
            raise ValueError("JOINT_CASHFLOW_MISSING")
        if (life, cash, long, short) != (obs.spot_life_base, obs.spot_cash_quote, obs.long_base, obs.short_base):
            raise ValueError("JOINT_ACCOUNT_REPLAY_MISMATCH")
        known = {**state["opening_joint"], **{k: str(v) for k, v in events.items()}}
        if any(known.get(k) != str(v) for k, v in self.reservations.joint_cash_events.items()):
            raise ValueError("JOINT_CASH_HISTORY_CHANGED")
        unrealized = long * (obs.mark_price_usdt - entries["long"]) + short * (entries["short"] - obs.mark_price_usdt)
        nav = cash + life * obs.index_price_usdt + unrealized
        adjusted = nav - external_flows
        highwater = max(Decimal(state["highwater"]), adjusted)
        valuation = {"nav_quote": str(nav), "adjusted_nav_quote": str(adjusted), "highwater_quote": str(highwater),
                     "drawdown_bps": str(max(ZERO, highwater - adjusted) / highwater * Decimal("10000")),
                     "unrealized_quote": str(unrealized), "external_flows_quote": str(external_flows)}
        return events, valuation

    async def reconcile(self):
        async with self._mutex:
            self._busy = True
            self._healthy = False
            try:
                self._verify_storage()
                now = self.clock_ms()
                with self.journal.locked() as state:
                    self._validate_state(state)
                    if type(now) is not int or now < 0 or state["last_ms"] is not None and now < state["last_ms"]:
                        raise ValueError("JOINT_CLOCK_ROLLBACK")
                    state["last_ms"] = state["last_poll_ms"] = now
                    self.journal.commit(state)
                self.sender.request_budget.charge("STATUS", "joint:" + uuid.uuid4().hex)
                bundle = await asyncio.wait_for(self.collect(), self.max_age_ms / 1000)
                now = self.clock_ms()
                with self.journal.locked() as state:
                    self._validate_state(state)
                    if type(now) is not int or now < state["last_ms"]:
                        raise ValueError("JOINT_CLOCK_ROLLBACK")
                    state["last_ms"] = now
                    obs, orders, events, encoded, valuation = self._qualify(bundle, state, now)
                    state.update(sequence=obs.sequence, bundle=encoded, proof_id=digest(encoded), phase="APPLYING",
                                 valuation=valuation, highwater=valuation["highwater_quote"])
                    self.journal.commit(state)  # Intent-to-settle precedes every financial/WAL checkpoint.
                    self._apply(orders, events, state["proof_id"])
                    if self.controller._hedge_coordinator is not None:
                        self.controller._hedge_coordinator.reconcile_joint_preparations(authority=self, proof_id=state["proof_id"])
                    if (self.reservations.life_balance, self.reservations.usdt_balance) != (
                            obs.spot_life_base, obs.spot_cash_quote):
                        raise ValueError("JOINT_LEDGER_ACCOUNT_MISMATCH")
                    state["phase"] = "COMMITTED"
                    state["runner_fills"], state["runner_cancels"] = {}, {}
                    self.journal.commit(state)  # Cross-journal completion is last.
                self._healthy = True
                self.reason_code = "JOINT_RECONCILED" if self._armed else "JOINT_OPERATOR_REARM_REQUIRED"
                return True
            except asyncio.CancelledError:
                self._armed = False
                self.reason_code = "JOINT_RECONCILIATION_INTERRUPTED"
                raise
            except Exception as exc:
                self._armed = False
                self.reason_code = str(exc) if str(exc).startswith("JOINT_") else "JOINT_RECONCILIATION_UNAVAILABLE"
                return False
            finally:
                self._busy = False

    def _apply(self, orders, events, proof_id):
        spot = tuple((o.claim.intent_id, tuple((t.trade_id, t.quantity_base, t.price_usdt,
                                                t.fee_currency, t.signed_fee, t.at_ms) for t in o.trades))
                     for o in orders.values() if o.claim.product == "SPOT" and o.state != "unsent")
        self.reservations.apply_joint_snapshot(spot, events)
        gateway = self.controller._order_safety_gateway
        attribution = []
        for order in orders.values():
            if order.claim.product == "SPOT" and order.state != "unsent":
                fills = tuple(SpotFill(t.trade_id, t.quantity_base, t.price_usdt, t.fee_currency, t.signed_fee, t.at_ms)
                              for t in order.trades)
                total = sum((t.quantity_base for t in order.trades), ZERO)
                attribution.append((order.claim.wire_id, fills, total))
        # All spot fills are already durable. Complete every attribution journal
        # before checking readiness, including a crash between per-order writes.
        if gateway.apply_fills is None and attribution:
            raise ValueError("JOINT_SPOT_ATTRIBUTION_REQUIRED")
        incomplete = [args for args in attribution if gateway.apply_fills(*args) is not True]
        if any(gateway.apply_fills(*args) is not True for args in incomplete):
            raise ValueError("JOINT_SPOT_ATTRIBUTION_REQUIRED")
        for order in orders.values():
            c = order.claim
            wal = self.spot_wal if c.product == "SPOT" else self.sender.wal
            records = {r.intent_id: r for r in wal.all_records()}
            total = sum((t.quantity_base for t in order.trades), ZERO)
            if order.state == "unsent":
                if c.intent_id in records:
                    wal.abort_before_send(c.intent_id)
                    if c.product == "SPOT" and c.intent_id in self.reservations.reservation_ids:
                        self.reservations.abort_unsent(c.intent_id, session_id=c.session_id, epoch=c.epoch, wal=wal)
                if c.product == "SWAP":
                    self.sender.reconcile_unsent(order, authority=self, proof_id=proof_id)
            elif order.state in TERMINAL:
                if c.product == "SPOT":
                    self.reservations.confirm_terminal(c.intent_id, cumulative_filled=total, fills_reconciled=True,
                                                       exchange_state="FILLED" if order.state == "filled" else "CANCELED")
                wal.mark_exchange_terminal_observed(c.intent_id, order.exchange_order_id)
                wal.mark_terminal(c.intent_id, order.exchange_order_id)
            elif wal.get(c.intent_id).state == "SEND_UNKNOWN":
                wal.acknowledge(c.intent_id, order.exchange_order_id)
            if c.product == "SWAP" and self.controller._execution_loss_budget is not None:
                for t in order.trades:
                    self.controller._execution_loss_budget.record(
                        "swap:" + t.trade_id, max(ZERO, -t.realized_pnl_quote) + max(ZERO, -t.signed_fee),
                        session_id=c.session_id, at_utc=datetime.fromtimestamp(t.at_ms / 1000, timezone.utc))
            if c.product == "SWAP" and self.controller._hedge_coordinator is not None:
                self.controller._hedge_coordinator.reconcile_joint_order(order, authority=self, proof_id=proof_id)
            if c.product == "SPOT" and order.state in TERMINAL:
                callback = self.controller._order_safety_gateway.on_terminal_reconciled
                if callback is not None and callback(c.intent_id, total) is not True:
                    raise ValueError("JOINT_SPOT_TERMINAL_ATTRIBUTION_REQUIRED")
            if order.state in TERMINAL:
                self.capital.settle(c, proof_id, authority=self)

    def authorizes_release(self, claim, proof_id):
        try:
            data = self.journal._read()
            if (not self._busy or data != self.journal._state or data["phase"] != "APPLYING"
                    or data["proof_id"] != proof_id):
                return False
            order = next(o for o in data["bundle"]["orders"] if o["claim"] == _json(asdict(claim)))
            if order["state"] not in TERMINAL:
                return False
            wal = self.spot_wal if claim.product == "SPOT" else self.sender.wal
            records = {r.intent_id: r for r in wal.all_records()}
            return (claim.intent_id not in records and order["state"] == "unsent"
                    or records[claim.intent_id].state in ("TERMINAL", "ABORTED_BEFORE_SEND"))
        except Exception:
            return False

    def authorizes_order(self, order, proof_id):
        try:
            state = self.journal._read()
            return (self._busy and state == self.journal._state and state["phase"] == "APPLYING"
                    and state["proof_id"] == proof_id and _json(asdict(order)) in state["bundle"]["orders"])
        except Exception:
            return False

    def _fresh(self):
        self._verify_storage()
        with self.journal.locked() as state:
            self._validate_state(state)
            now = self.clock_ms()
            if type(now) is not int or now < 0 or state["last_ms"] is not None and now < state["last_ms"]:
                raise ValueError("JOINT_CLOCK_ROLLBACK")
            state["last_ms"] = now
            self.journal.commit(state)  # An observation failure must not erase an observed clock advance.
            if (not self._healthy or self._busy or state["phase"] != "COMMITTED"
                    or state["runner_fills"] or state["runner_cancels"]
                    or not 0 <= now - state["bundle"]["observed_at_ms"] <= self.max_age_ms
                    or self.capital.qualified_snapshot().sequence != state["sequence"]):
                raise ValueError("JOINT_FRESH_PROOF_REQUIRED")
            return state

    def observe_runner_fill(self, event):
        """Persist tracker hints; only complete exchange evidence may consume them."""
        r = self.sender.wal.find_by_client_order_id(event.order_id)
        fee = event.trade_fee
        if (event.trading_pair != self.sender.contract.trading_pair or r.slot_market != self.sender.contract.instrument
                or r.state == "ABORTED_BEFORE_SEND" or r.slot_side != event.trade_type.name
                or event.order_type != OrderType.LIMIT_MAKER or not finite(event.amount, positive=True)
                or not finite(event.price, positive=True) or not isinstance(event.exchange_trade_id, str)
                or not event.exchange_trade_id or not isinstance(event.exchange_order_id, str) or not event.exchange_order_id
                or r.exchange_order_id not in (None, event.exchange_order_id)
                or not isinstance(fee, TradeFeeBase) or fee.percent != 0 or len(fee.flat_fees) != 1
                or fee.flat_fees[0].token != "USDT" or fee.percent_token not in (None, "USDT")
                or not finite(fee.flat_fees[0].amount)):
            raise ValueError("JOINT_RUNNER_FILL_INVALID")
        hint = [r.intent_id, r.client_order_id, event.exchange_order_id,
                str(event.amount), str(event.price), str(-fee.flat_fees[0].amount)]
        with self.journal.locked() as state:
            self._validate_state(state)
            prior = state["runner_fills"].get(event.exchange_trade_id)
            if prior is not None and prior != hint:
                raise ValueError("JOINT_RUNNER_FILL_CONFLICT")
            state["runner_fills"][event.exchange_trade_id] = hint
            self.journal.commit(state)

    def observe_runner_cancel(self, event):
        r = self.sender.wal.find_by_client_order_id(event.order_id)
        if r.state == "ABORTED_BEFORE_SEND" or event.exchange_order_id not in (None, r.exchange_order_id):
            if r.exchange_order_id is not None or r.state == "ABORTED_BEFORE_SEND":
                raise ValueError("JOINT_RUNNER_CANCEL_CONFLICT")
        with self.journal.locked() as state:
            self._validate_state(state)
            prior = state["runner_cancels"].get(r.intent_id)
            if prior is not None and event.exchange_order_id not in (None, prior):
                raise ValueError("JOINT_RUNNER_CANCEL_CONFLICT")
            state["runner_cancels"][r.intent_id] = event.exchange_order_id or prior
            self.journal.commit(state)

    def allocation_ready(self):
        try:
            return self._armed and self._fresh() is not None
        except Exception:
            return False

    def measure(self):
        """Joint wallet + independently valued LIFE + SWAP UPL; no collateral addition.

        Settled PnL/fees/funding already change wallet cash. Only approved
        external transfers adjust performance; receipts never refund budgets.
        """
        try:
            state = self._fresh()
            v = state["valuation"]
            return JointCapitalMeasurement(*(Decimal(v[k]) for k in (
                "nav_quote", "adjusted_nav_quote", "highwater_quote", "drawdown_bps", "unrealized_quote")))
        except Exception:
            return None

    def successor_ready(self, session_id, epoch):
        try:
            self._fresh()
            return (not any((r.session_id, r.epoch) == (session_id, epoch)
                            and r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") for r in self._records())
                    and not any((c.session_id, c.epoch) == (session_id, epoch) for c in self.capital.claims().values()))
        except Exception:
            return False

    def manual_rearm(self, *, operator_id):
        self._armed = False
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ValueError("JOINT_OPERATOR_REQUIRED")
        self._fresh()
        if (self.capital.claim_ids() or any(r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") for r in self._records())
                or not self.capital._evaluate(self.capital.qualified_snapshot(), {}).allowed
                or not self.controller._carry_ready() or self.controller._order_safety_stopped
                or not self.controller._execution_loss_ready()
                or self.controller._runtime_risk_gate is not None and self.controller._runtime_risk_gate.state == "HALTED"):
            raise ValueError("JOINT_REARM_PROOF_REQUIRED")
        coordinator = self.controller._hedge_coordinator
        if coordinator is not None:
            coordinator.rearm_joint(authority=self)
        with self.journal.locked() as state:
            state["rearms"].append({"operator_id": operator_id, "at_ms": self.clock_ms(), "proof_id": state["proof_id"]})
            self.journal.commit(state)
        self._armed = True
        self.sender._runtime_enabled = True
        self.reason_code = "JOINT_REARMED"

    def reconciliation_due(self):
        try:
            with self.journal.locked() as state:
                self._validate_state(state)
                return state["last_poll_ms"] is None or self.clock_ms() - state["last_poll_ms"] >= self.poll_interval_ms
        except Exception:
            return True  # Attempt recovery, never infer health from a read error.

    async def cancel_working_swaps(self):
        try:
            return await self._cancel_working_swaps()
        except BaseException:
            self._healthy, self._armed = False, False
            self.reason_code = "JOINT_CANCEL_RECONCILIATION_REQUIRED"
            raise

    async def _cancel_working_swaps(self):
        """Persist attempts before transport; retry ACKs do not release claims."""
        async with self._mutex:
            self._verify_storage()
            now = self.clock_ms()
            with self.journal.locked() as state:
                self._validate_state(state)
                if type(now) is not int or now < 0 or state["last_ms"] is not None and now < state["last_ms"]:
                    raise ValueError("JOINT_CLOCK_ROLLBACK")
                state["last_ms"] = now
                self.journal.commit(state)
            count = 0
            claims = self.capital.claims()
            for r in self.sender.wal.all_records():
                if r.state not in ("SEND_UNKNOWN", "ACKED"):
                    continue
                if r.last_cancel_attempt_at is not None:
                    previous = int(datetime.fromisoformat(r.last_cancel_attempt_at).timestamp() * 1000)
                    if now - previous < self.cancel_retry_ms:
                        continue
                if count >= self.max_cancels_per_cycle:
                    break
                c = claims.get(r.intent_id)
                if c is None or c.product != "SWAP" or c.wire_id != r.client_order_id:
                    raise ValueError("JOINT_CANCEL_CLAIM_MISMATCH")
                self.sender.wal.mark_cancel_attempt(r.intent_id, at=datetime.fromtimestamp(now / 1000, timezone.utc))
                self.sender._revoked.add(r.intent_id)
                self.sender.request_budget.charge("CANCEL", f"joint:{r.client_order_id}:{r.cancel_attempts + 1}")
                count += 1
                await self.cancel_swap(c)
