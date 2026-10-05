"""P3 reference prices are LIFE/USDT valuations with explicit evidence."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.reference import (
    BenchmarkComponent,
    CorrelationEvidence,
    LifeMarketEvidence,
    ReferenceEngine,
    ReferencePolicy,
)

POLICY = ReferencePolicy(max_age_ms=2000, min_life_depth_base=Decimal("10"),
                         min_source_depth_quote=Decimal("1000"),
                         max_deviation_bps=Decimal("500"), max_influence=Decimal("0.5"),
                         min_correlation=Decimal("0.2"),
                         min_correlation_samples=3)

CORRELATION = CorrelationEvidence(Decimal("0.8"), 5, 1000)


def life(*, mid="1", bid_depth="100", ask_depth="100", own_separated=True, ts=1000):
    return LifeMarketEvidence(mid_usdt=Decimal(mid), bid_depth_base=Decimal(bid_depth),
                              ask_depth_base=Decimal(ask_depth), own_depth_separated=own_separated,
                              exchange_timestamp_ms=ts, source="okx:LIFE-USDT")


def btc(*, current="110000", anchor="100000", depth="100000", quote="USDT", fx=None, ts=1000):
    return BenchmarkComponent(source_id="okx:BTC-USDT", weight=Decimal("1"),
                              current_price=Decimal(current), anchor_price_usdt=Decimal(anchor),
                              quote_currency=quote, quote_to_usdt=fx,
                              independent_depth_quote=Decimal(depth), exchange_timestamp_ms=ts)


def engine():
    return ReferenceEngine(POLICY, life_anchor_usdt=Decimal("1"), model_version="relative-return-v1",
                           life_source_id="okx:LIFE-USDT",
                           source_anchors_usdt={"okx:BTC-USDT": Decimal("100000"),
                                                "other:AVAX-BTC": Decimal("50")})


def research(**kwargs):
    return engine().evaluate("bounded_benchmark", execution_mode="simulation", **kwargs)


def test_absolute_btc_price_cannot_become_life_price():
    result = research(market=life(mid="1.02"),
                      components=(btc(),), influence=Decimal("0.5"),
                      exchange_now_ms=1100, correlation=CORRELATION)
    assert result.price_usdt == Decimal("1.06")
    assert result.benchmark_implied_usdt == Decimal("1.1")
    assert result.price_usdt < Decimal("2")
    assert result.reason_code == "BOUNDED_BENCHMARK_READY"


def test_basket_relative_returns_are_deterministic_and_currency_conversion_is_required():
    basket = (
        BenchmarkComponent("okx:BTC-USDT", Decimal("0.25"), Decimal("110000"),
                           Decimal("100000"), "USDT", None, Decimal("100000"), 1000),
        BenchmarkComponent("other:AVAX-BTC", Decimal("0.75"), Decimal("0.00055"),
                           Decimal("50"), "BTC", Decimal("100000"), Decimal("1000"), 1000),
    )
    first = research(market=life(mid="1.05"), components=basket,
                     influence=Decimal("0.5"), exchange_now_ms=1100,
                     correlation=CORRELATION)
    second = research(market=life(mid="1.05"), components=basket,
                      influence=Decimal("0.5"), exchange_now_ms=1100,
                      correlation=CORRELATION)
    assert first == second
    assert first.benchmark_implied_usdt == Decimal("1.1")
    missing_fx = basket[1].with_conversion(None)
    unavailable = research(market=life(mid="1.05"),
                           components=(basket[0], missing_fx), influence=Decimal("0.5"),
                           exchange_now_ms=1100, correlation=CORRELATION)
    assert unavailable.price_usdt is None
    assert unavailable.reason_code == "BENCHMARK_CONVERSION_UNAVAILABLE"
    assert unavailable.influence_applied == 0


def test_zero_influence_keeps_market_life_reference_unchanged():
    result = research(market=life(mid="1.03"), components=(btc(),),
                      influence=Decimal("0"), exchange_now_ms=1100,
                      correlation=None)
    assert result.price_usdt == Decimal("1.03")
    assert result.influence_applied == 0


def test_deviation_above_limit_blocks_benchmark_quoting_without_budget_adjustment():
    result = research(market=life(mid="1"), components=(btc(current="150000"),),
                      influence=Decimal("0.5"), exchange_now_ms=1100,
                      correlation=CORRELATION)
    assert result.price_usdt is None
    assert result.reason_code == "BENCHMARK_DEVIATION_EXCEEDED"


def test_market_requires_independent_two_sided_depth_not_own_only_or_tiny_trade():
    own_only = engine().evaluate("market", market=life(own_separated=False), exchange_now_ms=1100)
    empty = engine().evaluate("market", market=life(bid_depth="0", ask_depth="0"), exchange_now_ms=1100)
    assert own_only.price_usdt is None and own_only.reason_code == "OWN_DEPTH_UNSEPARABLE"
    assert empty.price_usdt is None and empty.reason_code == "LIFE_DEPTH_INSUFFICIENT"


def test_no_new_trades_keeps_qualified_book_reference_without_synthetic_fills():
    result = engine().evaluate("market", market=life(mid="1.02"), exchange_now_ms=1100)
    assert result.price_usdt == Decimal("1.02")
    assert result.reason_code == "LIFE_MARKET_READY"
    assert result.synthetic_fills == 0
    assert result.synthetic_volume_base == 0
    assert result.synthetic_candles == 0


def test_missing_life_market_can_only_use_labeled_simulation_anchor():
    live = engine().evaluate("market", market=None, exchange_now_ms=1100)
    simulation = engine().evaluate("bootstrap_simulation", market=None, exchange_now_ms=1100,
                                   execution_mode="simulation")
    shadow = engine().evaluate("bootstrap_simulation", market=None, exchange_now_ms=1100,
                               execution_mode="shadow")
    assert live.price_usdt is None and live.reason_code == "LIFE_MARKET_UNAVAILABLE"
    assert simulation.price_usdt == Decimal("1")
    assert simulation.reason_code == "HYPOTHETICAL_SIMULATION_ANCHOR"
    assert simulation.quality == "HYPOTHETICAL"
    assert shadow.price_usdt is None


def test_life_down_benchmark_up_or_source_liquidity_loss_disables_influence():
    divergence = research(market=life(mid="0.99"), components=(btc(),),
                          influence=Decimal("0.5"), exchange_now_ms=1100,
                          correlation=CORRELATION)
    thin_source = research(market=life(mid="1.02"),
                           components=(btc(depth="0"),), influence=Decimal("0.5"),
                           exchange_now_ms=1100, correlation=CORRELATION)
    broken_correlation = research(market=life(mid="1.02"),
                                  components=(btc(),), influence=Decimal("0.5"),
                                  exchange_now_ms=1100, correlation=CorrelationEvidence(Decimal("-0.2"), 5, 1000))
    assert divergence.price_usdt == Decimal("0.99")
    assert divergence.reason_code == "LIFE_BENCHMARK_DIVERGENCE"
    assert thin_source.price_usdt == Decimal("1.02")
    assert thin_source.reason_code == "BENCHMARK_LIQUIDITY_INSUFFICIENT"
    assert broken_correlation.price_usdt == Decimal("1.02")
    assert broken_correlation.reason_code == "BENCHMARK_CORRELATION_FAILED"


def test_benchmark_anchor_change_and_excess_influence_fail_closed():
    changed = research(market=life(mid="1.02"),
                       components=(btc(anchor="120000"),), influence=Decimal("0.5"),
                       exchange_now_ms=1100, correlation=CORRELATION)
    excess = research(market=life(mid="1.02"),
                      components=(btc(),), influence=Decimal("0.6"),
                      exchange_now_ms=1100, correlation=CORRELATION)
    assert changed.price_usdt is None and changed.reason_code == "BENCHMARK_ANCHOR_MISMATCH"
    assert excess.price_usdt is None and excess.reason_code == "BENCHMARK_INFLUENCE_INVALID"


def test_future_or_stale_correlation_cannot_authorize_benchmark_influence():
    future = research(market=life(mid="1.02"),
                      components=(btc(),), influence=Decimal("0.5"), exchange_now_ms=1100,
                      correlation=CorrelationEvidence(Decimal("0.8"), 5, 1200))
    stale = research(market=life(mid="1.02", ts=5000),
                     components=(btc(ts=5000),), influence=Decimal("0.5"),
                     exchange_now_ms=5100,
                     correlation=CorrelationEvidence(Decimal("0.8"), 5, 1000))
    assert future.price_usdt == Decimal("1.02") and future.influence_applied == 0
    assert future.reason_code == "BENCHMARK_CORRELATION_STALE"
    assert stale.price_usdt == Decimal("1.02") and stale.influence_applied == 0
    assert stale.reason_code == "BENCHMARK_CORRELATION_STALE"


def test_benchmark_model_is_research_only():
    result = engine().evaluate("bounded_benchmark", market=life(mid="1.02"),
                               components=(btc(),), influence=Decimal("0.5"),
                               exchange_now_ms=1100, correlation=CORRELATION, execution_mode="live")
    assert result.price_usdt is None and result.reason_code == "BENCHMARK_RESEARCH_ONLY"
    unspecified = engine().evaluate("bounded_benchmark", market=life(mid="1.02"),
                                    components=(btc(),), influence=Decimal("0.5"),
                                    exchange_now_ms=1100, correlation=CORRELATION)
    assert unspecified.price_usdt is None and unspecified.reason_code == "BENCHMARK_RESEARCH_ONLY"


def test_life_market_identity_and_self_referential_benchmark_are_rejected():
    wrong_market = LifeMarketEvidence(Decimal("1"), Decimal("100"), Decimal("100"),
                                      True, 1000, "other:LIFE-USDT")
    result = engine().evaluate("market", market=wrong_market, exchange_now_ms=1100)
    assert result.price_usdt is None and result.reason_code == "LIFE_SOURCE_MISMATCH"
    self_source = BenchmarkComponent("okx:LIFE-USDT", Decimal("1"), Decimal("1"),
                                     Decimal("1"), "USDT", None, Decimal("1000"), 1000)
    self_result = research(market=life(mid="1.02"),
                           components=(self_source,), influence=Decimal("0.5"),
                           exchange_now_ms=1100, correlation=CORRELATION)
    assert self_result.price_usdt is None and self_result.reason_code == "BENCHMARK_SELF_REFERENCE"
