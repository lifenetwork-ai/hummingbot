"""Financial readiness reuse stays fenced by independent journal verification."""
import os
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history
from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import capacity
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.lazy_archive import TransformedMap
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent


def unexpected(*args, **kwargs):
    raise AssertionError("state-only transition replayed settled financial history")


def partial(tmp_path):
    _, _, wal, book = _setup(tmp_path)
    a, _ = _attributor(tmp_path, wal, book)
    fill = SpotFill("trade-1", D("0.4"), D("1"), "USDT", D("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, book, require_fees=True).apply_fills("wire-1", (fill,), D("0.4"))
    assert a.apply("wire-1", (fill,))
    wal.migrate_archive(capacity("wal"))
    book.migrate_archive(capacity("risk"))
    assert a.ready()
    return a, fill


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 1000, 10000])
@pytest.mark.parametrize("mode", ["BUY", "SELL", "exit"])
async def test_zero_fill_order_lifecycle_reuses_financial_proof(tmp_path, monkeypatch, history, mode):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        archive_financial_journals(f)
        a = f.c._fill_attributor
        assert a.ready()
        wal, book = a.wal, a.reservations
        current = f.c._order_safety_manager.current_session
        value = SpotIntent("new", "SELL" if mode == "exit" else mode, D("1"), D("1"), current.session_id, current.epoch)
        original_cut = a._ready_cut
        monkeypatch.setattr(a, "_durable_sources_match", unexpected)
        monkeypatch.setattr(a, "capital", unexpected)
        monkeypatch.setattr(TransformedMap, "items", unexpected)
        monkeypatch.setattr(TransformedMap, "values", unexpected)
        result = (book.reserve_risk_reduction(value, target_base=D("0")) if mode == "exit"
                  else book.reserve(value, reference_price=D("1")))
        assert result.allowed and a.ready()
        wal.begin("new", client_order_id="new-wire", session_id=current.session_id, epoch=current.epoch,
                  reservation_id="new", slot_market="LIFE-USDT", slot_side=value.side, slot_level=0)
        assert a.ready()
        wal.arm_send("new", client_order_id="new-wire", session_id=current.session_id, epoch=current.epoch,
                     reservation_id="new")
        assert a.ready()
        wal.acknowledge("new", "exchange-new")
        assert a.ready()
        book.mark_unknown("new")
        assert a.ready()
        wal.mark_cancel_requested("new")
        wal.mark_cancel_attempt("new", at=f.v[6].wall)
        book.request_cancel("new")
        assert a.ready()
        assert a._ready_cut is original_cut  # One detached proof, no growing cache.


@pytest.mark.parametrize("operation", ["ack", "cancel", "unknown"])
def test_partial_filled_active_order_state_changes_do_not_replay(tmp_path, monkeypatch, operation):
    a, _ = partial(tmp_path)
    monkeypatch.setattr(a, "_durable_sources_match", unexpected)
    monkeypatch.setattr(a, "capital", unexpected)
    if operation == "ack":
        a.wal.acknowledge("i1", "exchange-1")
    elif operation == "cancel":
        a.wal.mark_cancel_requested("i1")
        a.wal.mark_cancel_attempt("i1", at=AT)
        a.reservations.request_cancel("i1")
    else:
        a.reservations.mark_unknown("i1")
    assert a.ready()


@pytest.mark.parametrize("change", ["fill", "fee", "cashflow", "budget", "attribution", "budget_bytes"])
def test_financial_changes_invalidate_completed_proof(tmp_path, monkeypatch, change):
    a, _ = partial(tmp_path)
    check = Mock(wraps=a._durable_sources_match)
    monkeypatch.setattr(a, "_durable_sources_match", check)
    if change == "fill":
        a.reservations.record_fill("i1", "new-trade", D("0.1"), D("1"))
    elif change == "fee":
        book = a.reservations
        events = dict(book._account_events.items())
        events["FEE:trade-1"] = ("FEE", "USDT", D("-0.02"))
        book._commit(book.life_balance, book.usdt_balance - D("0.01"), book._reservations, book._trades, events)
    elif change == "cashflow":
        a.reservations.record_cashflow("102", "USDT", D("1"))
    elif change == "budget":
        a.loss_budget.record("new-loss", D("0"), session_id=a.wal.get("i1").session_id, at_utc=AT)
    elif change == "budget_bytes":
        a.loss_budget.path.write_bytes(a.loss_budget.path.read_bytes() + b"\n")
    else:
        a._events["trade-1"]["loss_quote"] = "1"
    assert a.ready() is (change in ("budget", "budget_bytes"))
    assert check.call_count == 1


