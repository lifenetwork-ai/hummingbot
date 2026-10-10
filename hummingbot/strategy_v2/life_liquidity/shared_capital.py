"""One durable LIFE spot/SWAP capital authority, with no inferred claim release.

Snapshot providers must qualify the whole account. All pending-fill endpoints
are stressed independently; quoted hedge proceeds never finance another order.
PolicyState supplies host-local locking, atomic checkpoints and writer fencing.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import product
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.joint_exposure import JointRiskLimits
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState

ZERO = Decimal("0")
BPS = Decimal("10000")


def _number(value, *, positive=False):
    return isinstance(value, Decimal) and value.is_finite() and (value > 0 if positive else value >= 0)


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


@dataclass(frozen=True)
class MarginTier:
    max_notional_quote: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal

    def __post_init__(self):
        if (not _number(self.max_notional_quote, positive=True)
                or not _number(self.initial_margin_rate, positive=True)
                or not _number(self.maintenance_margin_rate, positive=True)
                or not self.maintenance_margin_rate <= self.initial_margin_rate <= 1):
            raise ValueError("CAPITAL_TIER_INVALID")


@dataclass(frozen=True)
class CapitalPolicy:
    account_uid: str
    account_mode: str
    collateral_currency: str
    position_mode: str
    leverage: int
    contract_value_life: Decimal
    lot_contracts: Decimal
    max_age_ms: int
    fee_rate: Decimal
    price_stress_bps: Decimal
    max_stress_loss_quote: Decimal
    limits: JointRiskLimits
    tiers: tuple[MarginTier, ...]

    def __post_init__(self):
        if (not isinstance(self.account_uid, str) or not self.account_uid.isascii() or not self.account_uid.isdecimal()
                or self.account_mode != "2" or self.collateral_currency != "USDT"
                or self.position_mode not in ("ONEWAY", "HEDGE")
                or type(self.leverage) is not int or self.leverage < 1
                or type(self.max_age_ms) is not int or self.max_age_ms <= 0
                or not _number(self.contract_value_life, positive=True) or not _number(self.lot_contracts, positive=True)
                or not _number(self.fee_rate) or self.fee_rate > 1
                or not _number(self.price_stress_bps) or self.price_stress_bps >= BPS
                or not _number(self.max_stress_loss_quote)
                or not isinstance(self.limits, JointRiskLimits) or not isinstance(self.tiers, tuple) or not self.tiers
                or any(not isinstance(t, MarginTier) for t in self.tiers)):
            raise ValueError("CAPITAL_POLICY_INVALID")
        for lower, upper in zip(self.tiers, self.tiers[1:]):
            if (lower.max_notional_quote >= upper.max_notional_quote
                    or lower.initial_margin_rate > upper.initial_margin_rate
                    or lower.maintenance_margin_rate > upper.maintenance_margin_rate):
                raise ValueError("CAPITAL_TIER_ORDER_INVALID")


@dataclass(frozen=True)
class CapitalClaim:
    intent_id: str
    wire_id: str
    account_uid: str
    session_id: str
    epoch: int
    config_version: int
    risk_epoch: int
    product: str
    side: str
    position_action: str
    quantity_base: Decimal
    price_usdt: Decimal
    leverage: int

    def __post_init__(self):
        if (any(not isinstance(v, str) or not v for v in (self.intent_id, self.wire_id, self.account_uid, self.session_id))
                or any(type(v) is not int or v < 1 for v in (
                    self.epoch, self.config_version, self.risk_epoch, self.leverage))
                or self.product not in ("SPOT", "SWAP") or self.side not in ("BUY", "SELL")
                or self.position_action not in ("OPEN", "CLOSE")
                or self.product == "SPOT" and (self.position_action != "OPEN" or self.leverage != 1)
                or not _number(self.quantity_base, positive=True) or not _number(self.price_usdt, positive=True)):
            raise ValueError("CAPITAL_CLAIM_INVALID")

    @classmethod
    def spot(cls, permit, side, account_uid):
        return cls(permit.intent_id, permit.client_order_id, account_uid, permit.session_id, permit.epoch,
                   permit.config_version, permit.risk_epoch, "SPOT", side, "OPEN",
                   permit.quantity_base, permit.price_usdt, 1)


@dataclass(frozen=True)
class CapitalSnapshot:
    account_uid: str
    account_mode: str
    collateral_currency: str
    position_mode: str
    margin_mode: str
    leverage: int
    observed_at_ms: int
    sequence: int
    scope_complete: bool
    account_source: str
    spot_life_base: Decimal
    spot_cash_quote: Decimal
    long_base: Decimal
    short_base: Decimal
    collateral_quote: Decimal
    initial_margin_quote: Decimal
    maintenance_margin_quote: Decimal
    funding_liability_quote: Decimal
    mark_price_usdt: Decimal
    index_price_usdt: Decimal
    mark_source: str
    index_source: str
    pending: tuple[CapitalClaim, ...]


@dataclass(frozen=True)
class CapitalDecision:
    allowed: bool
    reason_code: str
    net_delta_worst_base: Decimal = ZERO
    gross_worst_base: Decimal = ZERO
    basis_loss_quote: Decimal = ZERO
    initial_margin_quote: Decimal = ZERO
    maintenance_margin_quote: Decimal = ZERO
    initial_buffer_quote: Decimal = ZERO
    maintenance_buffer_quote: Decimal = ZERO
    spot_buy_hold_quote: Decimal = ZERO
    fees_quote: Decimal = ZERO
    directional_stress_quote: Decimal = ZERO
    stress_loss_quote: Decimal = ZERO


class SharedCapitalAuthority:
    def __init__(self, path: Path, *, policy: CapitalPolicy, observation, clock_ms, create: bool):
        if not isinstance(policy, CapitalPolicy) or not callable(observation) or not callable(clock_ms):
            raise ValueError("CAPITAL_AUTHORITY_INVALID")
        self.policy, self.observation, self.clock_ms = policy, observation, clock_ms
        self.journal = PolicyState(path, policy=_json(asdict(policy)),
                                   initial={"claims": {}, "last_checked_ms": None,
                                            "snapshot_sequence": None, "snapshot": None}, create=create)
        with self.journal.locked() as state:
            self._retained = self._decode(state)

    def _decode(self, state):
        if set(state) != {"claims", "last_checked_ms", "snapshot_sequence", "snapshot"}:
            raise ValueError("CAPITAL_JOURNAL_INVALID")
        for field in ("last_checked_ms", "snapshot_sequence"):
            if state[field] is not None and (type(state[field]) is not int or state[field] < 0):
                raise ValueError("CAPITAL_JOURNAL_INVALID")
        raw_snapshot = state["snapshot"]
        if (raw_snapshot is None) != (state["snapshot_sequence"] is None):
            raise ValueError("CAPITAL_JOURNAL_INVALID")
        if raw_snapshot is not None:
            values = dict(raw_snapshot)
            for field in ("spot_life_base", "spot_cash_quote", "long_base", "short_base", "collateral_quote",
                          "initial_margin_quote", "maintenance_margin_quote", "funding_liability_quote",
                          "mark_price_usdt", "index_price_usdt"):
                if not isinstance(values[field], str):
                    raise ValueError("CAPITAL_JOURNAL_INVALID")
                values[field] = Decimal(values[field])
            values["pending"] = tuple(self._read_claim(c) for c in values["pending"])
            obs = CapitalSnapshot(**values)
            self._validate_snapshot(obs, obs.observed_at_ms)
            if (state["last_checked_ms"] is None or state["last_checked_ms"] < obs.observed_at_ms
                    or state["snapshot_sequence"] != obs.sequence or _json(asdict(obs)) != raw_snapshot):
                raise ValueError("CAPITAL_JOURNAL_INVALID")
        claims = {}
        for key, raw in state["claims"].items():
            claim = self._read_claim(raw)
            if key != claim.intent_id:
                raise ValueError("CAPITAL_JOURNAL_INVALID")
            claims[key] = claim
        if len({c.wire_id for c in claims.values()}) != len(claims):
            raise ValueError("CAPITAL_WIRE_ID_DUPLICATE")
        if claims and raw_snapshot is None:
            raise ValueError("CAPITAL_JOURNAL_INVALID")
        return claims

    def _read_claim(self, raw):
        values = dict(raw)
        for field in ("quantity_base", "price_usdt"):
            if not isinstance(values[field], str):
                raise ValueError("CAPITAL_JOURNAL_INVALID")
            values[field] = Decimal(values[field])
        claim = CapitalClaim(**values)
        self._validate_claim(claim)
        return claim

    def retained_claim_ids(self):
        """Conservative diagnostic even when a failed checkpoint makes disk uncertain."""
        return tuple(sorted(self._retained))

    def claim_ids(self):
        with self.journal.locked() as state:
            return tuple(sorted(self._decode(state)))

    def _validate_claim(self, claim):
        if (not isinstance(claim, CapitalClaim) or claim.account_uid != self.policy.account_uid
                or claim.product == "SWAP" and (
                    claim.leverage != self.policy.leverage
                    or claim.quantity_base % (self.policy.contract_value_life * self.policy.lot_contracts) != 0)):
            raise ValueError("CAPITAL_CLAIM_SCOPE_INVALID")

    def _validate_snapshot(self, obs, now):
        p = self.policy
        if (not isinstance(obs, CapitalSnapshot) or obs.account_uid != p.account_uid
                or obs.collateral_currency != p.collateral_currency
                or obs.account_mode != p.account_mode or obs.position_mode != p.position_mode
                or obs.margin_mode != "cross" or type(obs.leverage) is not int or obs.leverage != p.leverage
                or obs.scope_complete is not True or obs.account_source != "okx_account"
                or obs.mark_source != "okx_mark" or obs.index_source != "okx_index"
                or type(obs.observed_at_ms) is not int or not 0 <= now - obs.observed_at_ms <= p.max_age_ms
                or type(obs.sequence) is not int or obs.sequence < 1
                or not isinstance(obs.pending, tuple)
                or not all(_number(v) for v in (
                    obs.spot_life_base, obs.spot_cash_quote, obs.long_base, obs.short_base,
                    obs.collateral_quote, obs.initial_margin_quote, obs.maintenance_margin_quote,
                    obs.funding_liability_quote))
                or not _number(obs.mark_price_usdt, positive=True) or not _number(obs.index_price_usdt, positive=True)
                or p.position_mode == "ONEWAY" and obs.long_base > 0 and obs.short_base > 0):
            raise ValueError("CAPITAL_SNAPSHOT_UNQUALIFIED")
        for c in obs.pending:
            self._validate_claim(c)
        if len({c.intent_id for c in obs.pending}) != len(obs.pending):
            raise ValueError("CAPITAL_PENDING_DUPLICATE")

    def _snapshot(self, state, now):
        obs = self.observation()
        self._validate_snapshot(obs, now)
        encoded = _json(asdict(obs))
        sequence = state["snapshot_sequence"]
        if sequence is not None and (obs.sequence < sequence or obs.sequence == sequence and encoded != state["snapshot"]):
            raise ValueError("CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION")
        if state["snapshot"] is not None and obs.observed_at_ms < state["snapshot"]["observed_at_ms"]:
            raise ValueError("CAPITAL_SNAPSHOT_CLOCK_ROLLBACK")
        state["snapshot_sequence"], state["snapshot"] = obs.sequence, encoded
        return obs

    def _margins(self, base, price):
        notional = base * price
        if notional == 0:
            return ZERO, ZERO
        for tier in self.policy.tiers:
            if notional <= tier.max_notional_quote:
                return (notional * max(tier.initial_margin_rate, Decimal(1) / self.policy.leverage),
                        notional * tier.maintenance_margin_rate)
        raise ValueError("CAPITAL_TIER_UNCOVERED")

    def _evaluate(self, obs, claims):
        orders = dict(claims)
        if len({c.intent_id for c in obs.pending}) != len(obs.pending):
            raise ValueError("CAPITAL_PENDING_DUPLICATE")
        for external in obs.pending:
            self._validate_claim(external)
            if external.intent_id in orders and orders[external.intent_id] != external:
                raise ValueError("CAPITAL_PENDING_IDENTITY_MISMATCH")
            orders[external.intent_id] = external
        if len({c.wire_id for c in orders.values()}) != len(orders):
            raise ValueError("CAPITAL_WIRE_ID_DUPLICATE")
        totals = {key: ZERO for key in ("buy", "sell", "long", "short", "close_long", "close_short")}
        spend = fees = ZERO
        price = max(obs.mark_price_usdt, obs.index_price_usdt,
                    *(c.price_usdt for c in orders.values()))
        for c in orders.values():
            fees += c.quantity_base * max(c.price_usdt, price) * self.policy.fee_rate
            if c.product == "SPOT":
                totals["buy" if c.side == "BUY" else "sell"] += c.quantity_base
                if c.side == "BUY":
                    spend += c.quantity_base * c.price_usdt
            else:
                key = ("long" if c.side == "BUY" else "short") if c.position_action == "OPEN" else (
                    "close_long" if c.side == "SELL" else "close_short")
                totals[key] += c.quantity_base
        if totals["sell"] > obs.spot_life_base:
            raise ValueError("CAPITAL_SPOT_INVENTORY_UNAVAILABLE")
        if spend + fees > obs.spot_cash_quote:
            raise ValueError("CAPITAL_CASH_UNAVAILABLE")
        if totals["close_long"] > obs.long_base or totals["close_short"] > obs.short_base:
            raise ValueError("CAPITAL_CLOSE_EXCEEDS_POSITION")
        spots = (obs.spot_life_base - totals["sell"], obs.spot_life_base + totals["buy"])
        longs = (obs.long_base - totals["close_long"], obs.long_base + totals["long"])
        shorts = (obs.short_base - totals["close_short"], obs.short_base + totals["short"])
        scenarios = tuple(product(spots, longs, shorts))
        delta = max(abs(s + long - short) for s, long, short in scenarios)
        gross = max(s + long + short for s, long, short in scenarios)
        perpetual = max(longs) + max(shorts)
        stressed_price = price * (1 + self.policy.price_stress_bps / BPS)
        basis = perpetual * (stressed_price * self.policy.limits.adverse_basis_bps / BPS
                             + abs(obs.mark_price_usdt - obs.index_price_usdt))
        directional = delta * price * self.policy.price_stress_bps / BPS
        actual_im, actual_mm = self._margins(obs.long_base + obs.short_base,
                                             max(obs.mark_price_usdt, obs.index_price_usdt))
        future_im, future_mm = self._margins(perpetual, stressed_price)
        im = max(obs.initial_margin_quote, actual_im) + max(ZERO, future_im - actual_im)
        mm = max(obs.maintenance_margin_quote, actual_mm) + max(ZERO, future_mm - actual_mm)
        available = obs.collateral_quote - spend - fees - basis - directional - obs.funding_liability_quote
        stress = fees + basis + directional + obs.funding_liability_quote
        values = dict(net_delta_worst_base=delta, gross_worst_base=gross, basis_loss_quote=basis,
                      initial_margin_quote=im, maintenance_margin_quote=mm,
                      initial_buffer_quote=available - im, maintenance_buffer_quote=available - mm,
                      spot_buy_hold_quote=spend, fees_quote=fees, directional_stress_quote=directional,
                      stress_loss_quote=stress)
        limits = self.policy.limits
        for failed, reason in (
                (delta > limits.max_abs_delta_base, "CAPITAL_DELTA_LIMIT"),
                (gross > limits.max_gross_base, "CAPITAL_GROSS_LIMIT"),
                (basis > limits.max_basis_loss_quote, "CAPITAL_BASIS_LIMIT"),
                (obs.funding_liability_quote > limits.max_funding_quote, "CAPITAL_FUNDING_LIMIT"),
                (stress > self.policy.max_stress_loss_quote, "CAPITAL_STRESS_LOSS_LIMIT"),
                (available - im < limits.min_margin_buffer_quote, "CAPITAL_INITIAL_MARGIN_LOW"),
                (available - mm < limits.min_margin_buffer_quote, "CAPITAL_MAINTENANCE_MARGIN_LOW")):
            if failed:
                return CapitalDecision(False, reason, **values)
        return CapitalDecision(True, "CAPITAL_READY", **values)

    def _operate(self, claim=None, *, reserve=False, spot_balances=None, swap_account=None):
        try:
            if _json(asdict(self.policy)) != self.journal.policy:
                raise ValueError("CAPITAL_POLICY_CHANGED")
            with self.journal.locked() as state:
                claims = self._decode(state)
                self._retained = claims
                now = self.clock_ms()
                if (type(now) is not int or now < 0
                        or state["last_checked_ms"] is not None and now < state["last_checked_ms"]):
                    raise ValueError("CAPITAL_CLOCK_ROLLBACK")
                state["last_checked_ms"] = now
                try:
                    obs = self._snapshot(state, now)
                    if spot_balances is not None and spot_balances != (obs.spot_life_base, obs.spot_cash_quote):
                        raise ValueError("CAPITAL_SPOT_BALANCE_MISMATCH")
                    if swap_account is not None:
                        a = swap_account
                        if (a.account_uid != obs.account_uid or a.position_mode.name != obs.position_mode
                                or a.margin_mode != obs.margin_mode or a.leverage != obs.leverage
                                or a.account_mode != obs.account_mode or a.connector_ready is not True
                                or type(a.snapshot_sequence) is not int or a.snapshot_sequence != obs.sequence
                                or type(a.observed_at_ms) is not int
                                or not 0 <= now - a.observed_at_ms <= self.policy.max_age_ms):
                            raise ValueError("CAPITAL_SWAP_SNAPSHOT_MISMATCH")
                        ct = self.policy.contract_value_life
                        long = max(a.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else a.long_contracts
                        short = max(-a.net_contracts, ZERO) if obs.position_mode == "ONEWAY" else a.short_contracts
                        if (long * ct, short * ct) != (obs.long_base, obs.short_base):
                            raise ValueError("CAPITAL_SWAP_POSITION_MISMATCH")
                    if claim is not None:
                        self._validate_claim(claim)
                        if claim.intent_id in claims and claims[claim.intent_id] != claim:
                            raise ValueError("CAPITAL_CLAIM_CHANGED")
                        if not reserve and claim.intent_id not in claims:
                            raise ValueError("CAPITAL_CLAIM_NOT_RESERVED")
                        if reserve and claim.intent_id not in claims and any(c.intent_id == claim.intent_id for c in obs.pending):
                            raise ValueError("CAPITAL_UNOWNED_PENDING_ID")
                        if reserve and any((c.session_id, c.epoch) != (claim.session_id, claim.epoch)
                                           for c in (*claims.values(), *obs.pending)):
                            raise ValueError("CAPITAL_OLD_SESSION_CLAIMS")
                        claims = {**claims, claim.intent_id: claim}
                    decision = self._evaluate(obs, claims)
                    if decision.allowed and reserve:
                        state["claims"] = {key: _json(asdict(c)) for key, c in claims.items()}
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("CAPITAL_") else "CAPITAL_OBSERVATION_UNAVAILABLE"
                    decision = CapitalDecision(False, reason)
                if decision.allowed and reserve:
                    self._retained = claims
                self.journal.commit(state)
                self._retained = self._decode(state)
                return decision
        except Exception:
            return CapitalDecision(False, "CAPITAL_STATE_UNAVAILABLE")

    def check(self):
        return self._operate()

    def reserve(self, claim: CapitalClaim):
        return self._operate(claim, reserve=True)

    def authorize(self, claim: CapitalClaim):
        return self._operate(claim)

    def spot(self, permit, side, balances, *, reserve=False):
        try:
            claim = CapitalClaim.spot(permit, side, self.policy.account_uid)
            return self._operate(claim, reserve=reserve, spot_balances=balances)
        except Exception:
            return CapitalDecision(False, "CAPITAL_SPOT_INVALID")

    def swap(self, permit, account, *, reserve=False):
        try:
            if (permit.account_uid != self.policy.account_uid or permit.instrument != "LIFE-USDT-SWAP"
                    or permit.position_mode.name != self.policy.position_mode
                    or permit.contract_value_life != self.policy.contract_value_life
                    or permit.contracts * permit.contract_value_life != permit.quantity_base):
                raise ValueError("CAPITAL_SWAP_CONTRACT_MISMATCH")
            claim = CapitalClaim(permit.intent_id, permit.client_order_id, permit.account_uid,
                                 permit.session_id, permit.epoch, permit.config_version, permit.risk_epoch,
                                 "SWAP", permit.side.name, permit.position_action.name,
                                 permit.quantity_base, permit.price_usdt, permit.leverage)
            return self._operate(claim, reserve=reserve, swap_account=account)
        except Exception:
            return CapitalDecision(False, "CAPITAL_SWAP_INVALID")
