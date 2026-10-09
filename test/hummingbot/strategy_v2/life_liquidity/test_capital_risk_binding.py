"""Independent NAV and persisted high-water drawdown gate offline LIFE sends."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_account_bills import approval_file, bill
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor, IndependentNavObservation
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation


def _monitor(tmp_path, attribution, state, *, restore=False):
    return CapitalRiskMonitor(
        tmp_path / "capital_risk.json", attributor=attribution,
        independent_value=lambda: state["value"], utc_clock_ms=lambda: state["now"],
        max_value_age_ms=500, create=not restore)


def _nav(price, *, value_at=AT_MS, observed_at=AT_MS):
    return IndependentNavObservation(Decimal(price), value_at, observed_at, "independent_market")


def test_independent_nav_highwater_survives_restart_and_lower_price(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    state = {"now": AT_MS, "value": _nav("1.5")}
    monitor = _monitor(tmp_path, attribution, state)

    high = monitor.measure()
    assert high.adjusted_nav_quote == Decimal("25")
    assert high.highwater_quote == Decimal("25")
    state["now"] += 100
    state["value"] = _nav("1", value_at=AT_MS + 100, observed_at=AT_MS + 100)
    low = monitor.measure()
    assert low.adjusted_nav_quote == Decimal("20")
    assert low.highwater_quote == Decimal("25")
    assert low.drawdown_bps == Decimal("2000")

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored_attribution, _ = _attributor(tmp_path, wal, restored_reservations, restore=True)
    restored = _monitor(tmp_path, restored_attribution, state, restore=True)
    assert restored.measure().drawdown_bps == Decimal("2000")


def test_stale_monitor_cannot_overwrite_new_highwater_or_reuse_rolled_back_clock(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    state = {"now": AT_MS, "value": _nav("1.5")}
    monitor = _monitor(tmp_path, attribution, state)
    stale = _monitor(tmp_path, attribution, state, restore=True)
    assert monitor.measure().highwater_quote == Decimal("25")
    assert stale.measure() is None
    state["now"] -= 1
    state["value"] = _nav("1.5", value_at=AT_MS - 1, observed_at=AT_MS - 1)
    assert monitor.measure() is None


def test_post_replace_error_requires_restart_to_trust_highwater(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    state = {"now": AT_MS, "value": _nav("1.5")}
    monitor = _monitor(tmp_path, attribution, state)
    real_save = monitor._save

    def write_then_fail(updated):
        real_save(updated)
        raise OSError("directory sync ambiguous")

    with patch.object(monitor, "_save", side_effect=write_then_fail):
        assert monitor.measure() is None
    assert monitor.measure() is None
    restored = _monitor(tmp_path, attribution, state, restore=True)
    assert restored.measure().highwater_quote == Decimal("25")


@pytest.mark.asyncio
async def test_approved_deposit_does_not_mask_cashflow_adjusted_drawdown(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    approvals = CashflowApprovals.load(approval_file(tmp_path, approvals=[
        {"bill_id": "101", "currency": "USDT", "amount": "10"}]))
    attribution, _ = _attributor(tmp_path, wal, reservations, approvals=approvals)
    connector = controller._order_safety_gateway.connector
    connector.bill_pages[None] = [bill("101", change="10"), bill("100")]
    bills = SpotBillReconciler(connector, reservations, wal, approvals,
                               on_cashflows_applied=attribution.apply_approved_cashflows)
    assert await bills.reconcile()
    state = {"now": AT_MS, "value": _nav("1.5")}
    monitor = _monitor(tmp_path, attribution, state)

    high = monitor.measure()
    assert high.nav_quote == Decimal("35")
    assert high.adjusted_nav_quote == Decimal("25")
    assert high.highwater_quote == Decimal("25")
    state["now"] += 100
    state["value"] = _nav("1", value_at=AT_MS + 100, observed_at=AT_MS + 100)
    low = monitor.measure()
    assert low.nav_quote == Decimal("30")
    assert low.adjusted_nav_quote == Decimal("20")
    assert low.drawdown_bps == Decimal("2000")


@pytest.mark.parametrize("invalid", [
    None,
    _nav("1", value_at=AT_MS - 501),
    IndependentNavObservation(Decimal("1"), AT_MS, AT_MS, "benchmark_model"),
])
def test_missing_stale_or_model_nav_value_never_grants_risk(tmp_path, invalid):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    state = {"now": AT_MS, "value": invalid}
    monitor = _monitor(tmp_path, attribution, state)

    assert monitor.measure() is None


def test_capital_drawdown_overrides_synthetic_risk_input_and_latches_halt(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    state = {"now": AT_MS, "value": _nav("1.5"), "risk_now": 100}
    monitor = _monitor(tmp_path, attribution, state)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    base = SafetyObservation(100, True, True, True, True, Decimal("0"), Decimal("100"))
    controller.install_runtime_risk_gate(
        gate, observation=lambda: replace(base, observed_monotonic_ms=state["risk_now"]),
        monotonic_clock_ms=lambda: state["risk_now"], max_observation_age_ms=5)
    controller.install_capital_risk_monitor(monitor)
    assert not controller.allow_create_executor_actions()  # Recovery probe remains disabled.
    assert controller.allow_create_executor_actions()

    state["now"] += 100
    state["risk_now"] += 1
    state["value"] = _nav("1", value_at=AT_MS + 100, observed_at=AT_MS + 100)
    assert not controller.allow_create_executor_actions()
    assert gate.state == "HALTED"
    assert controller.runtime_risk_reason_code == "DRAWDOWN_LIMIT_BREACHED"
    assert SafetyGate(gate.path, max_drawdown_bps=Decimal("9999"),
                      min_margin_buffer_quote=Decimal("0"), stable_data_ms=0,
                      recovery_probe_base=Decimal("10")).state == "HALTED"


def test_missing_nav_value_pauses_runtime_risk_even_if_base_observer_is_green(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    state = {"now": AT_MS, "value": None}
    monitor = _monitor(tmp_path, attribution, state)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    controller.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(
            100, True, True, True, True, Decimal("0"), Decimal("100")),
        monotonic_clock_ms=lambda: 100, max_observation_age_ms=5)
    controller.install_capital_risk_monitor(monitor)

    assert not controller.allow_create_executor_actions()
    assert controller.runtime_risk_reason_code == "CAPITAL_VALUATION_UNAVAILABLE"


def test_persisted_nav_halt_revokes_final_wire_check(tmp_path):
    controller, template, connector, wal, reservations, _ = sender_setup(tmp_path)
    wal.initialize_empty()
    del controller.allow_create_executor_actions
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    session_id = controller._order_safety_manager.current_session.session_id
    loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=Decimal("1"), day_limit_quote=Decimal("1"),
                            session_limit_quote=Decimal("1"))
    loss.record("opening", Decimal("0"), session_id=session_id, at_utc=AT)
    attribution = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"),
        independent_value=lambda _: IndependentFillObservation(
            Decimal("1"), AT_MS, AT_MS, "independent_market"),
        max_reference_skew_ms=200, create=True)
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    state = {"now": AT_MS, "value": _nav("1.5"), "risk_now": 100}
    monitor = _monitor(tmp_path, attribution, state)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    controller.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(
            state["risk_now"], True, True, True, True, Decimal("0"), Decimal("100")),
        monotonic_clock_ms=lambda: state["risk_now"], max_observation_age_ms=5)
    controller.install_capital_risk_monitor(monitor)
    assert not controller.allow_create_executor_actions()
    assert controller.allow_create_executor_actions()
    _, executor = _attach_quote_planner(
        controller, template, wal, reservations,
        loss_budget_status=loss.verified_status(session_id=session_id, at_utc=AT))
    executor.place_open_order()
    check = connector.sent[0]["pre_send_check"]
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    state["now"] += 100
    state["risk_now"] += 1
    state["value"] = _nav("1", value_at=AT_MS + 100, observed_at=AT_MS + 100)

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert gate.state == "HALTED"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)
