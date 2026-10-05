from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.stress import StressInputs, evaluate_stress


def D(value):
    return Decimal(str(value))


def test_all_bids_fill_without_asks_and_depth_disappears():
    inputs = StressInputs(
        pending_buy_base=D(10), pending_sell_base=D(10), current_inventory_base=D(10),
        reference_price_usdt=D(1), stressed_exit_price_usdt=D("0.7"),
        stressed_high_price_usdt=D("1.1"),
        buy_limit_price_usdt=D(1), sell_limit_price_usdt=D(1),
        basis_shock_quote=D(0), funding_shock_quote=D(0),
        hedge_outage=False, hedge_required=False, collateral_buffer_quote=D(10),
        max_stress_loss_quote=D(5),
    )
    decision = evaluate_stress(inputs)
    assert decision.worst_loss_quote == D(6)
    assert decision.reason_code == "STRESS_LOSS_EXCEEDED"


def test_hedge_outage_blocks_new_exposure_even_with_small_price_shock():
    inputs = StressInputs(
        pending_buy_base=D(1), pending_sell_base=D(0), current_inventory_base=D(1),
        reference_price_usdt=D(1), stressed_exit_price_usdt=D("0.99"),
        stressed_high_price_usdt=D("1.01"),
        buy_limit_price_usdt=D(1), sell_limit_price_usdt=D(1),
        basis_shock_quote=D(0), funding_shock_quote=D(0),
        hedge_outage=True, hedge_required=True, collateral_buffer_quote=D(10),
        max_stress_loss_quote=D(5),
    )
    assert evaluate_stress(inputs).reason_code == "HEDGE_UNAVAILABLE"


def test_basis_funding_and_collateral_buffer_are_jointly_enforced():
    inputs = StressInputs(
        pending_buy_base=D(2), pending_sell_base=D(0), current_inventory_base=D(0),
        reference_price_usdt=D(1), stressed_exit_price_usdt=D("0.9"),
        stressed_high_price_usdt=D("1.1"),
        buy_limit_price_usdt=D(1), sell_limit_price_usdt=D(1),
        basis_shock_quote=D("0.3"), funding_shock_quote=D("0.1"),
        hedge_outage=False, hedge_required=True, collateral_buffer_quote=D("0.5"),
        max_stress_loss_quote=D(1),
    )
    decision = evaluate_stress(inputs)
    assert decision.worst_loss_quote == D("0.6")
    assert decision.reason_code == "COLLATERAL_BUFFER_EXCEEDED"


def test_invalid_shock_data_fails_closed():
    inputs = StressInputs(
        pending_buy_base=D(1), pending_sell_base=D(0), current_inventory_base=D(0),
        reference_price_usdt=D(1), stressed_exit_price_usdt=D(0),
        stressed_high_price_usdt=D("1.1"),
        buy_limit_price_usdt=D(1), sell_limit_price_usdt=D(1),
        basis_shock_quote=D(0), funding_shock_quote=D(0),
        hedge_outage=False, hedge_required=False, collateral_buffer_quote=D(1),
        max_stress_loss_quote=D(1),
    )
    assert evaluate_stress(inputs).reason_code == "STRESS_INPUT_UNAVAILABLE"


def test_all_asks_fill_before_rising_market():
    inputs = StressInputs(
        pending_buy_base=D(0), pending_sell_base=D(10), current_inventory_base=D(10),
        reference_price_usdt=D(1), stressed_exit_price_usdt=D("0.9"),
        stressed_high_price_usdt=D("1.3"),
        buy_limit_price_usdt=D(1), sell_limit_price_usdt=D(1),
        basis_shock_quote=D(0), funding_shock_quote=D(0),
        hedge_outage=False, hedge_required=False, collateral_buffer_quote=D(10),
        max_stress_loss_quote=D(2),
    )
    decision = evaluate_stress(inputs)
    assert decision.worst_loss_quote == D(3)
    assert decision.reason_code == "STRESS_LOSS_EXCEEDED"
