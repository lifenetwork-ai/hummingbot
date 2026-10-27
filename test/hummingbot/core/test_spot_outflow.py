import copy
import heapq
from decimal import Decimal
from random import Random
from types import SimpleNamespace

import pytest

from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowProjection


def policy():
    return SpotOutflowConfig(window_seconds=10, max_net_outflow_quote="100", fee_allowance_rate=".01")


def fill(identity, timestamp, side="BUY", price="10", amount="1", fees=()):
    return dict(market="okx", symbol="LIFE-USDT", order_id=identity.split(":")[0], exchange_trade_id=identity,
                timestamp=timestamp, trade_type=side, price=Decimal(price), amount=Decimal(amount),
                trade_fee=dict(percent="0", percent_token=None, fee_type="AddedToCost",
                               flat_fees=[dict(token=token, amount=value) for token, value in fees]))


def order(identity, amount="5", price="10"):
    return SimpleNamespace(client_order_id=identity, amount=Decimal(amount), price=Decimal(price))


@pytest.mark.parametrize("amount", [".001", ".00000001234567890123456789"])
def test_large_expiry_batch_matches_incremental_clock_with_bounded_heap_operations(monkeypatch, amount):
    from hummingbot.core import spot_outflow

    batch = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    batch.advance(20000)
    indices = list(range(4096))
    Random(7).shuffle(indices)
    for index in indices:
        batch.add_fill(fill(f"order-{index}:fill", 15000 + index, "BUY" if index % 2 else "SELL",
                            amount=amount, fees=[("USDT", ".00000001"), ("LIFE", "-.000001")]))
    batch.add_fill(fill("future:fill", 50000))
    reference = copy.deepcopy(batch)
    for now in range(25001, 29097):
        reference.advance(now)
    calls = []

    def pop(heap):
        calls.append(1)
        return heapq.heappop(heap)

    monkeypatch.setattr(spot_outflow, "heapq", SimpleNamespace(heappop=pop, heappush=heapq.heappush))
    batch.advance(29096)
    assert batch.status() == reference.status()
    assert batch.entries == reference.entries
    assert batch.future == reference.future and batch.expiry == reference.expiry
    # A cost budget rather than a machine-sensitive millisecond assertion.
    assert len(calls) <= 16
    for projection in (batch, reference):
        projection.add_fill(fill("new:fill", 29095, amount=".5"))
        projection.advance(39096)
        projection.advance(50001)
        assert projection.status()["recorded_net_outflow_quote"] == Decimal("10")
        projection.advance(60001)
    assert batch.status() == reference.status()
    assert not batch.entries and not batch.expiry and not batch.future


def test_exact_cashflow_original_fee_currencies_and_duplicate_same_timestamp_ids():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(10001)
    rows = [fill("a:1", 10000, fees=[("USDT", ".1234567890123456789"), ("LIFE", ".3")]),
            fill("a:2", 10000, "SELL", amount=".2", fees=[("USDT", "-.01"), ("ETH", ".0001")])]
    for row in rows + rows:
        ledger.add_fill(row)
    status = ledger.status()
    assert status["recorded_net_outflow_quote"] == Decimal("8.1134567890123456789")
    assert status["non_usdt_fees"] == {"LIFE": Decimal(".3"), "ETH": Decimal(".0001")}


def test_half_open_window_late_fill_expiry_and_future_edge():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    for key, stamp in (("old:1", 9999), ("edge:1", 10000), ("future:1", 20000)):
        ledger.add_fill(fill(key, stamp))
    assert ledger.status()["recorded_net_outflow_quote"] == Decimal("10")
    ledger.advance(20001)
    assert ledger.status()["recorded_net_outflow_quote"] == Decimal("10")
    ledger.add_fill(fill("late:1", 15000, "SELL", amount=".5"))
    assert ledger.status()["recorded_net_outflow_quote"] == Decimal("5")


def test_future_committed_debit_without_a_live_reservation_cannot_create_free_capacity():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    ledger.add_fill(fill("restored:1", 20000, amount="9"))
    assert ledger.status()["recorded_net_outflow_quote"] == 0
    assert not ledger.reserve(order("new", amount="2"))
    ledger.advance(20001)
    assert ledger.status()["recorded_net_outflow_quote"] == 90
    assert ledger.status()["reserved_quote"] == 0


