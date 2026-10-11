"""Settled ownership proof reuse never hides either journal's changed evidence."""
import os
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history

import pytest

from hummingbot.strategy_v2.life_liquidity.lazy_archive import TransformedMap
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent, _Reservation
from hummingbot.strategy_v2.life_liquidity.state import IntentRecord, IntentWAL


def consistent(f):
    session = f.c._order_safety_manager.current_session
    return f.c._quote_action_planner._journal_consistent(session.session_id, session.epoch)


def unexpected(*args, **kwargs):
    raise AssertionError("spot permission materialized settled WAL/reservation history")


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_real_protected_send_uses_complete_proof_without_all_records(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        if archived:
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        monkeypatch.setattr(IntentWAL, "all_records", unexpected)
        assert consistent(f)
        claim, _ = send(f, action)
        assert claim.intent_id in f.v[1].claim_ids()
        assert f.c._order_safety_wal.get(claim.intent_id).state == "SEND_UNKNOWN"


@pytest.mark.asyncio
async def test_active_updates_reuse_settled_pair_proof_without_iteration(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 1000)
        archive_financial_journals(f)
        assert consistent(f)  # Build the complete settled relationship proof.
        wal, ledger = f.c._order_safety_wal, f.c._order_safety_reservations
        session = f.c._order_safety_manager.current_session
        wal._commit(IntentRecord("active", "active-wire", session.session_id, session.epoch,
                                 "active", "SEND_UNKNOWN", slot_market="LIFE-USDT", slot_side="BUY", slot_level=0))
        assert ledger.reserve(SpotIntent("active", "BUY", D("1"), D("1"), session.session_id, session.epoch),
                              reference_price=D("1")).allowed
        # Financial reserve currently reloads the complete archive. Warm its
        # independent verification before guarding *relationship* iteration.
        wal.assert_verified()
        ledger.verify_memory()
        monkeypatch.setattr(TransformedMap, "items", unexpected)
        monkeypatch.setattr(TransformedMap, "values", unexpected)
        assert consistent(f)
        ledger.mark_unknown("active")
        assert consistent(f)
        assert not f.c._quote_action_planner._journal_consistent(session.session_id, session.epoch + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("fault", ["missing_wal", "missing_reservation", "orphan_reservation", "terminal_gap",
                                   "session", "epoch", "side", "reservation_alias", "memory", "uncertain",
                                   "disk", "stale_writer", "ledger_disk", "ledger_memory", "binding"])
async def test_warm_proof_rejects_coherent_historical_conflict(tmp_path, archived, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_financial_journals(f)
        assert consistent(f)
        wal, ledger = f.c._order_safety_wal, f.c._order_safety_reservations
        rows = dict(wal._records.items())
        if fault in ("missing_wal", "terminal_gap", "session", "epoch", "side", "reservation_alias"):
            if fault == "missing_wal":
                del rows["past-00000"]
            else:
                changes = {"terminal_gap": dict(state="SEND_UNKNOWN", exchange_terminal_observed=False),
                           "session": dict(session_id="other-session"), "epoch": dict(epoch=999),
                           "side": dict(slot_side="SELL"), "reservation_alias": dict(reservation_id="past-00001")}
                rows["past-00000"] = replace(rows["past-00000"], **changes[fault])
            wal._save(rows)
            wal._records = wal.reopen()._records
        elif fault in ("missing_reservation", "orphan_reservation"):
            reservations = dict(ledger._reservations.items())
            trades, events = dict(ledger._trades.items()), dict(ledger._account_events.items())
            if fault == "missing_reservation":
                del reservations["past-00000"]
                del trades["history-00000"]
                del events["FEE:history-00000"]
            else:
                intent = replace(reservations["past-00000"].intent, intent_id="orphan")
                reservations["orphan"] = _Reservation(intent, D("0"), D("0"), "TERMINAL")
            ledger._commit(ledger.life_balance, ledger.usdt_balance, reservations, trades, events)
        elif fault == "memory":
            wal._records = {**rows, "past-00000": replace(rows["past-00000"], session_id="other-session")}
        elif fault == "uncertain":
            ledger._uncertain = True
        elif fault == "binding":
            ledger.path = tmp_path / "foreign.json"
        elif fault == "ledger_memory":
            ledger._reservations = {**ledger._reservations, "past-00000": replace(
                ledger._reservations["past-00000"], intent=replace(
                    ledger._reservations["past-00000"].intent, epoch=999))}
        elif fault == "stale_writer":
            other = wal.reopen()
            other._commit(replace(other.get("past-00000"), cancel_requested=True))
        else:
            journal = ledger if fault == "ledger_disk" else wal
            path = journal.path if not archived else next(journal._archive.archive.directory.glob("*.json"))
            if fault == "ledger_disk" and archived:
                path = next(p for p in journal._archive.archive.directory.glob("*.json") if b'"state":"TERMINAL"' in p.read_bytes())
            stat, raw = path.stat(), path.read_bytes()
            changed = raw.replace(b'"state":"TERMINAL"', b'"state":"INVALID!"', 1)
            assert changed != raw and len(changed) == len(raw)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert not consistent(f), fault


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("reserved", [False, True])
async def test_aborted_coverage_allows_absent_or_terminal_reservation_only(tmp_path, archived, reserved):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        wal, ledger = f.c._order_safety_wal, f.c._order_safety_reservations
        session = f.c._order_safety_manager.current_session
        wal._commit(IntentRecord("aborted", "aborted-wire", session.session_id, session.epoch,
                                 "aborted", "ABORTED_BEFORE_SEND"))
        if reserved:
            reservations = {**ledger._reservations, "aborted": _Reservation(
                SpotIntent("aborted", "BUY", D("1"), D("1"), session.session_id, session.epoch), D("0"), D("0"), "TERMINAL")}
            ledger._commit(ledger.life_balance, ledger.usdt_balance, reservations, ledger._trades)
        if archived:
            archive_financial_journals(f)
        assert consistent(f)
        restored_wal, restored_ledger = wal.reopen(), ledger.reopen()
        f.c._quote_action_planner.wal = restored_wal
        f.c._quote_action_planner.reservations = restored_ledger
        assert consistent(f)  # Cold restore must establish its own proof.


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_aborted_open_reservation_never_counts_as_settled_coverage(tmp_path, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        wal, ledger = f.c._order_safety_wal, f.c._order_safety_reservations
        session = f.c._order_safety_manager.current_session
        wal._commit(IntentRecord("aborted", "aborted-wire", session.session_id, session.epoch,
                                 "aborted", "ABORTED_BEFORE_SEND"))
        assert ledger.reserve(SpotIntent("aborted", "BUY", D("1"), D("1"), session.session_id, session.epoch),
                              reference_price=D("1")).allowed
        if archived:
            archive_financial_journals(f)
        assert not consistent(f)
        assert ledger.has_open_intent("aborted")


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_only_current_prepared_intent_is_allowed_at_sender_boundary(tmp_path, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_financial_journals(f)
        assert consistent(f)
        wal, ledger, sender = f.c._order_safety_wal, f.c._order_safety_reservations, f.c._protected_spot_sender
        session = f.c._order_safety_manager.current_session
        wal._commit(IntentRecord("prepared", "prepared-wire", session.session_id, session.epoch,
                                 "prepared", "PREPARED", slot_market="LIFE-USDT", slot_side="BUY", slot_level=0))
        assert ledger.reserve(SpotIntent("prepared", "BUY", D("1"), D("1"), session.session_id, session.epoch),
                              reference_price=D("1")).allowed
        assert not consistent(f)
        assert not sender._journals_match()
        assert not sender._journals_match(pending_intent_id="other")
        assert sender._journals_match(pending_intent_id="prepared")
        ledger.confirm_terminal("prepared", cumulative_filled=D("0"), fills_reconciled=True, exchange_state="CANCELED")
        assert not sender._journals_match(pending_intent_id="prepared")
        wal.mark_terminal("prepared", "prepared-exchange")
        assert consistent(f)  # Changed settled pair must be requalified.


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_cold_proof_cannot_publish_over_changed_journal(tmp_path, monkeypatch, archived):
    from hummingbot.strategy_v2.life_liquidity.spot_journal_proof import SpotJournalProof

    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_financial_journals(f)
        wal = f.c._order_safety_wal
        original = SpotJournalProof._coverage

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            wal._records = {**wal._records, "past-00000": replace(wal.get("past-00000"), session_id="changed")}
            return result

        monkeypatch.setattr(SpotJournalProof, "_coverage", staticmethod(changed))
        assert not consistent(f)


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
async def test_final_network_boundary_rechecks_warm_historical_proof_and_retains_claim(tmp_path, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        claim, wire = send(f, action)
        wal = f.c._order_safety_wal
        rows = {**wal._records, "past-00000": replace(wal.get("past-00000"), session_id="changed")}
        wal._save(rows)
        wal._records = wal.reopen()._records
        with pytest.raises((PermissionError, ValueError)):
            f.v[3].sent[-1]["pre_send_check"](wire)
        assert claim.intent_id in f.v[1].claim_ids()
        assert f.c._order_safety_reservations.has_open_intent(claim.intent_id)
