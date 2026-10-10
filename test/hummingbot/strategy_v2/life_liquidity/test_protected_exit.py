"""Synthetic bounded exits cross the real V2 and protected executor boundaries."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_o2_revocation_replay import setup
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import _runner

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.exit_actions import ExitObservation, SpotExitPlanner
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

D = Decimal


async def exit_setup(tmp_path, *, objective="profit_mm"):
    controller, connector, wal, ledger, feed, risk, gate, quote, old, watchdog = await setup(tmp_path)
    planner = controller._quote_action_planner
    assert planner.on_runner_action_rejected(CreateExecutorAction(controller_id="life", executor_config=old.config))
    # Positive spread, but deliberately negative quote edge; exits have their own limits.
    original_snapshot = planner.snapshot()
    planner.snapshot = lambda: replace(original_snapshot,
                                       costs=QuoteCosts(D("1"), D("1"), D("0"), D("0"), D("0"), D("0")))
    budget = SubsidyBudgetLedger(tmp_path / "exit_budget.json", campaign_id="exit-only",
                                 campaign_limit_quote=D("1"), day_limit_quote=D("1"),
                                 session_limit_quote=D("1"))
    budget.initialize_empty()
    now = {"ms": 2000000}
    snapshot = {"value": ExitObservation(2000000, D("1"), D("0.99"), D("0.98"),
                                         D("5"), D("0.001"), "independent_market",
                                         InstrumentRules(D("0.01"), D("0.1"), D("0.1")))}
    from datetime import datetime, timezone

    from hummingbot.strategy_v2.life_liquidity.config import SubsidyBudgetConfig
    from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import (
        IndependentFillObservation,
        ReconciledFillAttributor,
    )
    from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
    from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotAccountReconciler, SpotReservationReconciler
    at = datetime.fromtimestamp(2000, tz=timezone.utc)
    subsidy = None
    if objective == "liquidity_service":
        limits = SubsidyBudgetConfig(campaign=D("1"), day=D("1"), session=D("1"))
        economics = controller.config.strategy.economics.model_copy(update={
            "objective": objective, "subsidy_budget_quote": limits})
        controller.config = controller.config.model_copy(update={"strategy":
                                                                 controller.config.strategy.model_copy(update={"economics": economics})})
        subsidy = SubsidyBudgetLedger(tmp_path / "service.json", campaign_id="service-only",
                                      campaign_limit_quote=D("1"), day_limit_quote=D("1"),
                                      session_limit_quote=D("1"))
        subsidy.initialize_empty()
        planner.subsidy_budget = subsidy
        planner.subsidy_utc_clock = lambda: at
        planner.snapshot = lambda: replace(original_snapshot, policy=EconomicPolicy(objective, D("0")),
                                           costs=QuoteCosts(D("1"), D("1"), D("0"), D("0"), D("0"), D("0")), subsidy_remaining_quote=D("1"))
    exits = SpotExitPlanner(controller, tmp_path / "exits.json", budget=budget,
                            observation=lambda: snapshot["value"], utc_clock_ms=lambda: now["ms"],
                            max_age_ms=1000, max_slippage_bps=D("200"), target_base=D("9"),
                            create=True)
    controller.install_spot_exit_planner(exits)
    wal.initialize_empty()
    session = controller._order_safety_manager.current_session
    loss = LossBudgetLedger(tmp_path / "loss.json", campaign_id="synthetic-exit",
                            campaign_limit_quote=D("1"), day_limit_quote=D("1"), session_limit_quote=D("1"))
    loss.record("opening", D("0"), session_id=session.session_id, at_utc=at)
    attribution = ReconciledFillAttributor(
        tmp_path / "attribution.json", wal=wal, reservations=ledger, loss_budget=loss,
        opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
        independent_value=lambda fill: IndependentFillObservation(D("1"), fill.fill_at_ms,
                                                                  fill.fill_at_ms, "independent_market"),
        max_reference_skew_ms=1, create=True, subsidy_budget=subsidy, exit_budget=budget)
    gateway = controller._order_safety_gateway
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway.apply_fills = reconciler.apply_fills
    gateway.confirm_terminal = reconciler.confirm_terminal
    gateway.on_cancel_requested = reconciler.request_cancel
    gateway.on_unknown = reconciler.mark_unknown
    gateway.account_check = SpotAccountReconciler(connector, ledger).check
    controller.install_execution_loss_budget(loss, utc_clock=lambda: at)
    controller.install_fill_attributor(attribution)
    return controller, connector, wal, ledger, gate, old, watchdog, exits, snapshot, now


@pytest.mark.asyncio
@pytest.mark.parametrize("objective", ["profit_mm", "liquidity_service"])
async def test_negative_profit_edge_exit_is_bounded_and_final_send_rechecks(tmp_path, objective):
    c, connector, wal, ledger, gate, old, watchdog, exits, snapshot, now = await exit_setup(tmp_path, objective=objective)
    try:
        assert c._quote_action_planner.propose() == []
        actions = await exits.propose("exit-1", D("0.5"))
        assert len(actions) == 1, exits.residual_reason_code
        executors = []
        runner = _runner(c, connector, executors, actions[0].executor_config)
        assert StrategyV2Base._filter_authorized_actions(runner, actions) == actions
        runner.tick(1)
        assert len(executors) == 1
        executor = executors[0]
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT", "side": "sell",
                "ordType": "post_only", "tdMode": "cash", "px": "0.99", "sz": "0.5"}
        connector.sent[0]["pre_send_check"](wire)
        assert exits.budget.campaign_committed_quote == D("0.005495")
        original_config = c.config
        c.config = c.config.model_copy(update={"recovery_account_uid": "99999"})
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        c.config = original_config
        connector.sent[0]["pre_send_check"](wire)
        snapshot["value"] = replace(snapshot["value"], depth_base=D("0"))
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert ledger.has_open_intent("exit-1")
        assert exits.residual_reason_code == "EXIT_DEPTH_UNAVAILABLE"
        with pytest.raises(PermissionError):
            executor.place_open_order()
    finally:
        watchdog.cancel()
        if "runner" in locals():
            runner.listen_to_executor_actions_task.cancel()
            await asyncio.gather(runner.listen_to_executor_actions_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["reverse", "slippage", "halt", "fee", "pending", "budget", "foreign"])
async def test_exit_failure_reports_residual_and_never_sends(tmp_path, fault):
    c, connector, wal, ledger, gate, old, watchdog, exits, snapshot, now = await exit_setup(tmp_path)
    try:
        amount = D("2") if fault == "reverse" else D("0.5")
        if fault == "slippage":
            snapshot["value"] = replace(snapshot["value"], limit_price_usdt=D("0.8"))
        elif fault == "halt":
            gate.halt("STOP")
        elif fault == "fee":
            snapshot["value"] = replace(snapshot["value"], fee_rate=D("NaN"))
        elif fault == "pending":
            from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
            session = c._order_safety_manager.current_session
            ledger.reserve(SpotIntent("competing", "SELL", D("1"), D("1"), session.session_id,
                                      session.epoch), reference_price=D("1"))
        elif fault == "budget":
            session = c._order_safety_manager.current_session
            exits.budget.reserve("old", D("1"), session_id=session.session_id,
                                 at_utc=c._order_safety_manager.wall_clock())
        elif fault == "foreign":
            connector.algo_pages["trigger"] = {"code": "0", "data": [{"instId": "OTHER-USDT", "algoId": "foreign"}]}
        assert await exits.propose("exit-1", amount) == []
        assert exits.residual_reason_code != "EXIT_READY"
        assert connector.sent == []
    finally:
        watchdog.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("objective", ["profit_mm", "liquidity_service"])
async def test_partial_exit_fee_floor_stays_held_until_terminal_account_proof_and_restart(tmp_path, objective):
    c, connector, wal, ledger, gate, old, watchdog, exits, snapshot, now = await exit_setup(tmp_path, objective=objective)
    attribution = c._fill_attributor
    try:
        actions = await exits.propose("exit-1", D("0.5"))
        assert len(actions) == 1, exits.residual_reason_code
        actual = OrderExecutor(old._strategy, actions[0].executor_config)
        actual.get_order_price = lambda: actual.config.price
        actual.place_open_order()
        wire_id = actual._order.order_id
        connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-exit",
                                     "state": "partially_filled", "accFillSz": "0.2"}
        connector.fills["exchange-exit"] = [{"tradeId": "exit-fill", "ordId": "exchange-exit",
                                             "fillSz": "0.2", "fillPx": "0.99", "feeCcy": "USDT", "fee": "-0.01", "fillTime": "2000000"}]
        connector.cash_balances = {"LIFE": "9.8", "USDT": "10.188"}
        assert not await exits.reconcile_budget()
        assert attribution.ready()
        assert exits.budget.campaign_committed_quote == D("0.012")
        if objective == "liquidity_service":
            assert attribution.subsidy_budget.campaign_committed_quote == 0
        assert ledger.has_open_intent("exit-1")
        connector.status[wire_id]["state"] = "canceled"
        connector.algo_pages["trigger"] = {"code": "0", "data": [{"algoId": "foreign"}]}
        assert not await exits.reconcile_budget()
        assert ledger.has_open_intent("exit-1")
        connector.algo_pages.clear()
        assert await exits.reconcile_budget(), exits.residual_reason_code
        assert ledger.is_terminal_intent("exit-1")
        assert exits.budget.campaign_committed_quote == D("0.012")
        restored = SpotExitPlanner(c, exits.journal.path, budget=SubsidyBudgetLedger(
            exits.budget.path, campaign_id="exit-only", campaign_limit_quote=D("1"),
            day_limit_quote=D("1"), session_limit_quote=D("1")), observation=exits.observation,
            utc_clock_ms=exits.utc_clock_ms, max_age_ms=1000, max_slippage_bps=D("200"),
            target_base=D("9"), create=False)
        assert not restored.handles(actual.config)
        assert await restored.reconcile_budget()
        assert restored.budget.campaign_committed_quote == D("0.012")
    finally:
        watchdog.cancel()


def test_over_limit_inventory_can_be_reduced_without_relaxing_new_risk_limits(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
    ledger = ReservationLedger(life_balance=D("10"), usdt_balance=D("10"),
                               limits=RiskLimits(D("0"), D("5"), D("5"), D("5")),
                               path=tmp_path / "reserve.json")
    intent = SpotIntent("exit", "SELL", D("1"), D("1"), "session", 1)
    assert not ledger.reserve(intent, reference_price=D("1")).allowed
    assert ledger.reserve_risk_reduction(intent, target_base=D("0")).allowed
    assert not ledger.reserve_risk_reduction(
        SpotIntent("another", "SELL", D("10"), D("1"), "session", 1), target_base=D("0")).allowed
    assert ledger.reserved_life == 1
