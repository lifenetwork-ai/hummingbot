"""P4 quote economics use final rounded orders and explicit quote-currency costs."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.economics import (
    EconomicInputs,
    EconomicPolicy,
    ExitInputs,
    evaluate_exit,
    evaluate_quote,
)


def D(value):
    return Decimal(value)


def inputs(**changes):
    values = dict(side="BUY", price_usdt=D("0.99"), quantity_base=D("10"),
                  value_usdt=D("1"), tick_size=D("0.01"), lot_size=D("1"),
                  min_size_base=D("1"), maker_fee_rate=D("0.001"),
                  exit_fee_rate=D("0.001"), impact_cost_quote=D("0"),
                  carry_cost_quote=D("0"), inventory_risk_quote=D("0"),
                  uncertainty_bps=D("0"), exit_value_includes_impact=False)
    values.update(changes)
    return EconomicInputs(**values)


def test_positive_spread_and_net_edge_have_explicit_quote_units():
    result = evaluate_quote(inputs(), EconomicPolicy("profit_mm", D("0")))
    assert result.final_price_usdt == D("0.99")
    assert result.gross_edge_quote == D("0.10")
    assert result.net_edge_quote == D("0.0801")


def test_negative_edge_requires_explicit_subsidy_capacity():
    expensive = inputs(maker_fee_rate=D("0.01"), exit_fee_rate=D("0.01"))
    profit = evaluate_quote(expensive, EconomicPolicy("profit_mm", D("0")))
    service = evaluate_quote(expensive, EconomicPolicy("liquidity_service", D("0")),
                             subsidy_remaining_quote=D("0.20"))
    exhausted = evaluate_quote(expensive, EconomicPolicy("liquidity_service", D("0")),
                               subsidy_remaining_quote=D("0.05"))
    assert profit.reason_code == "NET_EDGE_BELOW_MINIMUM" and not profit.allowed
    assert service.allowed and service.subsidy_reserved_quote > 0
    assert exhausted.reason_code == "SUBSIDY_BUDGET_EXCEEDED" and not exhausted.allowed


def test_rounding_minimum_size_and_impact_double_count_are_checked():
    rounded = evaluate_quote(inputs(price_usdt=D("0.995"), quantity_base=D("1.9")),
                             EconomicPolicy("profit_mm", D("0")))
    tiny = evaluate_quote(inputs(quantity_base=D("0.9")), EconomicPolicy("profit_mm", D("0")))
    double_impact = evaluate_quote(inputs(exit_value_includes_impact=True,
                                          impact_cost_quote=D("1")),
                                   EconomicPolicy("profit_mm", D("0")))
    assert rounded.final_price_usdt == D("0.99") and rounded.final_quantity_base == D("1")
    assert tiny.reason_code == "ORDER_BELOW_MINIMUM"
    assert double_impact.reason_code == "IMPACT_DOUBLE_COUNTED"


def test_100_bps_is_one_percent_and_sell_sign_is_correct():
    result = evaluate_quote(inputs(side="SELL", price_usdt=D("1.02"), value_usdt=D("1"),
                                   maker_fee_rate=D("0"), exit_fee_rate=D("0")),
                            EconomicPolicy("profit_mm", D("100")))
    assert result.net_edge_quote == D("0.20")
    assert result.net_edge_bps == D("200")
    assert result.allowed


def test_risk_reducing_exit_has_own_loss_and_slippage_limits():
    candidate = ExitInputs(position_base=D("10"), side="SELL", quantity_base=D("5"),
                           limit_price_usdt=D("0.98"), independent_value_usdt=D("1"),
                           max_slippage_bps=D("300"), remaining_exit_loss_quote=D("0.2"))
    allowed = evaluate_exit(candidate)
    assert allowed.allowed and allowed.expected_loss_quote == D("0.10")
    assert evaluate_exit(ExitInputs(**{**candidate.__dict__, "quantity_base": D("11")})).reason_code == (
        "EXIT_WOULD_REVERSE_POSITION")
    assert evaluate_exit(ExitInputs(**{**candidate.__dict__, "remaining_exit_loss_quote": D("0.05")})).reason_code == (
        "EXIT_LOSS_BUDGET_EXCEEDED")
    assert evaluate_exit(ExitInputs(**{**candidate.__dict__, "limit_price_usdt": D("0.90")})).reason_code == (
        "EXIT_SLIPPAGE_EXCEEDED")
