"""Spot fees and approved account cashflows survive recovery exactly once."""

import json
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import NOW, FakeOkx, full_scope

import pytest

from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

LIMITS = RiskLimits(Decimal("0"), Decimal("30"), Decimal("30"), Decimal("30"))


def seeded(tmp_path, *, fee=None):
    ledger = ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                               limits=LIMITS, path=tmp_path / "reservations.json")
    assert ledger.reserve(SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"),
                                     "session", 1), reference_price=Decimal("1")).allowed
    assert ledger.record_fill("i1", "trade-1", Decimal("0.4"), Decimal("1"))
    if fee is not None:
        assert ledger.record_fee("trade-1", "LIFE", fee)
    wal = IntentWAL(tmp_path / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id="session", epoch=1,
                reservation_id="i1")
    wal.acknowledge("i1", "exchange-1")
    return ledger, wal


def approval_file(tmp_path, *, anchor="100", approvals=()):
    path = tmp_path / "cashflows.json"
    path.write_text(json.dumps({"schema_version": 1, "anchor_bill_id": anchor,
                                "approved": list(approvals)}))
    return path


def bill(bill_id, *, kind="1", subtype="11", currency="USDT", change="2",
         trade_id="", order_id="", inst_id="", fee="0"):
    return {"billId": bill_id, "type": kind, "subType": subtype,
            "ccy": currency, "balChg": change, "tradeId": trade_id, "fee": fee,
            "ordId": order_id, "instId": inst_id,
            "from": "6" if subtype == "11" else "18",
            "to": "18" if subtype == "11" else "6"}


def test_fee_and_cashflow_are_durable_idempotent_and_conflict_checked(tmp_path):
    ledger, _ = seeded(tmp_path)
    assert ledger.record_fee("trade-1", "LIFE", Decimal("-0.01"))
    assert ledger.record_cashflow("102", "USDT", Decimal("2"))
    recovered = ReservationLedger.restore(tmp_path / "reservations.json", limits=LIMITS)
    assert recovered.life_balance == Decimal("10.39")
    assert recovered.usdt_balance == Decimal("11.6")
    assert not recovered.record_fee("trade-1", "LIFE", Decimal("-0.01"))
    assert not recovered.record_cashflow("102", "USDT", Decimal("2"))
    with pytest.raises(ValueError, match="ACCOUNT_EVENT_CONFLICT"):
        recovered.record_cashflow("102", "USDT", Decimal("3"))
    with pytest.raises(ValueError, match="ACCOUNT_EVENT_CONFLICT"):
        recovered.record_fee("trade-1", "USDT", Decimal("-0.01"))


def test_cashflow_batch_failure_persists_no_partial_credit(tmp_path):
    ledger, _ = seeded(tmp_path)
    with pytest.raises(ValueError, match="ACCOUNT_EVENT_EXCEEDS_BALANCE"):
        ledger.record_cashflows_batch((
            ("102", "USDT", Decimal("2")),
            ("103", "USDT", Decimal("-20"))))
    restored = ReservationLedger.restore(tmp_path / "reservations.json", limits=LIMITS)
    assert restored.usdt_balance == Decimal("9.6")
    assert restored.record_cashflow("102", "USDT", Decimal("2"))


