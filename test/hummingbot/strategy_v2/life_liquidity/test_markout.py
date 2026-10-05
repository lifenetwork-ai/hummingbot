"""P4.9: independent, time-ordered markouts by side, size, and horizon."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.markout import MarkoutTracker


def test_alternating_buy_sell_fills_can_both_have_adverse_markouts():
    tracker = MarkoutTracker(horizons_ms=(1000, 30000), min_samples=1,
                             size_bucket_edges=(Decimal("10"),))
    tracker.record_fill("b1", "BUY", Decimal("1"), Decimal("1"), fill_at_ms=1000)
    tracker.record_fill("s1", "SELL", Decimal("1"), Decimal("1"), fill_at_ms=2000)
    assert tracker.observe("b1", 1000, Decimal("0.9"), observed_at_ms=2000,
                           source_kind="independent_market")
    assert tracker.observe("s1", 1000, Decimal("1.1"), observed_at_ms=3000,
                           source_kind="independent_market")
    buy = tracker.summary("BUY", "small", 1000, now_ms=3000)
    sell = tracker.summary("SELL", "small", 1000, now_ms=3000)
    assert buy.mean_markout_quote == Decimal("-0.1")
    assert sell.mean_markout_quote == Decimal("-0.1")
    assert tracker.summary("BUY", "small", 30000, now_ms=3000).reason_code == "INSUFFICIENT_SAMPLES"


def test_future_or_model_based_observations_cannot_be_used_for_current_decision():
    tracker = MarkoutTracker(horizons_ms=(1000,), min_samples=2,
                             size_bucket_edges=(Decimal("10"),))
    tracker.record_fill("b1", "BUY", Decimal("1"), Decimal("1"), fill_at_ms=1000)
    assert not tracker.observe("b1", 1000, Decimal("0.9"), observed_at_ms=1500,
                               source_kind="independent_market")
    assert not tracker.observe("b1", 1000, Decimal("0.9"), observed_at_ms=2000,
                               source_kind="benchmark_model")
    assert tracker.observe("b1", 1000, Decimal("0.9"), observed_at_ms=2000,
                           source_kind="independent_market")
    assert tracker.summary("BUY", "small", 1000, now_ms=1999).reason_code == "INSUFFICIENT_SAMPLES"
    assert tracker.summary("BUY", "small", 1000, now_ms=2000).reason_code == "INSUFFICIENT_SAMPLES"
