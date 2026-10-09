"""Approved spot transfers must reconcile into capital exactly once."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_account_bills import approval_file, bill, spot_fill
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    SpotAccountReconciler,
    SpotFill,
    SpotReservationReconciler,
)


def _approved(tmp_path, *, currency="USDT"):
    return CashflowApprovals.load(approval_file(tmp_path, approvals=[
        {"bill_id": "102", "currency": currency, "amount": "2"},
        {"bill_id": "101", "currency": currency, "amount": "-1"}]))


def _bills(controller, reservations, wal, approvals, *, currency="USDT", callback=None):
    connector = controller._order_safety_gateway.connector
    connector.bill_pages[None] = [
        bill("102", currency=currency, change="2"),
        bill("101", subtype="12", currency=currency, change="-1"),
        bill("100")]
    return SpotBillReconciler(connector, reservations, wal, approvals,
                              on_cashflows_applied=callback)


@pytest.mark.asyncio
async def test_approved_deposit_withdrawal_survives_restart_and_repeated_bill_scan(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals,
                   callback=attribution.apply_approved_cashflows)

    assert await bills.reconcile()
    assert attribution.ready()
    assert reservations.usdt_balance == attribution.capital().usdt_balance == Decimal("11")
    assert attribution.capital().net_cashflows_quote == Decimal("1")
    assert attribution.capital().measure(
        Decimal("1"), source_kind="independent_market").adjusted_nav_quote == Decimal("20")
    assert await bills.reconcile()
    assert attribution.capital().net_cashflows_quote == Decimal("1")

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored, _ = _attributor(tmp_path, wal, restored_reservations,
                              restore=True, approvals=approvals)
    assert restored.ready()
    assert restored.capital().net_cashflows_quote == Decimal("1")


@pytest.mark.asyncio
async def test_cashflow_journal_failure_blocks_account_reconciliation_until_replay(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals,
                   callback=attribution.apply_approved_cashflows)

    with patch.object(attribution, "_save", side_effect=OSError("attribution write failed")):
        assert not await bills.reconcile()
    assert reservations.usdt_balance == Decimal("11")
    assert not attribution.ready()

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored, _ = _attributor(tmp_path, wal, restored_reservations,
                              restore=True, approvals=approvals)
    restored_bills = _bills(controller, restored_reservations, wal, approvals,
                            callback=restored.apply_approved_cashflows)
    assert await restored_bills.reconcile()
    assert restored.ready()


@pytest.mark.asyncio
async def test_cashflow_post_replace_error_restores_committed_transfer_once(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals,
                   callback=attribution.apply_approved_cashflows)
    real_save = attribution._save

    def write_then_fail(events, cashflows):
        real_save(events, cashflows)
        raise OSError("directory sync ambiguous")

    with patch.object(attribution, "_save", side_effect=write_then_fail):
        assert not await bills.reconcile()
    assert not attribution.ready()

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored, _ = _attributor(tmp_path, wal, restored_reservations,
                              restore=True, approvals=approvals)
    assert restored.ready()
    assert restored.capital().net_cashflows_quote == Decimal("1")
    restored_bills = _bills(controller, restored_reservations, wal, approvals,
                            callback=restored.apply_approved_cashflows)
    assert await restored_bills.reconcile()
    assert restored.capital().net_cashflows_quote == Decimal("1")


@pytest.mark.asyncio
async def test_life_cashflow_without_independent_transfer_value_remains_blocked(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path, currency="LIFE")
    attribution, _ = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals, currency="LIFE",
                   callback=attribution.apply_approved_cashflows)

    assert not await bills.reconcile()
    assert reservations.life_balance == Decimal("11")
    assert not attribution.ready()


@pytest.mark.asyncio
async def test_controller_install_binds_bill_reconciliation_to_attribution(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals)
    controller._order_safety_gateway.account_check = SpotAccountReconciler(
        controller._order_safety_gateway.connector, reservations, bills=bills).check
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)

    assert await bills.reconcile()
    assert attribution.ready()


@pytest.mark.asyncio
async def test_partial_fill_fee_and_approved_cashflows_replay_together(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations, approvals=approvals)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert not attribution.apply("wire-1", (fill,))  # Approved bills have not been scanned yet.
    assert not attribution.ready()
    wal.acknowledge("i1", "exchange-1")
    bills = _bills(controller, reservations, wal, approvals,
                   callback=attribution.apply_approved_cashflows)
    connector = controller._order_safety_gateway.connector
    connector.bill_pages[None].insert(0, bill(
        "103", kind="2", subtype="1", currency="USDT", change="-0.41",
        trade_id="trade-1", order_id="exchange-1", inst_id="LIFE-USDT", fee="-0.01"))
    history = spot_fill("103", "trade-1", "exchange-1")
    history.update(feeCcy="USDT", fee="-0.01")
    connector.all_fill_history_pages[None] = [history]

    assert await bills.reconcile()
    assert attribution.ready()
    assert attribution.capital().life_balance == Decimal("10.4")
    assert attribution.capital().usdt_balance == Decimal("10.59")
    assert attribution.capital().net_cashflows_quote == Decimal("1")
    assert loss.verified_status(session_id=wal.get("i1").session_id,
                                at_utc=AT).session_loss_quote == Decimal("0.05")


@pytest.mark.asyncio
async def test_gateway_retries_fill_after_bill_cashflow_catches_up(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations, approvals=approvals)
    bills = _bills(controller, reservations, wal, approvals)
    connector = controller._order_safety_gateway.connector
    connector.bill_pages[None].insert(0, bill(
        "103", kind="2", subtype="1", currency="USDT", change="-0.41",
        trade_id="trade-1", order_id="exchange-1", inst_id="LIFE-USDT", fee="-0.01"))
    history = spot_fill("103", "trade-1", "exchange-1")
    history.update(feeCcy="USDT", fee="-0.01")
    connector.all_fill_history_pages[None] = [history]
    connector.status["wire-1"] = {
        "clOrdId": "wire-1", "ordId": "exchange-1", "state": "partially_filled",
        "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{
        "tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
        "fillPx": "1", "feeCcy": "USDT", "fee": "-0.01", "fillTime": str(AT_MS)}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "10.59"}
    controller._order_safety_gateway.account_check = SpotAccountReconciler(
        connector, reservations, bills=bills).check
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    session = controller._order_safety_manager.current_session

    first = await controller._order_safety_gateway.reconcile(session.session_id, session.epoch)
    assert not first.trade_events_reconciled
    assert attribution.ready()
    second = await controller._order_safety_gateway.reconcile(session.session_id, session.epoch)
    assert second.trade_events_reconciled
    assert attribution.capital().net_cashflows_quote == Decimal("1")


def test_changed_cashflow_approval_policy_cannot_restore_journal(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    approvals = _approved(tmp_path)
    _attributor(tmp_path, wal, reservations, approvals=approvals)
    changed = CashflowApprovals(approvals.anchor_bill_id, {
        **approvals.approved, "103": ("USDT", Decimal("1"))})

    with pytest.raises(ValueError, match="FILL_ATTRIBUTION_JOURNAL_INVALID"):
        _attributor(tmp_path, wal, reservations, restore=True, approvals=changed)
