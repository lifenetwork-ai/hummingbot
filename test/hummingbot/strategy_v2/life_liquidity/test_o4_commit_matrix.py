"""Cross-journal runner interruptions around SQLite/action/cashflow commits.

All amounts, fees, clocks and exchange responses are synthetic. Exceptions model
process interruption on either side of a commit; separate child-process tests
exercise the actual SQLite commit boundaries.
"""

import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
from test.hummingbot.strategy_v2.life_liquidity.test_account_bills import bill
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import (
    _cashflows,
    _limits,
    _recovery_config,
)
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder
from test.hummingbot.strategy_v2.life_liquidity.test_runtime_risk_binding import _install, _observation
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction


async def stop_runner(runner, controller):
    runner.listen_to_executor_actions_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner.listen_to_executor_actions_task
    controller.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", ["before_write", "after_replace"])
@pytest.mark.parametrize("recorder_boundary", ["before_commit", "after_commit"])
@pytest.mark.parametrize("cashflow", ["before_write", "after_replace"])
@pytest.mark.parametrize("retirement", ["before_write", "after_replace"])
async def test_unknown_send_survives_combined_commit_interruptions(
        tmp_path, monkeypatch, dispatch, recorder_boundary, cashflow, retirement):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    db_path = tmp_path / "executors.sqlite"
    recorder = _recorder(db_path)
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", recorder)
    connector = FakeTradingOkx()
    first, template, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(first, template, wal, ledger)
    _install(first, tmp_path, {"now": 100, "observation": _observation(100)})
    first.order_safety_watchdog_task = asyncio.get_running_loop().create_future()
    await install_market(first, connector, exchange_ms=AT_MS)
    journal = first._quote_action_planner.action_journal
    original_save = journal._save

    def interrupted_dispatch(records):
        assert records["quote-1"].state == "DISPATCHED"
        if dispatch == "after_replace":
            original_save(records)
        raise OSError("action dispatch interrupted")

    executors = []
    runner = _runner(first, connector, executors, proposed.config)
    try:
        with patch.object(journal, "_save", side_effect=interrupted_dispatch):
            runner.tick(1)
        assert len(connector.sent) == 1
        assert journal.get("quote-1").state == "PROPOSED"
        wire = connector.sent[0]["order_id"]
        executor = executors[0]
        executor._status = RunnableStatus.TERMINATED
        executor.config = executor.config.model_copy(update={"timestamp": 1.0})
        orchestrator = ExecutorOrchestrator(strategy=runner)
        orchestrator.active_executors["life"] = [executor]

        def interrupt_commit(_session):
            raise OSError("recorder commit interrupted")

        event.listen(Session, recorder_boundary, interrupt_commit)
        try:
            orchestrator.store_executor(StoreExecutorAction(executor_id="quote-1", controller_id="life"))
            assert orchestrator.active_executors["life"] == [executor]
        finally:
            event.remove(Session, recorder_boundary, interrupt_commit)
    finally:
        await stop_runner(runner, first)
    reopened = _recorder(db_path)
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", reopened)
    assert [row.id for row in reopened.get_executors_by_controller("life")] == (
        ["quote-1"] if recorder_boundary == "after_commit" else [])
    assert IntentWAL(wal.path).get("quote-1").state == "SEND_UNKNOWN"
    assert ReservationLedger.restore(ledger.path, limits=_limits()).has_open_intent("quote-1")
    durable_claim = QuoteActionJournal(journal.path, account_uid="12345").get("quote-1")
    assert durable_claim.state == ("DISPATCHED" if dispatch == "after_replace" else "PROPOSED")

    _cashflows(tmp_path, approved=[{"bill_id": "101", "currency": "USDT", "amount": "2"}])
    approvals = CashflowApprovals.load(tmp_path / "cashflows.json")
    loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                            session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=wal.get("quote-1").session_id, at_utc=AT)

    def attributor(reservations, *, create):
        return ReconciledFillAttributor(
            tmp_path / "fill_attribution.json", wal=IntentWAL(wal.path), reservations=reservations, loss_budget=loss,
            opening_life=Decimal("10"), opening_usdt=Decimal("10"),
            opening_independent_price_usdt=Decimal("1"),
            independent_value=lambda _: IndependentFillObservation(Decimal("1"), AT_MS, AT_MS, "independent_market"),
            max_reference_skew_ms=200, create=create, cashflow_approvals=approvals)

    attribution = attributor(ledger, create=True)
    original_attribution_save = attribution._save

    def interrupted_cashflow(events, cashflows):
        if cashflow == "after_replace":
            original_attribution_save(events, cashflows)
        raise OSError("cashflow attribution interrupted")

    connector.bill_pages[None] = [bill("101", change="2"), bill("100")]
    with patch.object(attribution, "_save", side_effect=interrupted_cashflow):
        assert not await SpotBillReconciler(
            connector, ledger, wal, approvals, on_cashflows_applied=attribution.apply_approved_cashflows).reconcile()
    assert not attribution.ready()
    restored_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
    restored_attribution = attributor(restored_ledger, create=False)
    assert await SpotBillReconciler(
        connector, restored_ledger, wal, approvals,
        on_cashflows_applied=restored_attribution.apply_approved_cashflows).reconcile()
    assert restored_attribution.ready()
    assert restored_attribution.capital().usdt_balance == Decimal("12")
    connector.cash_balances["USDT"] = "12"
    connector.status[wire] = {"clOrdId": wire, "ordId": "exchange-1", "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{**connector.status[wire], "instId": "LIFE-USDT"}]
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    config = _recovery_config(tmp_path).model_copy(update={"require_quote_action_journal": True})
    recovered = LifeLiquidityController(config, provider, MagicMock())
    restored_runner = _runner(recovered, connector, [], proposed.config)
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    try:
        restored_runner.tick(2)
        await recovered.order_safety_task
        assert recovered.order_safety_reason_code == "OLD_ORDERS_UNRESOLVED"
        assert ReservationLedger.restore(ledger.path, limits=_limits()).requires_reconciliation("quote-1")
        # A canceled status alone cannot release capacity while account scope is foreign.
        connector.status[wire]["state"] = "canceled"
        connector.open_pages[None] = []
        connector.algo_pages["trigger"] = {"code": "0", "data": [{"algoId": "foreign"}]}
        restored_runner.tick(3)
        await recovered.order_safety_task
        assert not ReservationLedger.restore(ledger.path, limits=_limits()).is_terminal_intent("quote-1")
        connector.algo_pages.clear()
        restored_runner.tick(4)
        await recovered.order_safety_task
        assert IntentWAL(wal.path).get("quote-1").state == "TERMINAL"
        assert ReservationLedger.restore(ledger.path, limits=_limits()).is_terminal_intent("quote-1")
        assert len(connector.sent) == 1
    finally:
        await stop_runner(restored_runner, recovered)

    real_action_save = QuoteActionJournal._save

    def interrupted_retirement(self, records):
        assert records["quote-1"].state == "RECONCILED"
        if retirement == "after_replace":
            real_action_save(self, records)
        raise OSError("action retirement interrupted")

    with patch.object(QuoteActionJournal, "_save", new=interrupted_retirement):
        interrupted = LifeLiquidityController(config, provider, MagicMock())
        interrupted._restore_order_safety()
        assert not interrupted._quote_action_recovery_ready
        interrupted.stop()
    final = LifeLiquidityController(config, provider, MagicMock())
    try:
        final._restore_order_safety()
        assert final._quote_action_recovery_ready
        assert QuoteActionJournal(journal.path, account_uid="12345").get("quote-1").state == "RECONCILED"
        final_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
        final_attribution = attributor(final_ledger, create=False)
        assert final_attribution.ready()
        assert final_attribution.capital().usdt_balance == Decimal("12")
        assert final_ledger.usdt_balance == Decimal("12")
        assert len(connector.sent) == 1
    finally:
        final.stop()
