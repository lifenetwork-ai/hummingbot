"""Synthetic O.2 paths through the actual V2 queue and protected spot send."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
from test.hummingbot.strategy_v2.life_liquidity.test_capital_risk_binding import _nav
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_fee_quote_binding import _binding, _costs, _fee
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_markout_runtime_binding import _horizon, _monitor
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from test.hummingbot.strategy_v2.life_liquidity.test_subsidy_runtime_binding import NOW, NOW_MS, _service
from types import SimpleNamespace

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.markout_risk import MarkoutProbeGuard
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
async def test_service_budget_change_rejects_already_queued_v2_action(tmp_path):
    connector = FakeTradingOkx()
    controller, _, _, wal, _, subsidy, quote_state = _service(tmp_path, connector=connector)
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    await install_market(controller, connector, exchange_ms=NOW_MS)
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    session = controller._order_safety_manager.current_session
    loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                            session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=session.session_id, at_utc=NOW)
    attribution = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal,
        reservations=controller._order_safety_reservations,
        loss_budget=loss, subsidy_budget=subsidy,
        opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"),
        independent_value=lambda _: IndependentFillObservation(
            Decimal("0.9"), NOW_MS, NOW_MS + 100, "independent_market"),
        max_reference_skew_ms=200, create=True)
    controller._order_safety_gateway.apply_fills = SpotReservationReconciler(
        wal, controller._order_safety_reservations, require_fees=True).apply_fills
    controller.install_execution_loss_budget(loss, utc_clock=lambda: NOW)
    controller.install_fill_attributor(attribution)
    assert controller.allow_create_executor_actions()
    quote_state["snapshot"] = replace(
        quote_state["snapshot"], loss_budget_status=controller.execution_loss_status)
    actions = controller.determine_executor_actions()
    assert len(actions) == 1
    queued = actions[0]
    assert subsidy.reserve("external-cost", Decimal("0.04"),
                           session_id=session.session_id, at_utc=NOW)
    assert quote_state["snapshot"].subsidy_remaining_quote == Decimal("0.05")
    executors = []
    runner = _runner(controller, connector, executors, queued.executor_config)
    try:
        assert controller.allow_create_executor_actions()
        assert StrategyV2Base._filter_authorized_actions(runner, [queued]) == []
        assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_SNAPSHOT_MISMATCH"
        runner.tick(1)
        assert connector.sent == []
        assert executors == []
        assert controller._quote_action_planner.action_journal.verified_get(
            queued.executor_config.id).state == "REJECTED"
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_service_budget_fill_and_halt_share_actual_v2_runner_gate(tmp_path):
    connector = FakeTradingOkx()
    controller, _, _, wal, reservations, subsidy, quote_state = _service(
        tmp_path, connector=connector)
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    await install_market(controller, connector, exchange_ms=NOW_MS)
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    session = controller._order_safety_manager.current_session
    loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                            session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=session.session_id, at_utc=NOW)
    attribution = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, subsidy_budget=subsidy,
        opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"),
        independent_value=lambda _: IndependentFillObservation(
            Decimal("0.9"), NOW_MS, NOW_MS + 100, "independent_market"),
        max_reference_skew_ms=200, create=True)
    controller._order_safety_gateway.apply_fills = SpotReservationReconciler(
        wal, reservations, require_fees=True).apply_fills
    controller.install_execution_loss_budget(loss, utc_clock=lambda: NOW)
    controller.install_fill_attributor(attribution)
    observations = {
        "now": NOW_MS, "risk_now": 100,
        "nav": _nav("1", value_at=NOW_MS, observed_at=NOW_MS),
    }
    capital = CapitalRiskMonitor(
        tmp_path / "capital_risk.json", attributor=attribution,
        independent_value=lambda: observations["nav"],
        utc_clock_ms=lambda: observations["now"], max_value_age_ms=500, create=True)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    controller.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(
            observations["risk_now"], True, True, True, True,
            Decimal("0"), Decimal("100")),
        monotonic_clock_ms=lambda: observations["risk_now"], max_observation_age_ms=5)
    controller.install_capital_risk_monitor(capital)
    assert not controller.allow_create_executor_actions()  # Bounded recovery, no new quote.
    assert controller.allow_create_executor_actions()
    quote_state["snapshot"] = replace(
        quote_state["snapshot"], loss_budget_status=controller.execution_loss_status)
    actions = controller.determine_executor_actions()
    assert len(actions) == 1
    executors = []
    runner = _runner(controller, connector, executors, actions[0].executor_config)
    try:
        runner.tick(1)
        assert len(connector.sent) == len(executors) == 1
        intent_id = executors[0].config.id
        wire_id = connector.sent[0]["order_id"]
        assert wal.get(intent_id).state == "SEND_UNKNOWN"
        assert subsidy.campaign_committed_quote == Decimal("0.0098")
        fill = SpotFill("service-trade", Decimal("0.4"), executors[0].config.price,
                        "USDT", Decimal("-0.01"), NOW_MS)
        assert controller._order_safety_gateway.apply_fills(wire_id, (fill,), Decimal("0.4"))
        assert attribution.ready()
        assert loss.verified_status(session_id=session.session_id,
                                    at_utc=NOW).session_loss_quote == Decimal("0.046")
        assert subsidy.campaign_committed_quote == Decimal("0.046")
        quote_state["snapshot"] = replace(
            quote_state["snapshot"], loss_budget_status=controller.execution_loss_status)
        assert controller._quote_action_planner.session_snapshot() is None
        assert controller.determine_executor_actions() == []
        assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_SNAPSHOT_MISMATCH"

        observations["now"] = NOW_MS + 100
        observations["nav"] = _nav("0.8", value_at=NOW_MS + 100,
                                   observed_at=NOW_MS + 100)
        observations["risk_now"] = 101
        assert not controller.allow_create_executor_actions()
        assert gate.state == "HALTED"
        with pytest.raises(PermissionError, match="LIFE_TRADING_DISABLED"):
            executors[0].place_open_order()
        assert len(connector.sent) == 1
        assert reservations.has_open_intent(intent_id)

        restored_reservations = type(reservations).restore(
            reservations.path, limits=reservations.limits)
        restored_subsidy = SubsidyBudgetLedger(
            subsidy.path, campaign_id="life-launch", campaign_limit_quote=Decimal("1"),
            day_limit_quote=Decimal("0.1"), session_limit_quote=Decimal("0.05"))
        restored_attribution = ReconciledFillAttributor(
            attribution.path, wal=IntentWAL(wal.path), reservations=restored_reservations,
            loss_budget=LossBudgetLedger(
                loss.path, campaign_id="life", campaign_limit_quote=Decimal("1"),
                day_limit_quote=Decimal("1"), session_limit_quote=Decimal("1")),
            subsidy_budget=restored_subsidy, opening_life=Decimal("10"),
            opening_usdt=Decimal("10"), opening_independent_price_usdt=Decimal("1"),
            independent_value=attribution.independent_value,
            max_reference_skew_ms=200, create=False)
        assert restored_attribution.ready()
        assert restored_subsidy.campaign_committed_quote == Decimal("0.046")
        assert SafetyGate(gate.path, max_drawdown_bps=Decimal("500"),
                          min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                          recovery_probe_base=Decimal("1")).state == "HALTED"
        assert quote_state["snapshot"].subsidy_remaining_quote == Decimal("0.05")
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_probe_partial_fill_markout_and_nav_halt_on_v2_queue(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = sender_setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    economics = controller.config.strategy.economics.model_copy(update={
        "fee_policy": "pause", "fee_max_age": "1s"})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    await install_market(controller, connector, exchange_ms=AT_MS)
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    session = controller._order_safety_manager.current_session
    loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                            session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=session.session_id, at_utc=AT)
    attribution = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"),
        independent_value=lambda _: IndependentFillObservation(
            Decimal("0.9"), AT_MS, AT_MS + 100, "independent_market"),
        max_reference_skew_ms=200, create=True)
    controller._order_safety_gateway.apply_fills = SpotReservationReconciler(
        wal, reservations, require_fees=True).apply_fills
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    observations = {"now": AT_MS, "nav": _nav("1"), "risk_now": 100, "horizons": {}}
    capital = CapitalRiskMonitor(
        tmp_path / "capital_risk.json", attributor=attribution,
        independent_value=lambda: observations["nav"],
        utc_clock_ms=lambda: observations["now"], max_value_age_ms=500, create=True)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    controller.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(
            observations["risk_now"], True, True, True, True,
            Decimal("0"), Decimal("100")),
        monotonic_clock_ms=lambda: observations["risk_now"], max_observation_age_ms=5)
    controller.install_capital_risk_monitor(capital)
    fee_state = {"now": 2000500, "fee": _fee()}
    quote_state, _ = _attach_quote_planner(
        controller, template, wal, reservations,
        loss_budget_status=loss.verified_status(session_id=session.session_id, at_utc=AT),
        fee_binding=_binding(fee_state), propose=False)
    quote_state["snapshot"] = replace(quote_state["snapshot"], costs=_costs())
    markout_state = {"now": AT_MS, "observations": observations["horizons"]}
    markout = _monitor(tmp_path, attribution, markout_state)
    controller.install_markout_monitor(markout)
    controller.install_markout_probe_guard(MarkoutProbeGuard(
        markout, reservations, max_quote_base=Decimal("1"),
        max_campaign_base=Decimal("1"), create=True))

    assert not controller.allow_create_executor_actions()  # Safety recovery still blocks DEGRADED.
    assert controller.allow_create_executor_actions()
    actions = controller.determine_executor_actions()
    assert len(actions) == 1
    config = actions[0].executor_config
    executors = []
    runner = _runner(controller, connector, executors, config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        assert len(executors) == 1
        wire = {"clOrdId": executors[0]._order.order_id, "instId": "LIFE-USDT",
                "side": "buy", "ordType": "post_only", "tdMode": "cash",
                "px": str(config.price), "sz": str(config.amount)}
        connector.sent[0]["pre_send_check"](wire)
        assert wal.get(config.id).state == "SEND_UNKNOWN"
        assert reservations.has_open_intent(config.id)
        fee_state["fee"] = _fee(maker="-0.02")
        with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
            connector.sent[0]["pre_send_check"](wire)
        assert controller._quote_action_planner.fee_reason_code == "FEE_COST_UNDERSTATED"
        fee_state["fee"] = _fee()

        fill = SpotFill("trade-probe", Decimal("0.4"), config.price,
                        "USDT", Decimal("-0.01"), AT_MS)
        assert controller._order_safety_gateway.apply_fills(
            wire["clOrdId"], (fill,), Decimal("0.4"))
        assert attribution.ready()
        assert loss.verified_status(session_id=session.session_id,
                                    at_utc=AT).session_loss_quote > 0
        observations["now"] = AT_MS + 400
        observations["nav"] = _nav("1", value_at=AT_MS + 400,
                                   observed_at=AT_MS + 400)
        markout_state["now"] = observations["now"]
        assert not controller.allow_create_executor_actions()
        assert controller.markout_reason_code == "MARKOUT_HORIZON_PENDING"
        queued_retry = CreateExecutorAction(
            controller_id="life", executor_config=config.model_copy(update={"id": "probe-retry"}))
        assert StrategyV2Base._filter_authorized_actions(runner, [queued_retry]) == []
        assert len(connector.sent) == 1
        connector.fail_status = True
        runner.tick(2)
        await controller.order_safety_task
        assert controller._order_safety_manager.state == "PAUSED"
        assert connector.cancels == [("LIFE-USDT", wire["clOrdId"])]
        assert reservations.has_open_intent(config.id)

        observations["now"] = AT_MS + 1000
        observations["nav"] = _nav("1", value_at=AT_MS + 1000,
                                   observed_at=AT_MS + 1000)
        observations["horizons"][("trade-probe", 1000)] = _horizon("0.8")
        markout_state["now"] = observations["now"]
        assert not controller.allow_create_executor_actions()
        assert controller.markout_reason_code == "MARKOUT_ADVERSE"
        assert gate.state == "NORMAL"

        observations["now"] = AT_MS + 1100
        observations["nav"] = _nav("0.8", value_at=AT_MS + 1100,
                                   observed_at=AT_MS + 1100)
        observations["risk_now"] = 101
        assert not controller.allow_create_executor_actions()
        assert gate.state == "HALTED"
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        with pytest.raises(PermissionError, match="LIFE_TRADING_DISABLED"):
            executors[0].place_open_order()
        assert len(connector.sent) == 1
        assert wal.get(config.id).state == "SEND_UNKNOWN"
        assert reservations.has_open_intent(config.id)

        restarted_reservations = type(reservations).restore(
            reservations.path, limits=reservations.limits)
        restarted_attribution = ReconciledFillAttributor(
            attribution.path, wal=IntentWAL(wal.path), reservations=restarted_reservations,
            loss_budget=loss, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
            opening_independent_price_usdt=Decimal("1"),
            independent_value=attribution.independent_value,
            max_reference_skew_ms=200, create=False)
        restarted_markout = _monitor(
            tmp_path, restarted_attribution, markout_state, restore=True)
        assert not restarted_markout.evaluate()
        assert restarted_markout.reason_code == "MARKOUT_ADVERSE"
        assert SafetyGate(gate.path, max_drawdown_bps=Decimal("500"),
                          min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                          recovery_probe_base=Decimal("1")).state == "HALTED"
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
