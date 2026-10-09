"""Advisory, bounded LIFE perpetual hedge sizing from actual spot exposure."""

from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.joint_exposure import LinearLifeContractSpec


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _positive(value: Decimal) -> bool:
    return _nonnegative(value) and value > 0


@dataclass(frozen=True)
class HedgePolicy:
    life_swap_instrument: str
    deadband_base: Decimal
    batch_base: Decimal
    max_child_base: Decimal
    max_unhedged_base: Decimal
    max_unhedged_ms: int
    max_retry_attempts: int
    max_hedge_cost_quote: Decimal
    max_basis_bps: Decimal
    maker_fee_rate: Decimal

    def __post_init__(self):
        if (not isinstance(self.life_swap_instrument, str)
                or not self.life_swap_instrument.startswith("LIFE-")
                or not self.life_swap_instrument.endswith("-SWAP")
                or not all(_nonnegative(value) for value in (
                    self.deadband_base, self.max_unhedged_base,
                    self.max_hedge_cost_quote, self.max_basis_bps,
                    self.maker_fee_rate))
                or not all(_positive(value) for value in (
                    self.batch_base, self.max_child_base))
                or self.batch_base > self.max_child_base
                or not isinstance(self.max_unhedged_ms, int)
                or isinstance(self.max_unhedged_ms, bool) or self.max_unhedged_ms <= 0
                or not isinstance(self.max_retry_attempts, int)
                or isinstance(self.max_retry_attempts, bool)
                or self.max_retry_attempts <= 0):
            raise ValueError("HEDGE_POLICY_INVALID")


@dataclass(frozen=True)
class HedgeObservation:
    spot_life_base: Decimal
    target_inventory_base: Decimal
    perp_contracts_signed: Decimal
    pending_hedge_contracts_signed: Decimal
    unresolved_spot_buy_base: Decimal
    unresolved_spot_sell_base: Decimal
    swap_instrument: str
    connector_ready: bool
    mark_price_usdt: Decimal
    index_price_usdt: Decimal
    executable_price_usdt: Decimal
    mark_source: str
    index_source: str
    executable_depth_base: Decimal
    estimated_impact_quote: Decimal
    unhedged_since_ms: int
    now_ms: int
    retry_attempts: int


@dataclass(frozen=True)
class HedgeDecision:
    reason_code: str
    side: str | None
    contracts: Decimal
    base_quantity: Decimal
    allow_spot_risk_increase: bool
    estimated_cost_quote: Decimal


def plan_life_hedge(policy: HedgePolicy, observation: HedgeObservation,
                    contract: LinearLifeContractSpec) -> HedgeDecision:
    zero = Decimal("0")

    def blocked(reason: str, *, spot_allowed: bool = False) -> HedgeDecision:
        return HedgeDecision(reason, None, zero, zero, spot_allowed, zero)

    if (not isinstance(policy, HedgePolicy)
            or not isinstance(observation, HedgeObservation)
            or not isinstance(contract, LinearLifeContractSpec)
            or not all(_nonnegative(value) for value in (
                observation.spot_life_base, observation.target_inventory_base,
                observation.unresolved_spot_buy_base,
                observation.unresolved_spot_sell_base,
                observation.executable_depth_base,
                observation.estimated_impact_quote))
            or not all(_positive(value) for value in (
                observation.mark_price_usdt, observation.index_price_usdt,
                observation.executable_price_usdt))
            or not isinstance(observation.perp_contracts_signed, Decimal)
            or not observation.perp_contracts_signed.is_finite()
            or not isinstance(observation.pending_hedge_contracts_signed, Decimal)
            or not observation.pending_hedge_contracts_signed.is_finite()
            or not isinstance(observation.connector_ready, bool)
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (
                observation.unhedged_since_ms, observation.now_ms,
                observation.retry_attempts))
            or observation.now_ms < observation.unhedged_since_ms):
        return blocked("HEDGE_INPUT_INVALID")
    if observation.swap_instrument != policy.life_swap_instrument:
        return blocked("HEDGE_INSTRUMENT_MISMATCH")
    if observation.mark_source != "okx_mark" or observation.index_source != "okx_index":
        return blocked("HEDGE_PRICE_SOURCE_UNQUALIFIED")
    if (observation.unresolved_spot_buy_base > 0
            or observation.unresolved_spot_sell_base > 0):
        return blocked("SPOT_INTENT_UNRESOLVED")
    if observation.pending_hedge_contracts_signed != 0:
        return blocked("HEDGE_IN_FLIGHT")
    residual = (observation.spot_life_base - observation.target_inventory_base
                + contract.base_from_contracts(observation.perp_contracts_signed))
    age_ms = observation.now_ms - observation.unhedged_since_ms
    urgent = abs(residual) > policy.max_unhedged_base or age_ms > policy.max_unhedged_ms
    if abs(residual) <= policy.deadband_base and not urgent:
        return blocked("HEDGE_DEADBAND", spot_allowed=True)
    if not observation.connector_ready:
        return blocked("HEDGE_CONNECTOR_UNAVAILABLE")
    if observation.retry_attempts >= policy.max_retry_attempts:
        return blocked("HEDGE_RETRY_CAP")
    basis_bps = (abs(observation.mark_price_usdt - observation.index_price_usdt)
                 / observation.index_price_usdt * Decimal("10000"))
    if basis_bps > policy.max_basis_bps:
        return blocked("HEDGE_BASIS_LIMIT")
    target_base = min(abs(residual), policy.max_child_base,
                      observation.executable_depth_base)
    contracts = contract.contracts_for_base(target_base)
    base = contract.base_from_contracts(contracts)
    if base < policy.batch_base:
        return blocked("HEDGE_DEPTH_INSUFFICIENT")
    side = "SELL" if residual > 0 else "BUY"
    adverse_price = ((observation.mark_price_usdt - observation.executable_price_usdt)
                     if side == "SELL" else
                     (observation.executable_price_usdt - observation.mark_price_usdt))
    cost = (max(zero, adverse_price) * base
            + observation.estimated_impact_quote
            + policy.maker_fee_rate * observation.executable_price_usdt * base)
    if cost > policy.max_hedge_cost_quote:
        return blocked("HEDGE_COST_LIMIT")
    return HedgeDecision("HEDGE_URGENT" if urgent else "HEDGE_INTENT_READY",
                         side, contracts, base, False, cost)
