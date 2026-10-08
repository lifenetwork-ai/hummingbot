"""Synthetic P5.9 adaptation inputs must respect price, size, and risk limits."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_spot_quotes import D, ledger, plan

from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_quotes import AdaptiveQuotePolicy, AdaptiveQuoteSignals, QuoteCosts


def adaptive_policy(**changes):
    values = dict(max_spread_bps=D("100"), max_depth_fraction=D("0.5"),
                  target_inventory_base=D("10"), inventory_band_base=D("5"),
                  max_inventory_widen_bps=D("20"))
    values.update(changes)
    return AdaptiveQuotePolicy(**values)


def signals(**changes):
    values = dict(volatility_bps=D("15"), buy_markout_loss_bps=D("20"),
                  sell_markout_loss_bps=D("0"), buy_independent_depth_base=D("4"),
                  sell_independent_depth_base=D("4"))
    values.update(changes)
    return AdaptiveQuoteSignals(**values)


def test_fees_volatility_and_side_markout_widen_only_the_affected_quotes():
    result = plan((D("0.98"), D("1.02")),
                  rules=InstrumentRules(D("0.0001"), D("0.1"), D("0.1")),
                  costs=QuoteCosts(D("0.001"), D("0.001"), D("0"), D("0"), D("0"), D("0")),
                  adaptive_policy=adaptive_policy(), adaptive_signals=signals())

    assert [(item.side, item.price_usdt, item.quantity_base)
            for item in result.candidates] == [
                ("BUY", D("0.9945"), D("1.5")),
                ("SELL", D("1.0035"), D("1.5"))]


def test_inventory_pressure_shrinks_and_widens_only_risk_increasing_side():
    result = plan((D("0.98"), D("1.02")), reservations=ledger(life="15"),
                  quotes=QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("2"),)),
                  rules=InstrumentRules(D("0.0001"), D("0.1"), D("0.1")),
                  adaptive_policy=adaptive_policy(),
                  adaptive_signals=signals(volatility_bps=D("0"),
                                           buy_markout_loss_bps=D("0")))

    assert [(item.side, item.price_usdt, item.quantity_base)
            for item in result.candidates] == [
                ("BUY", D("0.995"), D("1")),
                ("SELL", D("1.003"), D("2"))]


def test_depth_budget_is_shared_across_levels_and_reports_kpi_shortfall():
    result = plan((D("0.98"), D("1.02")),
                  quotes=QuotesConfig(spreads_bps=(D("30"), D("40")),
                                      sizes_base=(D("1"), D("1"))),
                  rules=InstrumentRules(D("0.0001"), D("0.1"), D("0.1")),
                  adaptive_policy=adaptive_policy(),
                  adaptive_signals=signals(volatility_bps=D("0"),
                                           buy_markout_loss_bps=D("0"),
                                           buy_independent_depth_base=D("3"),
                                           sell_independent_depth_base=D("3")),
                  min_depth_base_per_side=D("2"))

    assert [(item.side, item.level, item.quantity_base) for item in result.candidates] == [
        ("BUY", 0, D("1")), ("SELL", 0, D("1")),
        ("BUY", 1, D("0.5")), ("SELL", 1, D("0.5"))]
    assert result.depth_target_met is False


def test_existing_unresolved_orders_consume_the_same_depth_budget():
    reservations = ledger()
    assert reservations.reserve(
        SpotIntent("existing", "BUY", D("1"), D("0.99"), "s1", 1),
        reference_price=D("1")).allowed
    result = plan((D("0.98"), D("1.02")), reservations=reservations,
                  quotes=QuotesConfig(spreads_bps=(D("40"),), sizes_base=(D("1"),)),
                  rules=InstrumentRules(D("0.0001"), D("0.1"), D("0.1")),
                  adaptive_policy=adaptive_policy(),
                  adaptive_signals=signals(volatility_bps=D("0"),
                                           buy_markout_loss_bps=D("0"),
                                           buy_independent_depth_base=D("3"),
                                           sell_independent_depth_base=D("3")),
                  sides=("BUY",))

    assert [(item.side, item.quantity_base) for item in result.candidates] == [
        ("BUY", D("0.5"))]


def test_spread_cap_rejects_unaffordable_side_even_with_service_subsidy():
    result = plan((D("0.98"), D("1.02")),
                  policy=EconomicPolicy("liquidity_service", D("0")),
                  subsidy_remaining_quote=D("100"),
                  rules=InstrumentRules(D("0.0001"), D("0.1"), D("0.1")),
                  adaptive_policy=adaptive_policy(max_spread_bps=D("40")),
                  adaptive_signals=signals(buy_markout_loss_bps=D("50")))

    assert [item.side for item in result.candidates] == ["SELL"]
    assert [(item.side, item.reason_code) for item in result.rejections] == [
        ("BUY", "ADAPTIVE_SPREAD_LIMIT")]


def test_outward_tick_rounding_cannot_exceed_the_spread_cap():
    result = plan((D("0.98"), D("1.02")),
                  adaptive_policy=adaptive_policy(max_spread_bps=D("40")),
                  adaptive_signals=signals(volatility_bps=D("0"),
                                           buy_markout_loss_bps=D("0")))

    assert result.candidates == ()
    assert [(item.side, item.reason_code) for item in result.rejections] == [
        ("BUY", "ADAPTIVE_SPREAD_LIMIT"),
        ("SELL", "ADAPTIVE_SPREAD_LIMIT")]


def test_missing_or_invalid_adaptive_inputs_fail_closed():
    missing = plan((D("0.98"), D("1.02")), adaptive_policy=adaptive_policy())
    assert missing.candidates == ()
    assert missing.reason_code == "QUOTE_ADAPTIVE_INPUT_UNAVAILABLE"
    invalid = plan((D("0.98"), D("1.02")), adaptive_policy=adaptive_policy(),
                   adaptive_signals=signals(buy_independent_depth_base=Decimal("NaN")))
    assert invalid.candidates == ()
    assert invalid.reason_code == "QUOTE_ADAPTIVE_INPUT_UNAVAILABLE"