@pytest.mark.parametrize("field,value", [("client_order_id", "other-wire"), ("session_id", "other"),
                                         ("slot_side", "SELL"), ("slot_market", "BTC-USDT")])
@pytest.mark.parametrize("settled", [False, True])
def test_changed_coherent_wal_provenance_cannot_reuse_proof(tmp_path, monkeypatch, field, value, settled):
    a, _ = partial(tmp_path)
    if settled:
        # Partial cancellation still retains its reconciled fill and attribution.
        a.reservations.confirm_terminal("i1", cumulative_filled=D("0.4"), fills_reconciled=True, exchange_state="CANCELED")
        a.wal.mark_terminal("i1", "exchange-1")
        assert a.ready()
    a.wal._commit(replace(a.wal.get("i1"), **{field: value}))
    check = Mock(wraps=a._durable_sources_match)
    monkeypatch.setattr(a, "_durable_sources_match", check)
    assert not a.ready()
    assert check.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wal_memory", "balance", "trade", "account_event", "filled_total",
                                   "wal_uncertain", "risk_uncertain", "budget_uncertain", "attribution_memory",
                                   "budget_memory", "attribution_disk", "budget_disk", "wal_segment", "risk_segment",
                                   "wal_missing", "risk_symlink", "stale_wal", "stale_risk"])
async def test_reused_financial_proof_never_hides_unverified_inputs(tmp_path, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        a = f.c._fill_attributor
        assert a.ready()
        if fault == "wal_memory":
            a.wal._records = {}
        elif fault == "balance":
            a.reservations.life_balance += 1
        elif fault == "trade":
            a.reservations._trades = {**a.reservations._trades, "history-00000": ("past-00000", D("2"), D("1"))}
        elif fault == "account_event":
            a.reservations._account_events = {}
        elif fault == "filled_total":
            a.reservations._filled_base_total += 1
        elif fault.endswith("uncertain"):
            obj = {"wal_uncertain": a.wal, "risk_uncertain": a.reservations, "budget_uncertain": a.loss_budget}[fault]
            obj._uncertain = True
        elif fault == "attribution_memory":
            a._events["history-00000"]["loss_quote"] = "1"
        elif fault == "budget_memory":
            a.loss_budget._events["fill:history-00000"]["loss_quote"] = "1"
        elif fault in ("attribution_disk", "budget_disk"):
            path = a.path if fault == "attribution_disk" else a.loss_budget.path
            raw = path.read_bytes()
            path.write_bytes(raw.replace(b'"loss_quote":"0"', b'"loss_quote":"1"', 1)
                             if fault == "attribution_disk" else raw.replace(b'"loss_quote": "0"', b'"loss_quote": "1"', 1))
            assert path.read_bytes() != raw
        elif fault == "stale_wal":
            a.wal.reopen().mark_cancel_requested("past-00000")
        elif fault == "stale_risk":
            a.reservations.reopen().record_cashflow("102", "USDT", D("1"))
        else:
            import json
            book = a.wal if fault.startswith("wal") else a.reservations
            entry = json.loads(book.path.read_bytes())["archives"][0]["segments"][0]
            path = book._archive.archive.directory / (entry["sha256"] + ".json")
            raw, status = path.read_bytes(), path.stat()
            if fault == "wal_missing":
                path.unlink()
            elif fault == "risk_symlink":
                target = tmp_path / "foreign"
                target.write_bytes(raw)
                path.unlink()
                path.symlink_to(target)
            else:
                changed = raw.replace(b'BUY', b'BUX', 1)
                assert changed != raw and len(changed) == len(raw)
                path.write_bytes(changed)
                os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))
        assert not a.ready()


