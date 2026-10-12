"""Order telemetry observes one reservation without materializing history."""
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.lazy_archive import LazyArchiveMap
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger


def no_snapshot(*args, **kwargs):
    raise AssertionError("order telemetry materialized all reservations")


def migrate(f, archived):
    if archived:
        archive_settled_history(f)
        archive_financial_journals(f)


def assert_inputs(row, held):
    assert row["inputs"]["intent_price_usdt"] == str(held.intent.limit_price_usdt)
    assert row["inputs"]["intent_quantity_base"] == str(held.intent.quantity_base)
    assert row["inputs"]["intent_remaining_base"] == str(held.remaining_base)
    assert row["inputs"]["intent_side"] == held.intent.side
    assert row["intent_id"] == held.intent.intent_id


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_protected_sender_records_exact_intent_without_reservation_snapshot(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        monkeypatch.setattr(ReservationLedger, "reservation_snapshot", no_snapshot)
        order, _ = send(f, action)
        rows = [r for r in f.log.records() if r["stage"] == "FINAL_SEND" and r["allowed"]]
        assert len(rows) == 1
        assert_inputs(rows[0], f.c._order_safety_reservations.reservation_state(order.intent_id))
        assert len(f.v[3].sent) == 1 and f.log.healthy


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("state", ["OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN", "TERMINAL"])
async def test_order_telemetry_observes_current_remaining_quantity(tmp_path, monkeypatch, archived, state):
    async with system(tmp_path) as f:
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, _ = send(f, f.c._quote_action_planner.propose()[0])
        ledger = f.c._order_safety_reservations
        if state == "PARTIAL":
            ledger.record_fill(order.intent_id, "partial-telemetry", order.quantity_base / 2, order.price_usdt)
        elif state == "TERMINAL":
            ledger.confirm_terminal(order.intent_id, cumulative_filled=Decimal("0"),
                                    fills_reconciled=True, exchange_state="CANCELED")
        elif state == "CANCEL_PENDING":
            ledger.request_cancel(order.intent_id)
        elif state == "UNKNOWN":
            ledger.mark_unknown(order.intent_id)
        expected = ledger.reservation_state(order.intent_id)
        monkeypatch.setattr(ledger, "reservation_snapshot", no_snapshot)
        assert f.c._record_telemetry("PERMISSION", order.wire_id, True, "OBSERVED")
        assert_inputs(f.log.records()[-1], expected)
        # A second observation must sample the current row, not a telemetry cache.
        if state == "OPEN":
            ledger.record_fill(order.intent_id, "later-fill", order.quantity_base / 4, order.price_usdt)
        assert f.c._record_telemetry("PERMISSION", order.wire_id, True, "OBSERVED_AGAIN")
        assert_inputs(f.log.records()[-1], ledger.reservation_state(order.intent_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_historical_order_telemetry_uses_only_exact_rows(tmp_path, monkeypatch, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 1000)
        migrate(f, archived)
        ledger, wal = f.c._order_safety_reservations, f.c._order_safety_wal
        record = wal.get("past-00000")
        expected = ledger.reservation_state(record.intent_id)
        monkeypatch.setattr(ledger, "reservation_snapshot", no_snapshot)
        monkeypatch.setattr(LazyArchiveMap, "items", no_snapshot)
        assert f.c._record_telemetry("PERMISSION", record.client_order_id, False, "HISTORICAL")
        assert_inputs(f.log.records()[-1], expected)
        assert f.log.records()[-1]["inputs"]["wal_state"] == "TERMINAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("stage", ["CANCEL_REQUEST", "ACK", "EXCHANGE_CONFIRM"])
async def test_cancel_and_confirmation_hooks_do_not_lookup_reservations(tmp_path, monkeypatch, archived, stage):
    async with system(tmp_path) as f:
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, _ = send(f, f.c._quote_action_planner.propose()[0])
        monkeypatch.setattr(f.c._order_safety_reservations, "reservation_snapshot", no_snapshot)
        monkeypatch.setattr(f.c._order_safety_reservations, "reservation_state", no_snapshot)
        assert f.c._record_telemetry(stage, order.wire_id, None, "OBSERVED")
        assert "intent_remaining_base" not in f.log.records()[-1]["inputs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_reservation_lookup_error_still_revokes_final_send(tmp_path, monkeypatch, archived):
    async with system(tmp_path) as f:
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, wire = send(f, f.c._quote_action_planner.propose()[0])
        monkeypatch.setattr(f.c._order_safety_reservations, "reservation_snapshot", no_snapshot)
        monkeypatch.setattr(f.c._order_safety_reservations, "reservation_state", no_snapshot)
        with pytest.raises(PermissionError, match="TELEMETRY_UNAVAILABLE"):
            f.v[3].sent[-1]["pre_send_check"](wire)
        assert not f.log.healthy and f.c.telemetry_reason_code == "TELEMETRY_UNAVAILABLE"
        assert len(f.v[3].sent) == 1
        assert f.v[1].retained_claim_ids() == (order.intent_id,)
