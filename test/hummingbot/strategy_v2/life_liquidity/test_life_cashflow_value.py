"""Synthetic independently valued LIFE transfers are capital, never execution PnL."""
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_cashflow_attribution import _approved, _bills
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation


def setup(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = replace(_approved(tmp_path, currency="LIFE"),
                        transfer_times_ms={"101": AT_MS + 100, "102": AT_MS})
    # Re-create the attribution policy with explicit transfer times and value provider.
    original, loss = _attributor(tmp_path, wal, reservations)
    original.path.unlink()
    values = {"101": Decimal("3"), "102": Decimal("2")}

    def observation(key):
        return IndependentFillObservation(values[key], approvals.transfer_times_ms[key],
                                          approvals.transfer_times_ms[key] + 1, "independent_market")
    attribution = type(original)(
        original.path, wal=wal, reservations=reservations, loss_budget=loss,
        opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"), independent_value=original.independent_value,
        max_reference_skew_ms=200, create=True, cashflow_approvals=approvals,
        cashflow_value=observation)
    bills = _bills(controller, reservations, wal, approvals, currency="LIFE",
                   callback=attribution.apply_approved_cashflows)
    for row in controller._order_safety_gateway.connector.bill_pages[None][:-1]:
        row["ts"] = str(approvals.transfer_times_ms[row["billId"]])
    return attribution, bills, observation


@pytest.mark.asyncio
async def test_transfer_time_values_preserve_physical_balance_and_adjusted_nav(tmp_path):
    attribution, bills, observation = setup(tmp_path)
    assert await bills.reconcile()
    assert attribution.ready()
    capital = attribution.capital()
    assert capital.life_balance == Decimal("11")
    assert capital.net_cashflows_quote == Decimal("1")  # +2 LIFE @2, -1 LIFE @3
    assert capital.execution_loss_quote == 0
    assert capital.measure(Decimal("3"), source_kind="independent_market").adjusted_nav_quote == 42
    assert await bills.reconcile()
    restored = type(attribution)(
        attribution.path, wal=attribution.wal, reservations=attribution.reservations,
        loss_budget=attribution.loss_budget, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"), independent_value=attribution.independent_value,
        max_reference_skew_ms=200, create=False, cashflow_approvals=attribution.cashflow_approvals,
        cashflow_value=lambda _: pytest.fail("restored transfer must use durable independent value"))
    assert restored.ready()
    assert restored.capital().net_cashflows_quote == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["model", "stale", "missing", "bill_time", "checkpoint"])
async def test_unqualified_or_interrupted_transfer_blocks_until_verified_replay(tmp_path, fault):
    attribution, bills, observation = setup(tmp_path)
    if fault == "bill_time":
        bills.connector.bill_pages[None][0]["ts"] = str(AT_MS + 9999)
    elif fault == "checkpoint":
        with patch.object(attribution, "_save", side_effect=OSError("disk")):
            assert not await bills.reconcile()
    else:
        attribution.cashflow_value = lambda key: (
            None if fault == "missing" else replace(observation(key), **(
                {"source_kind": "model"} if fault == "model" else {"value_at_ms": AT_MS - 1000})))
    assert not await bills.reconcile() if fault != "checkpoint" else not attribution.ready()
    if fault == "bill_time":
        bills.connector.bill_pages[None][0]["ts"] = str(AT_MS)
    attribution.cashflow_value = observation
    assert await bills.reconcile()
    assert attribution.ready()
    assert attribution.capital().net_cashflows_quote == 1


@pytest.mark.asyncio
async def test_life_transfer_valuation_controls_real_v2_queue_and_final_send(tmp_path):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.test_o2_revocation_replay import setup as runtime_setup
    from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT
    from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import _runner

    from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
    from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
    from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotAccountReconciler, SpotReservationReconciler
    from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
    c, connector, wal, ledger, _, _, _, quote, old, watchdog = await runtime_setup(tmp_path)
    runner = None
    try:
        planner = c._quote_action_planner
        assert planner.on_runner_action_rejected(CreateExecutorAction(controller_id="life", executor_config=old.config))
        wal.initialize_empty()
        session = c._order_safety_manager.current_session
        approvals = replace(_approved(tmp_path, currency="LIFE"),
                            transfer_times_ms={"102": AT_MS, "101": AT_MS + 100})
        loss = LossBudgetLedger(tmp_path / "loss.json", campaign_id="transfers",
                                campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                                session_limit_quote=Decimal("1"))
        loss.record("opening", Decimal("0"), session_id=session.session_id, at_utc=AT)
        value = {"ready": False}
        attribution = ReconciledFillAttributor(
            tmp_path / "transfers.json", wal=wal, reservations=ledger, loss_budget=loss,
            opening_life=Decimal("10"), opening_usdt=Decimal("10"), opening_independent_price_usdt=Decimal("1"),
            independent_value=lambda fill: IndependentFillObservation(
                Decimal("1"), fill.fill_at_ms, fill.fill_at_ms, "independent_market"),
            max_reference_skew_ms=200, create=True, cashflow_approvals=approvals,
            cashflow_value=lambda key: IndependentFillObservation(
                Decimal("2") if key == "102" else Decimal("3"), approvals.transfer_times_ms[key],
                approvals.transfer_times_ms[key], "independent_market" if value["ready"] else "model"))
        bills = _bills(c, ledger, wal, approvals, currency="LIFE")
        for row in connector.bill_pages[None][:-1]:
            row["ts"] = str(approvals.transfer_times_ms[row["billId"]])
        gateway = c._order_safety_gateway
        gateway.account_check = SpotAccountReconciler(connector, ledger, bills=bills).check
        gateway.apply_fills = SpotReservationReconciler(wal, ledger, require_fees=True).apply_fills
        c.install_execution_loss_budget(loss, utc_clock=lambda: AT)
        c.install_fill_attributor(attribution)
        assert not await bills.reconcile()
        assert not c.allow_create_executor_actions()
        assert c.determine_executor_actions() == []
        value["ready"] = True
        assert await bills.reconcile()
        connector.cash_balances = {"LIFE": "11", "USDT": "10"}
        planner.snapshot = lambda: replace(quote["snapshot"], loss_budget_status=loss.verified_status(
            session_id=session.session_id, at_utc=AT))
        planner.intent_id_factory = lambda: "after-transfer"
        actions = c.determine_executor_actions()
        assert len(actions) == 1
        actual = []
        runner = _runner(c, connector, actual, actions[0].executor_config)
        runner.tick(1)
        assert len(actual) == 1
        executor = actual[0]
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
                "side": executor.config.side.name.lower(), "ordType": "post_only", "tdMode": "cash",
                "px": str(executor.config.price), "sz": str(executor.config.amount)}
        connector.sent[0]["pre_send_check"](wire)
        assert attribution.capital().net_cashflows_quote == 1
        attribution.path.unlink()
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert not ledger.is_terminal_intent(executor.config.id)
    finally:
        watchdog.cancel()
        if runner is not None:
            runner.listen_to_executor_actions_task.cancel()
            await asyncio.gather(runner.listen_to_executor_actions_task, return_exceptions=True)