def test_repeated_reservation_partial_commit_and_terminal_proof_handoff():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    first = order("a")
    assert ledger.reserve(first)
    assert ledger.reserve(first)  # The final gate counts this identity once.
    assert not ledger.reserve(order("b"))
    assert ledger.status()["reserved_quote"] == Decimal("50.50")
    ledger.add_fill(fill("a:1", 19999, amount="2", fees=[("USDT", ".2")]))
    assert ledger.status()["reserved_quote"] == Decimal("30.30")
    assert ledger.status()["recorded_net_outflow_quote"] == Decimal("20.2")
    # Local cancel/failed state alone cannot release a reservation.
    assert not ledger.reserve(order("b", amount="5"))
    ledger.confirm_terminal("a", Decimal("2"))
    assert ledger.reserve(order("b", amount="5"))
    assert ledger.status()["reserved_quote"] == Decimal("50.50")


def test_terminal_before_delayed_commit_keeps_capacity_and_same_id_cannot_change_terms():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    assert ledger.reserve(order("a", amount="9"))
    ledger.confirm_terminal("a", Decimal("9"))
    assert not ledger.reserve(order("b", amount="2"))
    assert not ledger.reserve(order("a", amount="1"))
    ledger.add_fill(fill("a:1", 20000, amount="9"))
    assert not ledger.reserve(order("b", amount="2"))  # [start, now) excludes this fill yet.
    ledger.advance(20001)
    assert ledger.status()["reserved_quote"] == 0
    assert ledger.status()["recorded_net_outflow_quote"] == 90
    assert not ledger.reserve(order("b", amount="2"))


def test_unbound_legacy_window_never_becomes_fresh_zero_and_sell_credit_is_not_pending_funding():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=10000)
    ledger.advance(19999)
    ledger.add_fill(fill("old:1", 9999))
    assert ledger.status()["recorded_net_outflow_quote"] is None
    assert not ledger.reserve(order("a", amount="1"))
    ledger.advance(20000)
    assert ledger.reserve(order("a", amount="9"))
    assert not ledger.reserve(order("b", amount="2"))
    ledger.add_fill(fill("sell:1", 19999, "SELL", amount="2"))
    assert not ledger.reserve(order("b", amount="2"))  # Negative net is floored at zero.
    ledger.release_unsent("a")
    assert ledger.reserve(order("b", amount="2"))


def test_unknown_actual_fee_percentage_is_unavailable_until_its_window_expires():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    row = fill("unknown:1", 19999)
    row["trade_fee"]["percent"] = ".001"
    ledger.add_fill(row)
    assert ledger.status()["reason_code"] == "spot_outflow_fee_unavailable"
    assert ledger.status()["recorded_net_outflow_quote"] is None
    ledger.advance(30000)
    assert ledger.status()["ready"] and ledger.status()["recorded_net_outflow_quote"] == 0


def test_long_lived_partial_order_deduplication_is_bounded_without_silently_freeing_capacity():
    config = SpotOutflowConfig(window_seconds=1, max_net_outflow_quote="20000", fee_allowance_rate=".01")
    ledger = SpotOutflowProjection(config, "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    assert ledger.reserve(order("a", amount="10001", price="1"))
    for index in range(10001):
        ledger.add_fill(fill("a:" + str(index), 19999, amount="1", price="1"))
        ledger.advance(20000 + index)
    assert ledger.status()["reason_code"] == "spot_outflow_order_fill_limit"
    assert len(ledger.reservations["a"].seen) == 10000
    assert not ledger.reserve(order("b", amount="1"))


@pytest.mark.parametrize("field,value", [("max_net_outflow_quote", "NaN"), ("fee_allowance_rate", "Infinity"),
                                         ("window_seconds", 0), ("max_net_outflow_quote", "0")])
def test_policy_requires_explicit_finite_bounds(field, value):
    values = dict(window_seconds=10, max_net_outflow_quote="100", fee_allowance_rate=".01")
    values[field] = value
    with pytest.raises(ValueError):
        SpotOutflowConfig(**values)
