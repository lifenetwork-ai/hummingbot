"""Synthetic P5.13 book replay; no exchange feed or live ownership proof."""

from dataclasses import replace
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.market_data import MarketSnapshot
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnBookOrder, separate_own_depth
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine, ReferencePolicy


def D(value):
    return Decimal(value)


def book(bids=None, asks=None):
    bids = bids or [["1.00", "10"], ["0.99", "6"]]
    asks = asks or [["1.10", "10"], ["1.11", "6"]]
    return MarketSnapshot.from_okx(
        {"code": "0", "data": [{"ts": "1000", "seqId": 42,
                                "bids": bids, "asks": asks}]},
        "LIFE-USDT", 2.0, "live", 100)


def own(wire, side, price, size, *, state="OPEN", exchange_id=None):
    return OwnBookOrder(wire_id=wire, exchange_order_id=exchange_id or f"ex-{wire}",
                        instrument="LIFE-USDT", side=side,
                        price_usdt=D(price), remaining_base=D(size), state=state)


def separate(snapshot=None, orders=(), **changes):
    values = dict(book=snapshot or book(), own_orders=orders,
                  order_scope_complete=True, orders_observed_monotonic=2.01,
                  max_observation_skew_ms=100, max_book_age_ms=500,
                  max_depth_distance_bps=D("200"))
    values.update(changes)
    return separate_own_depth(**values)


def engine():
    policy = ReferencePolicy(
        max_age_ms=500, min_life_depth_base=D("10"),
        min_source_depth_quote=D("100"), max_deviation_bps=D("500"),
        max_influence=D("0.5"), min_correlation=D("0.2"),
        min_correlation_samples=3)
    return ReferenceEngine(policy, life_anchor_usdt=D("1"),
                           model_version="test-v1", life_source_id="okx:LIFE-USDT",
                           source_anchors_usdt={})


def test_own_top_levels_are_removed_before_mid_and_depth_qualification():
    result = separate(orders=(own("b1", "BUY", "1.00", "10"),
                              own("a1", "SELL", "1.10", "10", state="CANCEL_PENDING")))
    assert result.reason_code == "OWN_DEPTH_SEPARATED"
    assert result.evidence.mid_usdt == D("1.05")
    assert result.evidence.bid_depth_base == D("6")
    assert result.evidence.ask_depth_base == D("6")
    decision = engine().evaluate("market", market=result.evidence, exchange_now_ms=1100)
    assert decision.price_usdt is None
    assert decision.reason_code == "LIFE_DEPTH_INSUFFICIENT"


def test_partial_own_quantity_is_subtracted_from_aggregated_level():
    result = separate(orders=(own("b1", "BUY", "1.00", "4"),))
    assert result.evidence.bid_depth_base == D("12")
    assert result.evidence.ask_depth_base == D("16")
    assert result.evidence.mid_usdt == D("1.05")
    decision = engine().evaluate("market", market=result.evidence, exchange_now_ms=1100)
    assert decision.price_usdt == D("1.05")


def test_own_only_book_cannot_qualify_reference():
    snapshot = book(bids=[["1.00", "10"]], asks=[["1.10", "10"]])
    result = separate(snapshot, (own("b1", "BUY", "1.00", "10"),
                                 own("a1", "SELL", "1.10", "10")))
    assert result.evidence is None
    assert result.reason_code == "INDEPENDENT_BOOK_SIDE_EMPTY"


def test_incomplete_or_stale_order_scope_cannot_be_treated_as_zero_own_depth():
    incomplete = separate(order_scope_complete=False)
    stale = separate(orders_observed_monotonic=1.0)
    old_book = separate(snapshot=book(), max_book_age_ms=50)
    assert incomplete.evidence is None and incomplete.reason_code == "OWN_ORDER_SCOPE_INCOMPLETE"
    assert stale.evidence is None and stale.reason_code == "OWN_ORDER_SNAPSHOT_STALE"
    assert old_book.evidence is None and old_book.reason_code == "BOOK_SNAPSHOT_STALE"


def test_unresolved_identity_or_state_cannot_qualify_reference():
    cases = (
        (own("b1", "BUY", "1.00", "4", state="SEND_UNKNOWN"),),
        (own("b1", "BUY", "1.00", "4"), own("b1", "BUY", "1.00", "1")),
        (own("b1", "BUY", "1.00", "11"),),
        (own("b1", "BUY", "0.98", "1"),),
    )
    for orders in cases:
        result = separate(orders=orders)
        assert result.evidence is None
        assert result.reason_code in ("OWN_ORDER_IDENTITY_UNAVAILABLE",
                                      "OWN_ORDER_BOOK_MISMATCH")


def test_far_away_depth_cannot_make_tiny_near_touch_book_eligible():
    snapshot = book(bids=[["1.00", "1"], ["0.50", "100"]],
                    asks=[["1.10", "1"], ["2.00", "100"]])
    result = separate(snapshot)
    assert result.evidence.bid_depth_base == D("1")
    assert result.evidence.ask_depth_base == D("1")
    assert engine().evaluate("market", market=result.evidence,
                             exchange_now_ms=1100).price_usdt is None


def test_bad_public_sequence_or_reused_exchange_id_fails_closed():
    no_sequence = separate(replace(book(), sequence_id=None))
    duplicate_exchange_id = separate(orders=(
        own("b1", "BUY", "1.00", "1", exchange_id="same"),
        own("b2", "BUY", "1.00", "1", exchange_id="same")))
    assert no_sequence.evidence is None
    assert no_sequence.reason_code == "BOOK_SNAPSHOT_UNAVAILABLE"
    assert duplicate_exchange_id.evidence is None
    assert duplicate_exchange_id.reason_code == "OWN_ORDER_IDENTITY_UNAVAILABLE"
