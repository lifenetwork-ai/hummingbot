from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.strategy_v2.utils.quote_guard import QuoteGuard, QuoteGuardConfig, SidewaysRange, observe_market


def policy(**updates):
    return QuoteGuardConfig(enabled=True, max_deviation_bps="200", resume_deviation_bps="100",
                            recovery_delay_seconds="2", **updates)


def book(bids=((99, 20),), asks=((101, 20),), timestamp=1000):
    result = OrderBook()
    result.apply_snapshot([OrderBookRow(p, q, timestamp) for p, q in bids], [OrderBookRow(p, q, timestamp) for p, q in asks], timestamp)
    return result


def order(identity="own", side=TradeType.BUY, price="99", amount="10", state=OrderState.OPEN,
          filled="0", exchange_id="exchange", updated_at=998):
    result = InFlightOrder(identity, "LIFE-USDT", OrderType.LIMIT_MAKER, side, Decimal(amount), 997,
                           Decimal(price), exchange_id, state)
    result.executed_amount_base = Decimal(filled)
    result.last_update_timestamp = updated_at
    return result


def observation(orders=(), bids=((99, 20),), asks=((101, 20),), timestamp=1000, now=1000, **limits):
    connector = SimpleNamespace(name="okx", in_flight_orders={o.client_order_id: o for o in orders},
                                _order_tracker=SimpleNamespace(cached_orders={}, lost_orders={}))
    return observe_market(connector, book(bids, asks, timestamp), "LIFE-USDT", now=now,
                          max_age_seconds=10, max_levels=limits.get("max_levels", 5), max_orders=limits.get("max_orders", 20))


def ready_guard(reference="100"):
    guard = QuoteGuard(policy())
    guard.evaluate(observation(), Decimal(reference), 0, sample=True)
    result = guard.evaluate(observation(timestamp=1002, now=1002), Decimal(reference), 2, sample=True)
    assert result["ready"]
    return guard


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_invalid_deviation_threshold_rejected(value):
    with pytest.raises(ValidationError):
        QuoteGuardConfig(enabled=True, max_deviation_bps=value, resume_deviation_bps="0")


@pytest.mark.parametrize("values", [{}, {"max_deviation_bps": "100"},
                                    {"max_deviation_bps": "100", "resume_deviation_bps": "100"},
                                    {"max_deviation_bps": "100", "resume_deviation_bps": "200"}])
def test_enabled_guard_requires_explicit_ordered_thresholds(values):
    with pytest.raises(ValidationError):
        QuoteGuardConfig(enabled=True, **values)


@pytest.mark.parametrize("reference,ready", [("102", True), ("102.0000001", False), ("98", True), ("97.9999999", False)])
def test_deviation_equality_is_allowed_exceedance_blocks(reference, ready):
    result = ready_guard().evaluate(observation(timestamp=1003, now=1003), Decimal(reference), 3)
    assert result["ready"] is ready
    if not ready:
        assert result["reason_code"] == "reference_deviation"


@pytest.mark.parametrize("bids,asks,detail", [([], [], "empty_or_filtered_depth"),
                                              ([(99, 10)], [], "empty_or_filtered_depth"),
                                              ([(99, 10)], [(101, 10)], "empty_or_filtered_depth"),
                                              ([(99, 9)], [(101, 10)], "inconsistent_own_depth")])
def test_empty_own_only_and_insufficient_own_depth_are_unavailable(bids, asks, detail):
    value = observation([order()], bids=bids, asks=asks)
    assert value.market_mid is None and value.detail == detail


def test_known_remainder_is_removed_and_traversal_is_bounded():
    value = observation([order()], bids=[(99, 10), (98, 20)])
    assert value.market_mid == Decimal("99.5")
    assert value.quality == "known_order_filtered"
    assert observation([order()], bids=[(99, 10), (98, 20)], max_levels=1).market_mid is None


@pytest.mark.parametrize("state,filled,exchange_id", [(OrderState.PENDING_CREATE, "0", None),
                                                      (OrderState.PENDING_CANCEL, "0", "exchange"), (OrderState.PARTIALLY_FILLED, "2", "exchange")])
