"""An ambiguous cashflow checkpoint must not clear a live runner send."""

import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_account_bills import bill
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import (
    _cashflows,
    _limits,
    _recovery_config,
)
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from unittest.mock import MagicMock, patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["before_write", "after_replace"])
async def test_runner_unknown_send_survives_cashflow_checkpoint_failure(tmp_path, failure_point):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    runner = _runner(controller, connector, [], proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task

    _cashflows(tmp_path, approved=[{"bill_id": "101", "currency": "USDT", "amount": "2"}])
    approvals = CashflowApprovals.load(tmp_path / "cashflows.json")
    session = wal.get("quote-1")
    loss = LossBudgetLedger(
        tmp_path / "loss_budget.json", campaign_id="life",
        campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
        session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=session.session_id, at_utc=AT)

    def attribution(ledger, *, create):
        return ReconciledFillAttributor(
            tmp_path / "fill_attribution.json", wal=wal, reservations=ledger,
            loss_budget=loss, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
            opening_independent_price_usdt=Decimal("1"),
            independent_value=lambda _: IndependentFillObservation(
                Decimal("1"), AT_MS, AT_MS, "independent_market"),
            max_reference_skew_ms=200, create=create, cashflow_approvals=approvals)

    first = attribution(reservations, create=True)
    connector.bill_pages[None] = [bill("101", change="2"), bill("100")]
    bills = SpotBillReconciler(
        connector, reservations, wal, approvals,
        on_cashflows_applied=first.apply_approved_cashflows)
    real_save = first._save

    def fail_checkpoint(events, cashflows):
        if failure_point == "after_replace":
            real_save(events, cashflows)
        raise OSError("attribution checkpoint failed")

    with patch.object(first, "_save", side_effect=fail_checkpoint):
        assert not await bills.reconcile()
    assert not first.ready()
    assert IntentWAL(wal.path).get("quote-1").state == "SEND_UNKNOWN"
    assert ReservationLedger.restore(reservations.path, limits=_limits()).has_open_intent("quote-1")

    restored_reservations = ReservationLedger.restore(reservations.path, limits=_limits())
    restored_attribution = attribution(restored_reservations, create=False)
    assert restored_attribution.ready() == (failure_point == "after_replace")
    assert await SpotBillReconciler(
        connector, restored_reservations, wal, approvals,
        on_cashflows_applied=restored_attribution.apply_approved_cashflows).reconcile()
    assert restored_attribution.ready()
    assert restored_attribution.capital().usdt_balance == Decimal("12")

    connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                 "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": wire_id, "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    connector.cash_balances = {"LIFE": "10", "USDT": "12"}
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    recovered = LifeLiquidityController(_recovery_config(tmp_path), provider, MagicMock())
    recovered_runner = _runner(recovered, connector, [], proposed.config)
    try:
        recovered_runner.tick(2)
        await recovered.order_safety_task
        assert IntentWAL(wal.path).get("quote-1").state != "TERMINAL"
        assert ReservationLedger.restore(reservations.path, limits=_limits()).requires_reconciliation("quote-1")
        assert len(connector.sent) == 1
    finally:
        recovered_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recovered_runner.listen_to_executor_actions_task
        recovered.stop()
