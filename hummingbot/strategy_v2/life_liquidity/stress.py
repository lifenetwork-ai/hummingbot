"""Conservative all-one-side fill stress, evaluated before a new quote."""

from dataclasses import dataclass
from decimal import Decimal


def _finite(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


@dataclass(frozen=True)
class StressInputs:
    pending_buy_base: Decimal
    pending_sell_base: Decimal
    current_inventory_base: Decimal
    reference_price_usdt: Decimal
    stressed_exit_price_usdt: Decimal
    stressed_high_price_usdt: Decimal
    buy_limit_price_usdt: Decimal
    sell_limit_price_usdt: Decimal
    basis_shock_quote: Decimal
    funding_shock_quote: Decimal
    hedge_outage: bool
    hedge_required: bool
    collateral_buffer_quote: Decimal
    max_stress_loss_quote: Decimal


@dataclass(frozen=True)
class StressDecision:
    allowed: bool
    reason_code: str
    worst_loss_quote: Decimal | None


def evaluate_stress(inputs: StressInputs) -> StressDecision:
    if (not all(_finite(value) for value in (
            inputs.pending_buy_base, inputs.pending_sell_base,
            inputs.basis_shock_quote, inputs.funding_shock_quote,
            inputs.collateral_buffer_quote, inputs.max_stress_loss_quote))
            or not _finite(inputs.current_inventory_base)
            or not all(_finite(value, positive=True) for value in (
                inputs.reference_price_usdt, inputs.stressed_exit_price_usdt,
                inputs.stressed_high_price_usdt,
                inputs.buy_limit_price_usdt, inputs.sell_limit_price_usdt))
            or inputs.stressed_high_price_usdt < inputs.reference_price_usdt
            or inputs.stressed_exit_price_usdt > inputs.reference_price_usdt
            or inputs.pending_sell_base > inputs.current_inventory_base):
        return StressDecision(False, "STRESS_INPUT_UNAVAILABLE", None)
    if inputs.hedge_required and inputs.hedge_outage and inputs.pending_buy_base > 0:
        return StressDecision(False, "HEDGE_UNAVAILABLE", None)
    opening_value = inputs.current_inventory_base * inputs.reference_price_usdt
    buy_scenario_value = ((inputs.current_inventory_base + inputs.pending_buy_base)
                          * inputs.stressed_exit_price_usdt
                          - inputs.pending_buy_base * inputs.buy_limit_price_usdt)
    sell_scenario_value = ((inputs.current_inventory_base - inputs.pending_sell_base)
                           * inputs.stressed_high_price_usdt
                           + inputs.pending_sell_base * inputs.sell_limit_price_usdt)
    sell_opening_value = inputs.current_inventory_base * inputs.stressed_high_price_usdt
    worst = (max(Decimal("0"), opening_value - buy_scenario_value,
                 sell_opening_value - sell_scenario_value)
             + inputs.basis_shock_quote + inputs.funding_shock_quote)
    if worst > inputs.max_stress_loss_quote:
        return StressDecision(False, "STRESS_LOSS_EXCEEDED", worst)
    if worst > inputs.collateral_buffer_quote:
        return StressDecision(False, "COLLATERAL_BUFFER_EXCEEDED", worst)
    return StressDecision(True, "STRESS_WITHIN_LIMITS", worst)
