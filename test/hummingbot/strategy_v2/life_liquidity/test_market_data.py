"""Order book readiness checks fail closed on absent and incomplete books."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.market_data import (
    ContinuousTradingGate,
    SnapshotQualityGate,
    is_order_book_ready,
)


@pytest.mark.parametrize("ready,bid,ask", [
    (False, 1, 2), (True, float("nan"), 2), (True, 1, float("nan")),
    (True, 0, 2), (True, 1, 0), (True, 2, 1), (True, 1, 1),
])
def test_unready_or_invalid_top_of_book_is_rejected(ready, bid, ask):
    book = SimpleNamespace(get_price=lambda is_buy: ask if is_buy else bid)
    connector = SimpleNamespace(ready=ready, get_order_book=lambda pair: book)
    assert is_order_book_ready(connector, "LIFE-USDT") is False


def test_missing_book_is_rejected_and_valid_book_is_accepted():
    def missing(pair):
        raise KeyError(pair)

    connector = SimpleNamespace(ready=True, get_order_book=missing)
    assert is_order_book_ready(connector, "LIFE-USDT") is False

    def empty_book(is_buy):
        raise EnvironmentError("Order book is empty")

    connector.get_order_book = lambda pair: SimpleNamespace(get_price=empty_book)
    assert is_order_book_ready(connector, "LIFE-USDT") is False
    connector.get_order_book = lambda pair: SimpleNamespace(get_price=lambda is_buy: 2 if is_buy else 1)
    assert is_order_book_ready(connector, "LIFE-USDT") is True


def instrument(**changes):
    data = {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
            "openType": "call_auction", "listTime": "1000", "contTdSwTime": "2000"}
    data.update(changes)
    return data


def server_time(ts="2000"):
    return {"code": "0", "data": [{"ts": ts}]}


@pytest.mark.parametrize("open_type", ["call_auction", "pre_quote"])
def test_live_during_auction_or_prequote_is_not_continuous(open_type):
    gate = ContinuousTradingGate()
    assert gate.evaluate(instrument(openType=open_type), server_time("1999")) is False
    assert gate.reason_code == "CONTINUOUS_TRADING_NOT_STARTED"
    assert gate.evaluate(instrument(openType=open_type), server_time("2000")) is True
    assert gate.reason_code == "CONTINUOUS_TRADING_CONFIRMED"


@pytest.mark.parametrize("open_type", ["", "fix_price"])
def test_normal_listing_uses_list_time_when_no_continuous_switch(open_type):
    gate = ContinuousTradingGate()
    metadata = instrument(openType=open_type, contTdSwTime="")
    assert gate.evaluate(metadata, server_time("999")) is False
    assert gate.evaluate(metadata, server_time("1000")) is True


@pytest.mark.parametrize("changes,reason", [
    ({"state": "preopen"}, "INSTRUMENT_NOT_LIVE"),
    ({"state": "suspend"}, "INSTRUMENT_NOT_LIVE"),
    ({"openType": "call_auction", "contTdSwTime": ""}, "CONTINUOUS_START_UNKNOWN"),
    ({"openType": "pre_quote", "contTdSwTime": ""}, "CONTINUOUS_START_UNKNOWN"),
    ({"openType": "unknown"}, "OPEN_TYPE_UNKNOWN"),
    ({"openType": None}, "OPEN_TYPE_UNKNOWN"),
    ({"listTime": ""}, "LIST_TIME_INVALID"),
    ({"listTime": "1" * 100}, "LIST_TIME_INVALID"),
    ({"contTdSwTime": "bad"}, "CONTINUOUS_START_INVALID"),
    ({"contTdSwTime": "999"}, "CONTINUOUS_START_INVALID"),
])
def test_unknown_or_inconsistent_listing_phase_fails_closed(changes, reason):
    gate = ContinuousTradingGate()
    assert gate.evaluate(instrument(**changes), server_time("3000")) is False
    assert gate.reason_code == reason


@pytest.mark.parametrize("response", [
    None, {}, {"code": "1", "data": [{"ts": "3000"}]},
    {"code": "0", "data": []}, server_time("NaN"), server_time("-1"),
    server_time("1" * 100),
])
def test_missing_or_bad_exchange_time_cannot_confirm_continuous_trading(response):
    gate = ContinuousTradingGate()
    assert gate.evaluate(instrument(), response) is False
    assert gate.reason_code == "EXCHANGE_TIME_INVALID"


def test_new_sample_revokes_previous_continuous_readiness():
    gate = ContinuousTradingGate()
    assert gate.evaluate(instrument(), server_time("2500")) is True
    assert gate.evaluate(instrument(state="suspend"), server_time("2501")) is False
    assert gate.ready is False


def book(*, ts="1500", bid="1.0000", ask="1.1000", bid_size="10", ask_size="12"):
    return {"code": "0", "data": [{
        "ts": ts,
        "bids": [[bid, bid_size, "0", "1"]],
        "asks": [[ask, ask_size, "0", "1"]],
        "seqId": 42,
    }]}


def test_market_snapshot_keeps_exchange_receipt_depth_state_and_source():
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(book(), exchange_now_ms=2000, received_monotonic=7.25,
                         market_state="live", continuous_trading_ready=True) is True
    snapshot = gate.snapshot
    assert snapshot.instrument == "LIFE-USDT"
    assert snapshot.exchange_timestamp_ms == 1500
    assert snapshot.received_monotonic == 7.25
    assert snapshot.bid == Decimal("1.0000")
    assert snapshot.ask == Decimal("1.1000")
    assert snapshot.bid_size == Decimal("10")
    assert snapshot.ask_size == Decimal("12")
    assert snapshot.bid_depth == ((Decimal("1.0000"), Decimal("10")),)
    assert snapshot.ask_depth == ((Decimal("1.1000"), Decimal("12")),)
    assert snapshot.market_state == "live"
    assert snapshot.data_source == "okx_rest_books"
    assert snapshot.sequence_id == 42
    assert gate.permit(8.75) is True
    assert gate.permit(8.751) is False


@pytest.mark.parametrize("side,level,reason", [
    ("bids", ["NaN", "2"], "BOOK_PRICE_INVALID"),
    ("asks", ["1.2", "0"], "BOOK_DEPTH_INVALID"),
    ("bids", ["1.2", "2"], "BOOK_LEVELS_UNSORTED"),
    ("asks", ["1.0", "2"], "BOOK_LEVELS_UNSORTED"),
])
def test_deeper_book_levels_are_checked(side, level, reason):
    response = book()
    response["data"][0][side].append(level)
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(response, exchange_now_ms=2000, received_monotonic=1,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == reason


@pytest.mark.parametrize("response,reason", [
    (book(ts="0"), "BOOK_RESPONSE_INVALID"),
    (book(ts="NaN"), "BOOK_RESPONSE_INVALID"),
    (book(bid="NaN"), "BOOK_PRICE_INVALID"),
    (book(ask="Infinity"), "BOOK_PRICE_INVALID"),
    (book(bid="0"), "BOOK_PRICE_INVALID"),
    (book(ask="-1"), "BOOK_PRICE_INVALID"),
    (book(bid="1.2", ask="1.1"), "BOOK_CROSSED"),
    (book(bid="1.1", ask="1.1"), "BOOK_CROSSED"),
    (book(bid_size="0"), "BOOK_DEPTH_INVALID"),
    (book(ask_size="NaN"), "BOOK_DEPTH_INVALID"),
    ({"code": "0", "data": []}, "BOOK_RESPONSE_INVALID"),
    ({"code": "1", "data": [book()["data"][0]]}, "BOOK_RESPONSE_INVALID"),
])
def test_invalid_snapshot_never_grants_permission(response, reason):
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(response, exchange_now_ms=2000, received_monotonic=1,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == reason
    assert gate.permit(1) is False


def test_stale_future_and_out_of_order_snapshots_fail_closed_without_cached_fallback():
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(book(ts="5000"), exchange_now_ms=5500, received_monotonic=1,
                         market_state="live", continuous_trading_ready=True) is True
    assert gate.evaluate(book(ts="4000"), exchange_now_ms=5600, received_monotonic=2,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == "BOOK_OUT_OF_ORDER"
    assert gate.permit(2) is False
    assert gate.evaluate(book(ts="6000"), exchange_now_ms=9001, received_monotonic=3,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == "BOOK_STALE"
    assert gate.evaluate(book(ts="9100"), exchange_now_ms=9000, received_monotonic=4,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == "BOOK_TIMESTAMP_FUTURE"


def test_out_of_order_receipt_and_conflicting_same_timestamp_are_rejected():
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(book(), exchange_now_ms=2000, received_monotonic=5,
                         market_state="live", continuous_trading_ready=True) is True
    assert gate.evaluate(book(ts="1600"), exchange_now_ms=2000, received_monotonic=4,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == "RECEIVE_TIME_OUT_OF_ORDER"
    assert gate.evaluate(book(bid="1.01"), exchange_now_ms=2000, received_monotonic=6,
                         market_state="live", continuous_trading_ready=True) is False
    assert gate.reason_code == "BOOK_TIMESTAMP_CONFLICT"


def test_crossed_book_is_only_observed_without_trade_permission_before_continuous_phase():
    gate = SnapshotQualityGate("LIFE-USDT", max_age_ms=2000)
    assert gate.evaluate(book(bid="1.2", ask="1.1"), exchange_now_ms=2000, received_monotonic=1,
                         market_state="live", continuous_trading_ready=False) is False
    assert gate.reason_code == "CONTINUOUS_TRADING_NOT_CONFIRMED"