def test_cold_restore_requires_complete_financial_validation(tmp_path, monkeypatch):
    a, _ = partial(tmp_path)
    restored = ReconciledFillAttributor(
        a.path, wal=a.wal.reopen(), reservations=a.reservations.reopen(), loss_budget=a.loss_budget,
        opening_life=a.opening_life, opening_usdt=a.opening_usdt,
        opening_independent_price_usdt=a.opening_independent_price_usdt,
        independent_value=a.independent_value, max_reference_skew_ms=a.max_reference_skew_ms, create=False)
    check = Mock(wraps=restored._durable_sources_match)
    monkeypatch.setattr(restored, "_durable_sources_match", check)
    assert restored.ready() and check.call_count == 1


def test_legacy_state_change_revalidates_complete_cut(tmp_path, monkeypatch):
    _, _, wal, book = _setup(tmp_path)
    a, _ = _attributor(tmp_path, wal, book)
    assert a.ready()
    wal.mark_cancel_requested("i1")
    check = Mock(wraps=a._durable_sources_match)
    monkeypatch.setattr(a, "_durable_sources_match", check)
    assert a.ready() and check.call_count == 1


@pytest.mark.parametrize("archived", [False, True])
def test_detached_policy_cannot_alias_mutable_transfer_timestamps(tmp_path, archived):
    _, _, wal, book = _setup(tmp_path)
    approvals = CashflowApprovals("100", {"102": ("USDT", D("1"))}, {"102": AT_MS})
    a, _ = _attributor(tmp_path, wal, book, approvals=approvals)
    book.record_cashflow("102", "USDT", D("1"))
    assert a.apply_approved_cashflows()
    if archived:
        wal.migrate_archive(capacity("wal"))
        book.migrate_archive(capacity("risk"))
    assert a.ready()
    a.cashflow_approvals.transfer_times_ms["102"] += 1
    assert not a.ready()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_real_sender_reuses_completed_financial_proof(tmp_path, monkeypatch, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        a = f.c._fill_attributor
        assert a.ready()
        monkeypatch.setattr(a, "_durable_sources_match", unexpected)
        claim, _ = send(f, action)
        assert a.ready() and a.reservations.has_open_intent(claim.intent_id)


@pytest.mark.asyncio
async def test_financial_change_revokes_real_final_send_with_claim_retained(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        claim, wire = send(f, action)
        f.c._order_safety_reservations.record_cashflow("102", "USDT", D("1"))
        with pytest.raises(PermissionError):
            f.v[3].sent[-1]["pre_send_check"](wire)
        assert claim.intent_id in f.v[1].claim_ids()
        assert f.c._order_safety_reservations.has_open_intent(claim.intent_id)


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("change", ["transfer_time", "fill_value"])
def test_initial_proof_rejects_memory_change_during_financial_replay(tmp_path, monkeypatch, archived, change):
    _, _, wal, book = _setup(tmp_path)
    approvals = CashflowApprovals("100", {"102": ("USDT", D("1"))}, {"102": AT_MS})
    a, _ = _attributor(tmp_path, wal, book, approvals=approvals)
    book.record_cashflow("102", "USDT", D("1"))
    assert a.apply_approved_cashflows()
    fill = SpotFill("trade-1", D("0.4"), D("1"), "USDT", D("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, book, require_fees=True).apply_fills("wire-1", (fill,), D("0.4"))
    assert a.apply("wire-1", (fill,))
    if archived:
        wal.migrate_archive(capacity("wal"))
        book.migrate_archive(capacity("risk"))
    assert a.ready()
    del a._ready_cut
    original = a.capital

    def changed():
        result = original()
        if change == "transfer_time":
            a.cashflow_approvals.transfer_times_ms["102"] += 1
        else:
            a._events["trade-1"]["independent_value_usdt"] = "0.8"
        return result
    monkeypatch.setattr(a, "capital", changed)
    assert not a.ready()
    assert not hasattr(a, "_ready_cut")
