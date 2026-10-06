"""P5.3 quote slots stay occupied until WAL terminal reconciliation."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.slots import SpotQuoteSlots
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _slots(tmp_path):
    wal = IntentWAL(tmp_path / "intents.json")
    return wal, SpotQuoteSlots(wal)


def _claim(slots, intent_id="i1", *, session_id="s1", epoch=1,
           side="BUY", level=0):
    return slots.claim(intent_id=intent_id, client_order_id=f"wire-{intent_id}",
                       reservation_id=intent_id, session_id=session_id, epoch=epoch,
                       side=side, level=level)


def test_one_active_intent_per_session_market_side_level(tmp_path):
    wal, slots = _slots(tmp_path)
    first = _claim(slots)
    assert first.state == "PREPARED"
    assert slots.status("s1", 1, "BUY", 0).state == "PREPARED"
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        _claim(slots, "i2")
    assert "i2" not in {record.intent_id for record in wal.all_records()}
    _claim(slots, "i3", side="SELL")
    _claim(slots, "i4", level=1)
    assert len(wal.all_records()) == 3


def test_cancel_ack_partial_fill_and_terminal_observation_do_not_free_slot(tmp_path):
    wal, slots = _slots(tmp_path)
    _claim(slots)
    wal.arm_send("i1", client_order_id="wire-i1", session_id="s1",
                 epoch=1, reservation_id="i1")
    wal.acknowledge("i1", "exchange-1")
    reservations = ReservationLedger(
        life_balance=Decimal("10"), usdt_balance=Decimal("10"),
        limits=RiskLimits(Decimal("0"), Decimal("20"), Decimal("30"), Decimal("20")))
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "s1", 1)
    assert reservations.reserve(intent, reference_price=Decimal("1")).allowed
    assert reservations.record_fill("i1", "trade-1", Decimal("0.4"), Decimal("1"))
    wal.mark_cancel_requested("i1")  # Exchange request ACK is not terminal proof.
    wal.mark_exchange_terminal_observed("i1", "exchange-1")
    assert slots.status("s1", 1, "BUY", 0).state == "RECONCILIATION_PENDING"
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        _claim(slots, "i2")
    assert reservations.reservation_ids == frozenset({"i1"})
    wal.mark_terminal("i1", "exchange-1")
    assert slots.status("s1", 1, "BUY", 0).state == "FREE"
    _claim(slots, "i2")


def test_restart_and_stale_wal_snapshot_cannot_reuse_busy_slot(tmp_path):
    wal, slots = _slots(tmp_path)
    stale = IntentWAL(wal.path)
    _claim(slots)
    restarted = SpotQuoteSlots(IntentWAL(wal.path))
    assert restarted.status("s1", 1, "BUY", 0).intent_id == "i1"
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        _claim(restarted, "i2")
    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        _claim(SpotQuoteSlots(stale), "i3", side="SELL")


def test_unscoped_or_old_session_orders_block_new_slot_claims(tmp_path):
    wal, slots = _slots(tmp_path)
    wal.prepare("legacy", client_order_id="legacy-wire", session_id="s1",
                epoch=1, reservation_id="legacy")
    with pytest.raises(ValueError, match="SLOT_SCOPE_UNPROVEN"):
        _claim(slots)
    wal.mark_terminal("legacy", "legacy-exchange")
    _claim(slots)
    with pytest.raises(ValueError, match="OLD_SESSION_ORDERS_UNRESOLVED"):
        _claim(slots, "i2", session_id="s2", epoch=2)
    with pytest.raises(ValueError, match="SLOT_REQUIRED"):
        wal.begin("bypass", client_order_id="bypass-wire", session_id="s1",
                  epoch=1, reservation_id="bypass")


def test_abort_before_send_frees_slot_and_concurrent_claim_has_one_winner(tmp_path):
    wal, slots = _slots(tmp_path)
    _claim(slots)
    wal.abort_before_send("i1")
    assert slots.status("s1", 1, "BUY", 0).state == "FREE"

    def compete(index):
        try:
            _claim(slots, f"race-{index}")
            return True
        except ValueError as exc:
            assert str(exc) == "SLOT_OCCUPIED"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(compete, range(8)))
    assert results.count(True) == 1
    assert sum(record.state == "PREPARED" for record in wal.all_records()) == 1
