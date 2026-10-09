"""Persisted markouts of reconciled fills revoke LIFE quote permission."""

import json
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.markout_risk import (
    IndependentHorizonObservation,
    MarkoutProbeGuard,
    ReconciledMarkoutMonitor,
)
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent


def _monitor(tmp_path, attribution, state, *, restore=False, min_samples=1,
             cohorts=(("BUY", "small", 1000),)):
    return ReconciledMarkoutMonitor(
        tmp_path / "markout_risk.json", attributor=attribution,
        independent_horizon=lambda trade_id, horizon: state["observations"].get((trade_id, horizon)),
        utc_clock_ms=lambda: state["now"], horizons_ms=(1000,), min_samples=min_samples,
        size_cutoff_base=Decimal("1"), cohort_window_ms=5000,
        max_horizon_lag_ms=200, min_mean_markout_quote=Decimal("0"),
        monitored_cohorts=cohorts, create=not restore)


def _fill(trade_id, quantity="0.4"):
    return SpotFill(trade_id, Decimal(quantity), Decimal("1"), "USDT",
                    Decimal("-0.01"), AT_MS)


def _horizon(price, *, value_at=AT_MS + 1000, observed_at=AT_MS + 1000):
    return IndependentHorizonObservation(Decimal(price), value_at, observed_at,
                                         "independent_market")


def test_reconciled_fill_horizon_observation_and_restart_are_idempotent(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 999, "observations": {}}
    monitor = _monitor(tmp_path, attribution, state)

    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_HORIZON_PENDING"
    state["now"] += 1
    state["observations"][("trade-1", 1000)] = _horizon("0.9")
    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_ADVERSE"
    assert monitor.summary("BUY", "small", 1000).mean_markout_quote == Decimal("-0.04")
    assert loss.verified_status(session_id=wal.get("i1").session_id,
                                at_utc=AT).session_loss_quote == Decimal("0.05")

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored_attribution, _ = _attributor(tmp_path, wal, restored_reservations, restore=True)
    restored = _monitor(tmp_path, restored_attribution, state, restore=True)
    assert not restored.evaluate()
    assert restored.reason_code == "MARKOUT_ADVERSE"
    assert restored.summary("BUY", "small", 1000).sample_count == 1


def test_missing_model_or_late_horizon_observation_never_resumes_quotes(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1201, "observations": {}}
    monitor = _monitor(tmp_path, attribution, state)
    state["observations"][("trade-1", 1000)] = _horizon("1.1", value_at=AT_MS + 1201)
    assert not monitor.evaluate()
    state["observations"][("trade-1", 1000)] = IndependentHorizonObservation(
        Decimal("1.1"), AT_MS + 1000, AT_MS + 1000, "benchmark_model")
    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_OBSERVATION_MISSING"


def test_future_independent_horizon_is_used_only_after_arrival(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1000, "observations": {
        ("trade-1", 1000): _horizon("1.1", observed_at=AT_MS + 1001)}}
    monitor = _monitor(tmp_path, attribution, state)

    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_OBSERVATION_MISSING"
    state["now"] += 1
    assert monitor.evaluate()


