"""Qualified offline carry streams and durable funding attribution.

Account collateral already includes settled funding. Forecast funding remains a
separate hold; expected receipts never finance risk and history cannot disappear on roll.
Authentication/feed collection is a separate demo/recovery integration gate.
"""

from copy import copy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.shared_capital import (
    CapitalClaim,
    CapitalDecision,
    CapitalSnapshot,
    MarginTier,
    SharedCapitalAuthority,
    _json,
)

ZERO = Decimal("0")


def number(value, signed=False):
    return isinstance(value, Decimal) and value.is_finite() and (signed or value >= 0)


@dataclass(frozen=True)
class CarryPolicy:
    account_uid: str
    instrument: str
    anchor_ms: int
    max_age_ms: int
    max_skew_ms: int
    horizon_ms: int
    max_schedule_gap_ms: int
    absolute_rate_stress: Decimal
    funding_budget_quote: Decimal
    max_basis_bps: Decimal

    def __post_init__(self):
        if (not isinstance(self.account_uid, str) or not self.account_uid.isascii() or not self.account_uid.isdecimal()
                or self.instrument != "LIFE-USDT-SWAP"
                or type(self.anchor_ms) is not int or self.anchor_ms < 0
                or any(type(v) is not int or v <= 0 for v in (
                    self.max_age_ms, self.max_skew_ms, self.horizon_ms, self.max_schedule_gap_ms))
                or not all(number(v) for v in (self.absolute_rate_stress, self.funding_budget_quote, self.max_basis_bps))
                or self.absolute_rate_stress > 1):
            raise ValueError("CARRY_POLICY_INVALID")


@dataclass(frozen=True)
class CarryPrice:
    instrument: str
    sequence: int
    observed_at_ms: int
    price_usdt: Decimal
    source: str


@dataclass(frozen=True)
class FundingPayment:
    bill_id: str
    settled_at_ms: int
    signed_payment_quote: Decimal  # Positive receipt, negative debit, denominated in USDT.


@dataclass(frozen=True)
class CarryFunding:
    account_uid: str
    instrument: str
    sequence: int
    observed_at_ms: int
    rate: Decimal  # Positive rate: longs pay shorts.
    settlement_ms: int
    next_settlement_ms: int
    coverage_from_ms: int
    coverage_through_ms: int
    payments: tuple[FundingPayment, ...]
    scope_complete: bool
    source: str


@dataclass(frozen=True)
class CarryTerms:
    account_uid: str
    instrument: str
    sequence: int
    observed_at_ms: int
    maker_fee_rate: Decimal  # Nonnegative conservative costs; no rebate credit.
    taker_fee_rate: Decimal
    tiers: tuple[MarginTier, ...]
    scope_complete: bool
    source: str


@dataclass(frozen=True)
class CarryAccount:
    snapshot: CapitalSnapshot
    included_funding_bill_ids: tuple[str, ...]
    settled_funding_included: bool
    unsettled_funding_only: bool  # Snapshot liability excludes settled bills and forecast holds.


@dataclass(frozen=True)
class CarryBundle:
    account: CarryAccount
    mark: CarryPrice
    index: CarryPrice
    funding: CarryFunding
    terms: CarryTerms


@dataclass(frozen=True)
class CarryDecision:
    allowed: bool
    reason_code: str
    funding_reserve_quote: Decimal = ZERO
    expected_payment_quote: Decimal = ZERO
    settled_payment_quote: Decimal = ZERO
    funding_debits_quote: Decimal = ZERO
    funding_events: int = 0
    fee_rate: Decimal = ZERO
    funding_cost_per_base: Decimal = ZERO
    capital: CapitalDecision | None = None
    funding_reference_price_usdt: Decimal = ZERO


