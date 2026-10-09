"""P4 capital drawdown uses NAV, not turnover or model self-valuation."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger


def D(value):
    return Decimal(value)


def test_cashflows_do_not_erase_capital_drawdown():
    ledger = CapitalLedger(opening_life=D("100"), opening_usdt=D("100"),
                           opening_independent_price_usdt=D("1"))
    ledger.record_fill("t1", "SELL", D("10"), D("0.9"), D("0"),
                       independent_value_usdt=D("1"))
    before = ledger.measure(D("1"), source_kind="independent_market")
    ledger.record_cashflow("deposit-1", D("100"))
    after = ledger.measure(D("1"), source_kind="independent_market")
    assert before.nav_quote == D("199")
    assert before.drawdown_bps == D("50")
    assert after.nav_quote == D("299")
    assert after.adjusted_nav_quote == D("199")
    assert after.drawdown_bps == D("50")
    assert after.execution_loss_quote == D("1")


def test_unqualified_or_benchmark_forecast_cannot_certify_capital():
    ledger = CapitalLedger(opening_life=D("100"), opening_usdt=D("100"),
                           opening_independent_price_usdt=D("1"))
    assert ledger.measure(None, source_kind="independent_market") is None
    assert ledger.measure(D("2"), source_kind="benchmark_model") is None


def test_starting_inventory_gain_does_not_hide_execution_loss():
    ledger = CapitalLedger(opening_life=D("100"), opening_usdt=D("100"),
                           opening_independent_price_usdt=D("1"))
    ledger.record_fill("t1", "SELL", D("10"), D("0.9"), D("0"),
                       independent_value_usdt=D("1"))
    rising = ledger.measure(D("1.2"), source_kind="independent_market")
    assert rising.adjusted_nav_quote > D("200")
    assert rising.starting_inventory_pnl_quote == D("18")
    assert rising.execution_loss_quote == D("1")


def test_funding_cost_reduces_nav_and_duplicate_event_is_idempotent():
    ledger = CapitalLedger(opening_life=D("100"), opening_usdt=D("100"),
                           opening_independent_price_usdt=D("1"))
    assert ledger.record_funding("funding-1", D("2"))
    assert not ledger.record_funding("funding-1", D("2"))
    assert ledger.measure(D("1"), source_kind="independent_market").nav_quote == D("198")


def test_100x_turnover_does_not_dilute_the_same_capital_loss():
    def replay(cycles: int, buy_price: Decimal):
        ledger = CapitalLedger(opening_life=D("100"), opening_usdt=D("1000"),
                               opening_independent_price_usdt=D("1"))
        for index in range(cycles):
            ledger.record_fill(f"buy-{index}", "BUY", D("1"), buy_price, D("0"),
                               independent_value_usdt=D("1"))
            ledger.record_fill(f"sell-{index}", "SELL", D("1"), D("1"), D("0"),
                               independent_value_usdt=D("1"))
        return ledger.measure(D("1"), source_kind="independent_market")

    low = replay(1, D("1.01"))
    high = replay(100, D("1.0001"))
    assert low.execution_loss_quote == high.execution_loss_quote == D("0.01")
    assert low.adjusted_nav_quote == high.adjusted_nav_quote == D("1099.99")
    assert low.drawdown_bps == high.drawdown_bps