@pytest.mark.asyncio
async def test_complete_bills_apply_only_approved_transfer_and_survive_restart(tmp_path):
    ledger, wal = seeded(tmp_path, fee=Decimal("0"))
    connector = FakeOkx()
    connector.bill_pages[None] = [
        bill("102"),
        bill("101", kind="2", subtype="1", currency="LIFE", change="0.4",
             trade_id="trade-1", order_id="exchange-1", inst_id="LIFE-USDT"),
        bill("100")]
    approvals = CashflowApprovals.load(approval_file(tmp_path, approvals=[
        {"bill_id": "102", "currency": "USDT", "amount": "2"}]))
    reconciler = SpotBillReconciler(connector, ledger, wal, approvals)
    assert await reconciler.reconcile()
    assert ledger.usdt_balance == Decimal("11.6")
    recovered = ReservationLedger.restore(tmp_path / "reservations.json", limits=LIMITS)
    assert await SpotBillReconciler(connector, recovered, IntentWAL(tmp_path / "intents.json"),
                                    approvals).reconcile()
    assert recovered.usdt_balance == Decimal("11.6")


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [bill("102")],  # anchor outside the bounded history
    [bill("102", change="3"), bill("100")],  # amount differs from approval
    [bill("102"), bill("102"), bill("100")],  # duplicate bill ID
    [bill("102", kind="99"), bill("100")],  # unsupported activity
    [bill("102", kind="2", trade_id="manual", inst_id="LIFE-USDT"), bill("100")],
])
async def test_missing_or_untrusted_bill_history_does_not_change_balances(tmp_path, rows):
    ledger, wal = seeded(tmp_path, fee=Decimal("0"))
    connector = FakeOkx()
    connector.bill_pages[None] = rows
    approvals = CashflowApprovals.load(approval_file(tmp_path, approvals=[
        {"bill_id": "102", "currency": "USDT", "amount": "2"}]))
    assert not await SpotBillReconciler(connector, ledger, wal, approvals).reconcile()
    assert ledger.life_balance == Decimal("10.4")
    assert ledger.usdt_balance == Decimal("9.6")


@pytest.mark.asyncio
async def test_trade_bill_fee_must_match_recorded_fill_fee(tmp_path):
    ledger, wal = seeded(tmp_path, fee=Decimal("-0.01"))
    connector = FakeOkx()
    connector.bill_pages[None] = [
        bill("101", kind="2", subtype="1", currency="LIFE", change="0.39",
             trade_id="trade-1", order_id="exchange-1", inst_id="LIFE-USDT",
             fee="-0.02"),
        bill("100")]
    approvals = CashflowApprovals.load(approval_file(tmp_path))
    assert not await SpotBillReconciler(connector, ledger, wal, approvals).reconcile()
    connector.bill_pages[None][0]["fee"] = "-0.01"
    assert await SpotBillReconciler(connector, ledger, wal, approvals).reconcile()


def test_cashflow_approval_requires_anchor_and_unique_ids(tmp_path):
    with pytest.raises(ValueError, match="CASHFLOW_APPROVAL_INVALID"):
        CashflowApprovals.load(approval_file(tmp_path, anchor="bad"))
    with pytest.raises(ValueError, match="CASHFLOW_APPROVAL_INVALID"):
        CashflowApprovals.load(approval_file(tmp_path, approvals=[
            {"bill_id": "102", "currency": "USDT", "amount": "2"},
            {"bill_id": "102", "currency": "USDT", "amount": "2"}]))


@pytest.mark.asyncio
@pytest.mark.parametrize("fee_fields, complete", [
    ({"fee": "-0.01", "feeCcy": "LIFE"}, True),
    ({}, False),
    ({"fee": "-0.01", "feeCcy": "OTHER"}, False),
])
async def test_terminal_fill_requires_durable_fee_and_matching_balance(
        tmp_path, fee_fields, complete):
    ledger = ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                               limits=LIMITS, path=tmp_path / "reservations.json")
    assert ledger.reserve(SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"),
                                     "session", 1), reference_price=Decimal("1")).allowed
    wal = IntentWAL(tmp_path / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id="session", epoch=1,
                reservation_id="i1")
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1", **fee_fields}]
    connector.cash_balances = {"LIFE": "10.39", "USDT": "9.6"}
    reservations = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reservations.apply_fills, confirm_terminal=reservations.confirm_terminal,
        on_cancel_requested=reservations.request_cancel, on_unknown=reservations.mark_unknown,
        account_check=SpotAccountReconciler(connector, ledger).check, scope_check=full_scope)
    result = await gateway.reconcile("session", 1)
    assert result.trade_events_reconciled is complete
    assert (wal.get("i1").state == "TERMINAL") is complete
    assert ledger.life_balance == (Decimal("10.39") if complete else Decimal("10"))
    assert ReservationLedger.restore(tmp_path / "reservations.json", limits=LIMITS).life_balance == (
        Decimal("10.39") if complete else Decimal("10"))
