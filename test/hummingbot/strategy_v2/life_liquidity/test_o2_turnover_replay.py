"""Synthetic A29 turnover invariance through actual V2 sends and service settlement."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from itertools import count
from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
from test.hummingbot.strategy_v2.life_liquidity.test_capital_risk_binding import _nav
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from test.hummingbot.strategy_v2.life_liquidity.test_subsidy_runtime_binding import NOW, NOW_MS, _service
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.models.base import RunnableStatus

D = Decimal


async def replay(directory, cycles):
    directory.mkdir()
    connector = FakeTradingOkx()
    controller, _, _, wal, reservations, subsidy, state = _service(directory, connector=connector)
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    await install_market(controller, connector, exchange_ms=NOW_MS)
    session = controller._order_safety_manager.current_session
    loss = LossBudgetLedger(directory / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=D("1"), day_limit_quote=D("1"),
                            session_limit_quote=D("1"))
    loss.record("opening", D("0"), session_id=session.session_id, at_utc=NOW)
    attribution = ReconciledFillAttributor(
        directory / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, subsidy_budget=subsidy, opening_life=D("10"), opening_usdt=D("10"),
        opening_independent_price_usdt=D("1"),
        independent_value=lambda fill: IndependentFillObservation(
            D("1"), fill.fill_at_ms, fill.fill_at_ms, "independent_market"),
        max_reference_skew_ms=200, create=True)
    reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, reservations).check)
    controller.install_order_safety(controller._order_safety_manager, gateway, wal,
                                    reservations=reservations)
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    controller.install_execution_loss_budget(loss, utc_clock=lambda: NOW)
    controller.install_fill_attributor(attribution)
    safety = SafetyGate(directory / "safety.json", max_drawdown_bps=D("4"),
                        min_margin_buffer_quote=D("0"), stable_data_ms=0,
                        recovery_probe_base=D("1"))
    safety.initialize_empty()
    controller.install_runtime_risk_gate(
        safety, observation=lambda: SafetyObservation(100, True, True, True, True, D("0"), D("100")),
        monotonic_clock_ms=lambda: 100, max_observation_age_ms=5)
    capital = CapitalRiskMonitor(
        directory / "capital_risk.json", attributor=attribution,
        independent_value=lambda: _nav("1", value_at=NOW_MS, observed_at=NOW_MS),
        utc_clock_ms=lambda: NOW_MS, max_value_age_ms=500, create=True)
    controller.install_capital_risk_monitor(capital)
    assert not controller.allow_create_executor_actions()
    assert controller.allow_create_executor_actions()
    planner = controller._quote_action_planner
    ids = count()
    planner.intent_id_factory = lambda: f"quote-{next(ids):04d}"
    planner.max_actions_per_tick = 2
    planner.snapshot = lambda: replace(
        state["snapshot"], subsidy_remaining_quote=subsidy.verified_status(
            session_id=session.session_id, at_utc=NOW).available_quote,
        loss_budget_status=loss.verified_status(session_id=session.session_id, at_utc=NOW))

    def final_check(request):
        request["pre_send_check"]({
            "clOrdId": request["order_id"], "instId": "LIFE-USDT",
            "side": request["trade_type"].name.lower(), "ordType": "post_only", "tdMode": "cash",
            "px": str(request["price"]), "sz": str(request["amount"])})

    connector.before_send = final_check
    executors = []
    actions = controller.determine_executor_actions()
    assert len(actions) == 2
    runner = _runner(controller, connector, executors, actions[0].executor_config)
    try:
        runner.determine_executor_actions = lambda: list(actions)
        runner.tick(1)
        assert len(connector.sent) == 2
        members = []
        # Same-size partial fill pairs give 100x turnover without repeatedly
        # rescanning 200 separate historical orders. The final pair alone loses
        # 0.01 USDT; all preceding pairs have zero net economic loss.
        for executor in executors:
            executor._status = RunnableStatus.TERMINATED
            wire = executor._order.order_id
            exchange = f"exchange-{executor.config.id}"
            members.append(executor.config.id)
            connector.status[wire] = {"clOrdId": wire, "ordId": exchange,
                                      "state": "canceled", "accFillSz": str(D("0.01") * cycles)}
            connector.fills[exchange] = [{
                "tradeId": f"trade-{executor.config.id}-{index:03d}", "ordId": exchange,
                "fillSz": "0.01", "fillPx": str(executor.config.price),
                "feeCcy": "USDT", "fee": "-0.0051" if index == cycles - 1 else "-0.0001",
                "fillTime": str(NOW_MS + index)} for index in range(cycles)]
        connector.cash_balances = {"LIFE": "10", "USDT": "9.99"}
        controller._runner_halt_ok = controller._halt_runner_orders()
        assert controller._runner_halt_ok
        await gateway.reconcile(session.session_id, session.epoch)
        complete = await gateway.reconcile(session.session_id, session.epoch)
        assert complete.scope_complete and complete.trade_events_reconciled
        assert not complete.open_order_ids and not complete.unknown_order_ids
        assert await controller.settle_service_inventory("closed-cycle", tuple(members))
        assert attribution.ready()
        measured = capital.measure()
        assert measured is not None
        assert not controller.allow_create_executor_actions()
        assert safety.state == "HALTED"
        return (measured, loss.verified_status(session_id=session.session_id, at_utc=NOW),
                subsidy.campaign_committed_quote, reservations.preview().filled_base_total)
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_100x_actual_runner_turnover_preserves_drawdown_loss_and_subsidy(tmp_path):
    low = await replay(tmp_path / "one", 1)
    high = await replay(tmp_path / "hundred", 100)
    assert low[0].adjusted_nav_quote == high[0].adjusted_nav_quote == D("19.99")
    assert low[0].drawdown_bps == high[0].drawdown_bps == D("5")
    assert low[1].campaign_loss_quote == high[1].campaign_loss_quote == D("0.01")
    assert low[2] == high[2] == D("0.01")
    assert high[3] == low[3] * 100