def test_transition_or_partial_fill_uses_full_original_quantity_not_smaller_private_remainder(state, filled, exchange_id):
    own = order(state=state, filled=filled, exchange_id=exchange_id)
    assert observation([own], bids=[(99, 10)]).market_mid is None
    assert observation([own], bids=[(99, 10), (98, 20)]).market_mid == Decimal("99.5")
    assert observation([own], bids=[(99, 100)]).market_mid == Decimal("100")


def test_book_before_private_ack_masks_ambiguous_price_and_missing_competitive_own_level_blocks():
    assert observation([order(updated_at=1000)], bids=[(99, 10)]).market_mid is None
    assert observation([order(price="100")], bids=[(99, 100)]).detail == "inconsistent_own_depth"


def test_cached_fill_and_lost_orders_are_included_in_own_price_uncertainty():
    connector = SimpleNamespace(name="okx", in_flight_orders={}, _order_tracker=SimpleNamespace(
        cached_orders={"filled": order(state=OrderState.FILLED, filled="10")}, lost_orders={}))
    assert observe_market(connector, book(bids=[(99, 10)]), "LIFE-USDT", now=1000, max_age_seconds=10,
                          max_levels=5, max_orders=20).market_mid is None
    connector._order_tracker.cached_orders = {}
    connector._order_tracker.lost_orders = {"lost": order(state=OrderState.FAILED)}
    assert observe_market(connector, book(bids=[(99, 10)]), "LIFE-USDT", now=1000, max_age_seconds=10,
                          max_levels=5, max_orders=20).market_mid is None


def test_stale_book_and_own_order_limit_block_without_unbounded_scan():
    assert observation(timestamp=980).detail == "stale_book"
    assert observation([order(str(i)) for i in range(3)], max_orders=2).detail == "own_order_limit"


def test_recovery_uses_elapsed_time_and_new_public_observations_not_readiness_call_count():
    guard = ready_guard()
    assert not guard.evaluate(observation(), Decimal("110"), 3)["ready"]
    good = observation(timestamp=1004, now=1004)
    for _ in range(100):
        assert not guard.evaluate(good, Decimal("100"), 100)["ready"]
    assert not guard.evaluate(good, Decimal("100"), 4, sample=True)["ready"]
    assert not guard.evaluate(good, Decimal("100"), 10, sample=True)["ready"]
    assert guard.evaluate(observation(timestamp=1011, now=1011), Decimal("100"), 11, sample=True)["ready"]


def test_recovery_requires_tighter_threshold_and_invalid_sample_resets_delay():
    guard = ready_guard()
    guard.evaluate(observation(), Decimal("110"), 3)
    assert not guard.evaluate(observation(timestamp=1004, now=1004), Decimal("101.5"), 4, sample=True)["ready"]
    guard.evaluate(observation(timestamp=1005, now=1005), Decimal("100"), 5, sample=True)
    guard.evaluate(observation(timestamp=989), Decimal("100"), 6)
    assert not guard.evaluate(observation(timestamp=1007, now=1007), Decimal("100"), 7, sample=True)["ready"]
    assert guard.evaluate(observation(timestamp=1009, now=1009), Decimal("100"), 9, sample=True)["ready"]


def test_manual_range_checks_observed_market_with_tighter_resume_band():
    limits = SidewaysRange(lower_price="90", upper_price="110", resume_lower_price="95", resume_upper_price="105")
    guard = QuoteGuard(policy(), limits)
    guard.evaluate(observation(), Decimal("100"), 0, sample=True)
    guard.evaluate(observation(timestamp=1002, now=1002), Decimal("100"), 2, sample=True)
    result = guard.evaluate(observation(bids=[(110, 10)], asks=[(112, 10)]), Decimal("111"), 3)
    assert result["reason_code"] == "sideways_breakout"
    assert not guard.evaluate(observation(bids=[(108, 10)], asks=[(110, 10)], timestamp=1004, now=1004),
                              Decimal("109"), 4, sample=True)["ready"]


@pytest.mark.parametrize("values", [dict(lower_price="100", upper_price="90", resume_lower_price="95", resume_upper_price="99"),
                                    dict(lower_price="90", upper_price="110", resume_lower_price="89", resume_upper_price="105"),
                                    dict(lower_price="90", upper_price="110", resume_lower_price="95", resume_upper_price="110"),
                                    dict(lower_price="NaN", upper_price="110", resume_lower_price="95", resume_upper_price="105")])
