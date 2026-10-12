"""Reservation membership checks one key and never enumerate terminal history."""
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import capacity, reservations

import pytest

from hummingbot.strategy_v2.life_liquidity.lazy_archive import LazyArchiveMap
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger


def no_enumeration(*args, **kwargs):
    raise AssertionError("single reservation membership enumerated history")


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("key,expected", [("pending", True), ("0", True), ("3", True), ("absent", False), ("1.5", False)])
def test_membership_includes_terminal_and_pending_rows_without_iteration(tmp_path, monkeypatch, archived, key, expected):
    ledger = reservations(tmp_path)
    if archived:
        ledger.migrate_archive(capacity("risk"))
        ledger = ledger.reopen()
    monkeypatch.setattr(ReservationLedger, "reservation_ids", property(no_enumeration))
    monkeypatch.setattr(LazyArchiveMap, "items", no_enumeration)
    assert ledger.has_reservation(key) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_protected_sender_does_not_build_complete_reservation_id_set(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        if archived:
            archive_settled_history(f)
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        monkeypatch.setattr(ReservationLedger, "reservation_ids", property(no_enumeration))
        order, wire = send(f, action)
        assert f.c._order_safety_reservations.has_reservation(order.intent_id)
        assert len(f.v[3].sent) == 1
        # A successful memory-only membership result cannot supply financial permission.
        f.v[1].journal._uncertain = True
        with pytest.raises(PermissionError):
            f.v[3].sent[-1]["pre_send_check"](wire)
        assert f.v[1].retained_claim_ids() == (order.intent_id,)
