"""Retained rolling history must not become a large cyclic-GC graph."""
import copy
import gc
from decimal import Decimal
from test.hummingbot.core.test_spot_outflow import fill, order, policy

from hummingbot.core.spot_outflow import SpotOutflowProjection


def test_retained_history_has_no_mutable_fee_payload_or_tracked_decimal_graph():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    for index in range(256):
        ledger.add_fill(fill(f"{index}:fill", 19999, amount=".00000001234567890123456789",
                             fees=[("USDT", ".0000000000123456789"), ("LIFE", "-.000000001")]))
    # Collect enough generations to untrack immutable nested tuples. This is a
    # representation-cost bound, not a hardware-sensitive latency assertion.
    for _ in range(4):
        gc.collect(2)
    assert len(ledger.entries) == 256
    assert sum(gc.is_tracked(value) for value in ledger.entries.values()) == 0
    assert ledger.status()["non_usdt_fees"] == {"LIFE": Decimal("-.000000256")}


def test_duplicate_fee_identity_is_a_snapshot_even_if_caller_mutates_metadata():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    row = fill("a:1", 19999, fees=[("USDT", ".2")])
    ledger.add_fill(row)
    row["trade_fee"]["fee_type"] = "DeductedFromReturns"
    ledger.add_fill(row)
    assert ledger.reason() == "spot_outflow_fill_conflict"
    assert ledger.net == Decimal("10.2")


def test_exact_decimal_duplicates_allow_equivalent_quantum_and_json_number_types():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    row = fill("a:1", 19999, price="10.000", amount=".1234567890123456789012345678",
               fees=[("USDT", ".000000000000123456789"), ("ETH", "-.00000000000001")])
    row["trade_fee"]["metadata"] = {"same": 1, "ordered": ["a", "b"]}
    ledger.add_fill(row)
    net = ledger.net
    replay = copy.deepcopy(row)
    replay["price"] = Decimal("1E+1")
    replay["amount"] = Decimal(".123456789012345678901234567800")
    replay["trade_fee"]["metadata"]["same"] = 1.0
    replay["trade_fee"] = dict(reversed(list(replay["trade_fee"].items())))
    ledger.add_fill(replay)
    assert ledger.reason() is None and ledger.net == net and len(ledger.entries) == 1
    ledger.advance(30000)
    assert ledger.net == 0 and ledger.status()["non_usdt_fees"] == {}


def test_future_exact_quantity_remains_reserved_until_commit_then_expires():
    ledger = SpotOutflowProjection(policy(), "LIFE-USDT", coverage_start_ms=0)
    ledger.advance(20000)
    amount = ".1234567890123456789012345678"
    assert ledger.reserve(order("a", amount=amount))
    ledger.confirm_terminal("a", Decimal(amount))
    ledger.add_fill(fill("a:1", 20000, amount=amount, fees=[("LIFE", "-.0000000001")]))
    assert ledger.reserved() > 0 and ledger.net == 0
    ledger.advance(20001)
    assert ledger.reserved() == 0 and ledger.net == Decimal(amount) * 10
    ledger.advance(30001)
    assert ledger.net == 0 and not ledger.entries