class CarryMonitor:
    def __init__(self, path: Path, *, policy: CarryPolicy, capital: SharedCapitalAuthority,
                 observation, clock_ms, create: bool):
        if (not isinstance(policy, CarryPolicy) or not isinstance(capital, SharedCapitalAuthority)
                or policy.account_uid != capital.policy.account_uid or not callable(observation) or not callable(clock_ms)):
            raise ValueError("CARRY_BINDING_INVALID")
        self.policy, self.capital, self.observation, self.clock_ms = policy, capital, observation, clock_ms
        self._binding = {"policy": _json(asdict(policy)), "capital_policy": capital.journal.policy,
                         "capital_path": str(capital.journal.path.resolve())}
        self.journal = PolicyState(path, policy=self._binding,
                                   initial={"last_ms": None, "streams": {}, "payments": {}}, create=create)
        self.last_decision = CarryDecision(False, "CARRY_STARTUP_REVALIDATION")
        with self.journal.locked() as state:
            self._validate_state(state)

    def _validate_state(self, state):
        if (set(state) != {"last_ms", "streams", "payments"}
                or state["last_ms"] is not None and (type(state["last_ms"]) is not int or state["last_ms"] < 0)
                or not isinstance(state["streams"], dict) or not isinstance(state["payments"], dict)
                or set(state["streams"]) not in (set(), {"account", "mark", "index", "funding", "terms"})
                or self._binding != self.journal.policy or _json(asdict(self.policy)) != self._binding["policy"]
                or self.capital.journal.policy != self._binding["capital_policy"]):
            raise ValueError("CARRY_STATE_INVALID")
        for name, stream in state["streams"].items():
            blob = stream["blob"]["snapshot"] if name == "account" else stream["blob"]
            if (type(stream["sequence"]) is not int or stream["sequence"] < 1
                    or stream["sequence"] != blob["sequence"] or type(blob["observed_at_ms"]) is not int
                    or state["last_ms"] is None or blob["observed_at_ms"] > state["last_ms"]):
                raise ValueError("CARRY_STATE_INVALID")
        payments = {}
        for key, raw in state["payments"].items():
            if not isinstance(raw["signed_payment_quote"], str):
                raise ValueError("CARRY_PAYMENT_INVALID")
            payment = FundingPayment(raw["bill_id"], raw["settled_at_ms"], Decimal(raw["signed_payment_quote"]))
            self._payment(payment, state["last_ms"])
            if key != payment.bill_id:
                raise ValueError("CARRY_PAYMENT_INVALID")
            payments[key] = _json(asdict(payment))
        if state["streams"]:
            stored = {r["bill_id"]: r for r in state["streams"]["funding"]["blob"]["payments"]}
            if stored != payments or set(state["streams"]["account"]["blob"]["included_funding_bill_ids"]) != set(payments):
                raise ValueError("CARRY_FUNDING_HISTORY_MISMATCH")
        elif payments:
            raise ValueError("CARRY_FUNDING_HISTORY_MISMATCH")

    def _payment(self, payment, through):
        if (not isinstance(payment, FundingPayment) or not isinstance(payment.bill_id, str) or not payment.bill_id
                or type(payment.settled_at_ms) is not int or not self.policy.anchor_ms <= payment.settled_at_ms <= through
                or not number(payment.signed_payment_quote, signed=True)):
            raise ValueError("CARRY_PAYMENT_INVALID")

    def _stamp(self, value, now):
        if (type(value.sequence) is not int or value.sequence < 1
                or type(value.observed_at_ms) is not int or not 0 <= now - value.observed_at_ms <= self.policy.max_age_ms):
            raise ValueError("CARRY_STREAM_STALE_OR_INVALID")

    def _qualify(self, bundle, state, now):
        p = self.policy
        if (not isinstance(bundle, CarryBundle) or not isinstance(bundle.account, CarryAccount)
                or not isinstance(bundle.account.snapshot, CapitalSnapshot)
                or not isinstance(bundle.mark, CarryPrice) or not isinstance(bundle.index, CarryPrice)
                or not isinstance(bundle.funding, CarryFunding) or not isinstance(bundle.terms, CarryTerms)):
            raise ValueError("CARRY_BUNDLE_INVALID")
        account, mark, index, funding, terms = bundle.account, bundle.mark, bundle.index, bundle.funding, bundle.terms
        obs = self.capital.qualified_snapshot()
        if (account.snapshot != obs or account.settled_funding_included is not True
                or account.unsettled_funding_only is not True
                or not isinstance(account.included_funding_bill_ids, tuple)
                or any(not isinstance(k, str) or not k for k in account.included_funding_bill_ids)
                or len(set(account.included_funding_bill_ids)) != len(account.included_funding_bill_ids)
                or obs.mark_price_usdt != mark.price_usdt or obs.index_price_usdt != index.price_usdt
                or mark.source != "okx_mark" or index.source != "okx_index"
                or not number(mark.price_usdt) or mark.price_usdt <= 0
                or not number(index.price_usdt) or index.price_usdt <= 0):
            raise ValueError("CARRY_PRICE_OR_ACCOUNT_UNQUALIFIED")
        for value in (mark, index, funding, terms):
            self._stamp(value, now)
            if value.instrument != p.instrument:
                raise ValueError("CARRY_INSTRUMENT_MISMATCH")
        self._stamp(obs, now)
        if max(v.observed_at_ms for v in (obs, mark, index, funding, terms)) - min(
                v.observed_at_ms for v in (obs, mark, index, funding, terms)) > p.max_skew_ms:
            raise ValueError("CARRY_STREAM_SKEW")
        if (funding.account_uid != p.account_uid or terms.account_uid != p.account_uid
                or funding.scope_complete is not True or terms.scope_complete is not True
                or funding.source != "okx_funding" or terms.source != "okx_account_terms"
                or not number(funding.rate, signed=True) or abs(funding.rate) > 1
                or any(type(v) is not int for v in (funding.settlement_ms, funding.next_settlement_ms,
                                                    funding.coverage_from_ms, funding.coverage_through_ms))
                or not now < funding.settlement_ms < funding.next_settlement_ms
                or funding.settlement_ms - now > p.max_schedule_gap_ms
                or funding.next_settlement_ms - funding.settlement_ms > p.max_schedule_gap_ms
                or funding.coverage_from_ms != p.anchor_ms
                or not p.anchor_ms <= funding.coverage_through_ms <= funding.observed_at_ms
                or now - funding.coverage_through_ms > p.max_age_ms
                or not isinstance(funding.payments, tuple)
                or not number(terms.maker_fee_rate) or not number(terms.taker_fee_rate)
                or max(terms.maker_fee_rate, terms.taker_fee_rate) > 1):
            raise ValueError("CARRY_FUNDING_OR_TERMS_UNQUALIFIED")
        # CapitalPolicy validates whole-notional tier ordering and finite rates.
        dynamic_policy = replace(self.capital.policy, tiers=terms.tiers,
                                 fee_rate=max(self.capital.policy.fee_rate, terms.maker_fee_rate, terms.taker_fee_rate))
        payments = {}
        for payment in funding.payments:
            self._payment(payment, funding.coverage_through_ms)
            if payment.bill_id in payments:
                raise ValueError("CARRY_PAYMENT_DUPLICATE")
            payments[payment.bill_id] = _json(asdict(payment))
        if (any(payments.get(k) != v for k, v in state["payments"].items())
                or set(account.included_funding_bill_ids) != set(payments)):
            raise ValueError("CARRY_FUNDING_HISTORY_OR_ACCOUNT_MISMATCH")
        previous = state["streams"].get("funding")
        if previous is not None:
            old = previous["blob"]
            if (funding.coverage_through_ms < old["coverage_through_ms"]
                    or old["settlement_ms"] <= now and funding.coverage_through_ms < old["settlement_ms"]):
                raise ValueError("CARRY_SETTLEMENT_COVERAGE_GAP")
        streams = {"account": account, "mark": mark, "index": index, "funding": funding, "terms": terms}
        for name, value in streams.items():
            stamp = value.snapshot if name == "account" else value
            encoded = _json(asdict(value))
            previous = state["streams"].get(name)
            if previous is not None:
                old_stamp = previous["blob"]["snapshot"] if name == "account" else previous["blob"]
                if (stamp.sequence < previous["sequence"] or stamp.observed_at_ms < old_stamp["observed_at_ms"]
                        or stamp.sequence == previous["sequence"] and encoded != previous["blob"]):
                    raise ValueError("CARRY_STREAM_REPLAY_OR_MUTATION")
        state["streams"] = {name: {"sequence": (v.snapshot if name == "account" else v).sequence,
                                   "blob": _json(asdict(v))} for name, v in streams.items()}
        state["payments"] = payments
        return obs, dynamic_policy

    def check(self, prospective: CapitalClaim | None = None):
        try:
            with self.journal.locked() as state:
                self._validate_state(state)
                now = self.clock_ms()
                if (type(now) is not int or now < self.policy.anchor_ms
                        or state["last_ms"] is not None and now < state["last_ms"]):
                    raise ValueError("CARRY_CLOCK_ROLLBACK")
                state["last_ms"] = now
                self.journal.commit(state)  # A failed feed must not erase an observed clock advance.
                bundle = self.observation()
                obs, policy = self._qualify(bundle, state, now)
                self.journal.commit(state)  # Preserve newer expensive evidence even on financial refusal.
                with self.capital.journal.locked() as capital_state:
                    claims = self.capital._decode(capital_state)
                if prospective is not None:
                    self.capital._validate_claim(prospective)
                    if prospective.intent_id in claims and claims[prospective.intent_id] != prospective:
                        raise ValueError("CARRY_CLAIM_CHANGED")
                    claims[prospective.intent_id] = prospective
                orders = dict(claims)
                for pending in obs.pending:
                    if pending.intent_id in orders and orders[pending.intent_id] != pending:
                        raise ValueError("CARRY_PENDING_CONFLICT")
                    orders[pending.intent_id] = pending
                long, short = obs.long_base, obs.short_base
                for order in orders.values():
                    if order.product == "SWAP" and order.position_action == "OPEN":
                        if order.side == "BUY":
                            long += order.quantity_base
                        else:
                            short += order.quantity_base
                funding = bundle.funding
                interval = funding.next_settlement_ms - funding.settlement_ms
                events = 1 + self.policy.horizon_ms // interval  # Explicit dynamic cadence, conservative phase.
                rate = max(abs(funding.rate), self.policy.absolute_rate_stress)
                funding_price = max(obs.mark_price_usdt, obs.index_price_usdt,
                                    *(o.price_usdt for o in orders.values()))
                stress_price = funding_price * (1 + policy.price_stress_bps / Decimal("10000"))
                per_base = stress_price * rate * events
                reserve = (long + short) * per_base
                paid = sum((v.signed_payment_quote for v in funding.payments), ZERO)
                debits = sum((max(ZERO, -v.signed_payment_quote) for v in funding.payments), ZERO)
                expected = -(obs.long_base - obs.short_base) * obs.mark_price_usdt * funding.rate
                virtual = copy(self.capital)
                virtual.policy = policy  # Local financial evaluator; immutable authority/journal unchanged.
                # Known unpaid funding and future stress are distinct. Settled bills
                # are already in account collateral and must not be deducted again.
                liability = obs.funding_liability_quote + reserve
                financial = virtual._evaluate(replace(obs, funding_liability_quote=liability), claims)
                basis = abs(obs.mark_price_usdt - obs.index_price_usdt) / obs.index_price_usdt * Decimal("10000")
                reason = ("CARRY_BASIS_LIMIT" if basis > self.policy.max_basis_bps else
                          "CARRY_FUNDING_BUDGET" if debits + liability > self.policy.funding_budget_quote else
                          financial.reason_code if not financial.allowed else "CARRY_READY")
                decision = CarryDecision(reason == "CARRY_READY", reason, reserve, expected, paid, debits,
                                         events, policy.fee_rate, per_base, financial, funding_price)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("CARRY_") else "CARRY_EVIDENCE_UNAVAILABLE"
            decision = CarryDecision(False, reason)
        self.last_decision = decision
        return decision