def test_manual_range_rejects_invalid_or_non_tighter_bounds(values):
    with pytest.raises(ValidationError):
        SidewaysRange(**values)


@pytest.mark.parametrize("mid", [90, 110])
def test_manual_outer_range_equality_is_permitted_after_admission(mid):
    guard = ready_guard()
    guard.sideways_range = SidewaysRange(lower_price="90", upper_price="110", resume_lower_price="95", resume_upper_price="105")
    assert guard.evaluate(observation(bids=[(mid - 1, 10)], asks=[(mid + 1, 10)]), Decimal(mid), 3)["ready"]


def test_rounding_noise_is_not_external_liquidity():
    value = observation([order(amount="999999999999999999")], bids=[(99, 10**18)])
    assert value.market_mid is None


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_downward_double_rounding_of_own_quantity_keeps_real_outside_depth(side):
    # Actual observed 5,091.6496 contracts at ctVal=.01: native book rows
    # contain 50.916495999999995 while the order ledger retains 50.916496.
    amount = Decimal('50.916496')
    projected = float('5091.6496') * .01
    own = order(side=side, price='99' if side == TradeType.BUY else '101', amount=str(amount))
    bids = [(99, projected), (98, 20)] if side == TradeType.BUY else [(99, 20)]
    asks = [(101, projected), (102, 20)] if side == TradeType.SELL else [(101, 20)]
    value = observation([own], bids=bids, asks=asks)
    assert value.market_mid == Decimal('99.5' if side == TradeType.BUY else '100.5')
    own_only = observation([own], bids=bids[:1], asks=asks[:1])
    assert own_only.market_mid is None
    # A real deficit outside floating-point noise remains inconsistent.
    if side == TradeType.BUY:
        bids[0] = (99, float(amount - Decimal('.0001')))
    else:
        asks[0] = (101, float(amount - Decimal('.0001')))
    assert observation([own], bids=bids, asks=asks).detail == 'inconsistent_own_depth'


def test_crossed_and_mutating_book_are_unavailable():
    connector = SimpleNamespace(name="okx", in_flight_orders={})
    crossed = SimpleNamespace(snapshot_uid=1000, last_diff_uid=1000,
                              bid_entries=lambda: iter([SimpleNamespace(price=102, amount=10)]),
                              ask_entries=lambda: iter([SimpleNamespace(price=101, amount=10)]))
    assert observe_market(connector, crossed, "LIFE-USDT", now=1000, max_age_seconds=10,
                          max_levels=5, max_orders=20).detail == "crossed_book"

    def changing():
        crossed.last_diff_uid += 1
        return iter([SimpleNamespace(price=99, amount=10)])
    crossed.bid_entries = changing
    assert observe_market(connector, crossed, "LIFE-USDT", now=1000, max_age_seconds=10,
                          max_levels=5, max_orders=20).detail == "inconsistent_observation"


def test_restart_does_not_inherit_a_previously_recovered_gate():
    ready_guard()
    new = QuoteGuard(policy())
    for _ in range(100):
        assert not new.evaluate(observation(), Decimal("100"), 100)["ready"]
    assert not new.evaluate(observation(), Decimal("100"), 100, sample=True)["ready"]


def test_same_public_book_with_private_changes_is_not_a_second_recovery_sample():
    guard = QuoteGuard(policy())
    guard.evaluate(observation(), Decimal("100"), 0, sample=True)
    # Private acknowledgements alone cannot satisfy two independent book samples.
    assert not guard.evaluate(observation([order(amount="1")]), Decimal("100"), 100, sample=True)["ready"]


def test_guard_http_contract_uses_decimal_strings_and_utc_timestamps():
    from hummingbot.cli.admin_models import QuoteGuardStatus
    result = ready_guard().evaluate(observation(timestamp=1003, now=1003), Decimal("100"), 3)
    body = QuoteGuardStatus.model_validate(result).model_dump(mode="json")
    assert body["market_mid"] == "100.0" or body["market_mid"] == "100"
    assert body["book_observed_at"].endswith("Z")
    assert body["evaluated_at"].endswith("Z")