def test_new_bad_partial_fill_revokes_controller_create_without_double_counting_loss(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    first = _fill("trade-1")
    assert controller._order_safety_gateway.apply_fills("wire-1", (first,), Decimal("0.4"))
    state = {"now": AT_MS + 1000, "observations": {
        ("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state)
    controller.install_markout_monitor(monitor)
    assert controller.allow_create_executor_actions()

    second = _fill("trade-2")
    assert controller._order_safety_gateway.apply_fills(
        "wire-1", (first, second), Decimal("0.8"))
    state["now"] = AT_MS + 1001
    state["observations"][("trade-2", 1000)] = _horizon("0.8")
    assert not controller.allow_create_executor_actions()
    assert controller.markout_reason_code == "MARKOUT_ADVERSE"
    assert monitor.summary("BUY", "small", 1000).sample_count == 2
    assert loss.verified_status(session_id=wal.get("i1").session_id,
                                at_utc=AT).session_loss_quote == Decimal("0.10")


def test_markout_journal_write_failure_and_stale_instance_block_new_risk(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1000, "observations": {("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state)
    stale = _monitor(tmp_path, attribution, state, restore=True)
    with patch.object(monitor, "_save", side_effect=OSError("journal write failed")):
        assert not monitor.evaluate()
    assert monitor.evaluate()
    assert not stale.evaluate()


def test_old_favorable_cohort_expires_and_insufficient_samples_block_resume(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1000, "observations": {("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state)
    assert monitor.evaluate()
    state["now"] = AT_MS + 5001
    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_INSUFFICIENT_SAMPLES"


def test_one_favorable_fill_cannot_satisfy_two_sample_policy(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1000, "observations": {("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state, min_samples=2)

    assert not monitor.evaluate()
    assert monitor.reason_code == "MARKOUT_INSUFFICIENT_SAMPLES"


def test_corrupt_persisted_horizon_does_not_become_trusted_on_restart(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = _fill("trade-1")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    state = {"now": AT_MS + 1000, "observations": {("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state)
    assert monitor.evaluate()
    data = json.loads(monitor.path.read_text())
    data["state"]["observations"]["trade-1"]["1000"]["value_at_ms"] = AT_MS + 999
    monitor.path.write_text(json.dumps(data))
    restored = _monitor(tmp_path, attribution, state, restore=True)

    assert not restored.evaluate()
    assert restored.reason_code == "MARKOUT_OBSERVATION_CONFLICT"


def test_net_flat_buy_sell_fills_retain_separate_adverse_cohorts(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    buy = _fill("trade-buy")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (buy,), Decimal("0.4"))
    assert attribution.apply("wire-1", (buy,))
    session = wal.get("i1")
    wal.begin("i2", client_order_id="wire-2", session_id=session.session_id,
              epoch=session.epoch, reservation_id="i2", slot_market="LIFE-USDT",
              slot_side="SELL", slot_level=1)
    wal.arm_send("i2", client_order_id="wire-2", session_id=session.session_id,
                 epoch=session.epoch, reservation_id="i2")
    assert reservations.reserve(SpotIntent(
        "i2", "SELL", Decimal("0.4"), Decimal("1"), session.session_id, session.epoch),
        reference_price=Decimal("1")).allowed
    sell = _fill("trade-sell")
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-2", (sell,), Decimal("0.4"))
    assert attribution.apply("wire-2", (sell,))
    state = {"now": AT_MS + 1000, "observations": {
        ("trade-buy", 1000): _horizon("0.9"),
        ("trade-sell", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state, cohorts=(
        ("BUY", "small", 1000), ("SELL", "small", 1000)))

    assert not monitor.evaluate()
    assert monitor.summary("BUY", "small", 1000).mean_markout_quote == Decimal("-0.04")
    assert monitor.summary("SELL", "small", 1000).mean_markout_quote == Decimal("-0.04")
    assert attribution.capital().life_balance == Decimal("10")
    assert loss.verified_status(session_id=session.session_id,
                                at_utc=AT).session_loss_quote == Decimal("0.05")

    # An empty cohort must not conceal an adverse cohort evaluated later.
    mixed = _monitor(tmp_path / "mixed", attribution, state, cohorts=(
        ("BUY", "large", 1000), ("SELL", "small", 1000)))
    assert not mixed.evaluate()
    assert mixed.reason_code == "MARKOUT_ADVERSE"


def test_adverse_markout_revokes_queued_final_wire_check(tmp_path):
    controller, template, connector, wal, reservations, _ = sender_setup(tmp_path)
    del controller.allow_create_executor_actions
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    session = controller._order_safety_manager.current_session
    wal.begin("history", client_order_id="history-wire", session_id=session.session_id,
              epoch=session.epoch, reservation_id="history", slot_market="LIFE-USDT",
              slot_side="BUY", slot_level=1)
    wal.arm_send("history", client_order_id="history-wire", session_id=session.session_id,
                 epoch=session.epoch, reservation_id="history")
    assert reservations.reserve(SpotIntent(
        "history", "BUY", Decimal("1"), Decimal("1"), session.session_id, session.epoch),
        reference_price=Decimal("1")).allowed
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
    first = _fill("trade-1")
    assert controller._order_safety_gateway.apply_fills(
        "history-wire", (first,), Decimal("0.4"))
    state = {"now": AT_MS + 1000, "observations": {("trade-1", 1000): _horizon("1.1")}}
    monitor = _monitor(tmp_path, attribution, state)
    controller.install_markout_monitor(monitor)
    assert controller.allow_create_executor_actions()
    _, executor = _attach_quote_planner(
        controller, template, wal, reservations,
        loss_budget_status=loss.verified_status(session_id=session.session_id, at_utc=AT))
    executor.place_open_order()
    check = connector.sent[0]["pre_send_check"]
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    second = _fill("trade-2")
    assert controller._order_safety_gateway.apply_fills(
        "history-wire", (first, second), Decimal("0.8"))
    state["now"] += 1
    state["observations"][("trade-2", 1000)] = _horizon("0.8")

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert controller.markout_reason_code == "MARKOUT_ADVERSE"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)


def test_empty_history_permits_one_capped_probe_then_waits_for_horizon(tmp_path):
    controller, template, connector, wal, reservations, _ = sender_setup(tmp_path)
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    controller._spot_quote_gates_ready = lambda: True
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
    _attach_quote_planner(
        controller, template, wal, reservations,
        loss_budget_status=loss.verified_status(session_id=session.session_id, at_utc=AT),
        propose=False)
    state = {"now": AT_MS + 1000, "observations": {}}
    monitor = _monitor(tmp_path, attribution, state)
    controller.install_markout_monitor(monitor)
    guard = MarkoutProbeGuard(
        monitor, reservations, max_quote_base=Decimal("1"),
        max_campaign_base=Decimal("1"), create=True)
    controller.install_markout_probe_guard(guard)

    actions = controller.determine_executor_actions()
    assert len(actions) == 1, (controller.markout_reason_code,
                               controller._quote_action_planner.reason_code,
                               controller.execution_loss_reason_code,
                               controller.fill_attribution_reason_code)
    assert actions[0].executor_config.side.name == "BUY"
    assert controller.determine_executor_actions() == []
    executor = OrderExecutor(template._strategy, actions[0].executor_config)
    executor.get_order_price = lambda: executor.config.price
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    connector.sent[0]["pre_send_check"](wire)
    assert reservations.has_open_intent(executor.config.id)
    fill = SpotFill("probe-fill", Decimal("1"), executor.config.price, "USDT",
                    Decimal("-0.01"), AT_MS)
    assert controller._order_safety_gateway.apply_fills(
        executor._order.order_id, (fill,), Decimal("1"))
    assert not controller.allow_create_executor_actions()
    assert controller.markout_reason_code == "MARKOUT_OBSERVATION_MISSING"
    assert reservations.preview().filled_base_total == Decimal("1")


def test_probe_policy_is_bound_to_durable_cap_and_fails_on_change(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    state = {"now": AT_MS + 1000, "observations": {}}
    monitor = _monitor(tmp_path, attribution, state)
    assert not monitor.evaluate()
    guard = MarkoutProbeGuard(
        monitor, reservations, max_quote_base=Decimal("0.5"),
        max_campaign_base=Decimal("1"), create=True)
    assert not guard.authorizes("BUY", Decimal("0.4"))  # Existing unresolved order.
    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored_attribution, _ = _attributor(
        tmp_path, type(wal)(wal.path), restored_reservations, restore=True)
    restored_monitor = _monitor(tmp_path, restored_attribution, state, restore=True)
    assert not restored_monitor.evaluate()
    restored = MarkoutProbeGuard(
        restored_monitor, restored_reservations, max_quote_base=Decimal("0.5"),
        max_campaign_base=Decimal("1"), create=False)
    assert not restored.authorizes("BUY", Decimal("0.6"))
    with pytest.raises(ValueError, match="MARKOUT_PROBE_POLICY_MISMATCH"):
        MarkoutProbeGuard(monitor, reservations, max_quote_base=Decimal("0.5"),
                          max_campaign_base=Decimal("2"), create=False)
    guard.path.write_text("{}")
    assert not guard.capacity_available()
