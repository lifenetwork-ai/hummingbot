"""Rolling fill history and one-sided stress must protect every new spot quote."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_inventory_subsidy_settlement import cycle
from test.hummingbot.strategy_v2.life_liquidity.test_o2_revocation_replay import setup as runtime_setup
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS

import pytest

from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotRiskBinding, SpotStressObservation

D = Decimal


def binding(tmp_path, *, restore=False, maximum="1"):
    attribution, _, _, controller = cycle(tmp_path)
    state = {"now": AT_MS + 1}
    state["stress"] = SpotStressObservation(AT_MS, AT_MS, "independent_market", D("1"),
                                            D("0.99"), D("1.01"), D("20"), D("10"), D("0"), D("0"))
    guard = SpotRiskBinding(
        tmp_path / "spot_risk.json", attributor=attribution,
        utc_clock_ms=lambda: state["now"], stress_observation=lambda: state["stress"],
        window_ms=1000, max_filled_base=D(maximum), target_inventory_base=D("10"),
        max_stress_loss_quote=D("0.2"), max_observation_age_ms=2000, create=not restore)
    return guard, attribution, state, controller


def intent(quantity="0.5", side="BUY", price="1"):
    return SpotIntent("candidate", side, D(quantity), D(price), "session", 1)


def test_replenishment_uses_durable_fill_times_plus_pending_same_side(tmp_path):
    guard, attribution, _, _ = binding(tmp_path)
    preview = attribution.reservations.preview()
    assert guard.check(intent(), preview) is None  # 0.4 filled + 0.5 proposed.
    assert preview.check_and_hold(intent(), reference_price=D("1")).allowed
    assert guard.check(intent("0.2"), preview) == "ROLLING_FILL_CAPACITY_EXHAUSTED"
    # Opposite-side fills do not cancel the BUY counter.
    assert guard.check(intent("0.7"), attribution.reservations.preview()) == "ROLLING_FILL_CAPACITY_EXHAUSTED"


def test_window_ages_naturally_and_does_not_reset_on_restart_or_rollback(tmp_path):
    guard, attribution, state, _ = binding(tmp_path)
    assert guard.check(intent("0.7"), attribution.reservations.preview()) == "ROLLING_FILL_CAPACITY_EXHAUSTED"
    restored = SpotRiskBinding(
        guard.journal.path, attributor=attribution, utc_clock_ms=lambda: state["now"],
        stress_observation=lambda: state["stress"], window_ms=1000,
        max_filled_base=D("1"), target_inventory_base=D("10"),
        max_stress_loss_quote=D("0.2"), max_observation_age_ms=2000, create=False)
    assert restored.check(intent("0.7"), attribution.reservations.preview()) == "ROLLING_FILL_CAPACITY_EXHAUSTED"
    state["now"] = AT_MS + 1001
    assert restored.check(intent("0.7"), attribution.reservations.preview()) is None
    state["now"] -= 1
    assert restored.check(intent(), attribution.reservations.preview()) == "SPOT_RISK_UNAVAILABLE"
    with pytest.raises(ValueError):
        SpotRiskBinding(
            guard.journal.path, attributor=attribution, utc_clock_ms=lambda: state["now"],
            stress_observation=lambda: state["stress"], window_ms=1000,
            max_filled_base=D("100"), target_inventory_base=D("10"),
            max_stress_loss_quote=D("0.2"), max_observation_age_ms=2000, create=False)


@pytest.mark.parametrize("change,reason", [
    ("depth", "STRESS_EXIT_DEPTH_UNAVAILABLE"), ("shock", "STRESS_LOSS_EXCEEDED"),
    ("stale", "SPOT_RISK_UNAVAILABLE"), ("model", "SPOT_RISK_UNAVAILABLE"),
    ("journal", "SPOT_RISK_UNAVAILABLE"),
])
def test_stress_and_missing_evidence_block_risk(tmp_path, change, reason):
    guard, attribution, state, _ = binding(tmp_path)
    if change == "depth":
        state["stress"] = replace(state["stress"], exit_depth_base=D("0"))
    elif change == "shock":
        state["stress"] = replace(state["stress"], stressed_exit_usdt=D("0.5"))
    elif change == "stale":
        state["now"] += 2001
    elif change == "model":
        state["stress"] = replace(state["stress"], source_kind="benchmark")
    else:
        guard.journal.path.unlink()
    assert guard.check(intent(), attribution.reservations.preview()) == reason


def test_unknown_order_is_included_in_one_sided_stress(tmp_path):
    guard, attribution, _, _ = binding(tmp_path)
    pending = SpotIntent("unknown", "BUY", D("0.5"), D("1.4"), "session", 1)
    assert attribution.reservations.reserve(pending, reference_price=D("1")).allowed
    attribution.reservations.mark_unknown("unknown")
    # The pending order cannot be offset with a pending sell.
    assert guard.check(intent("0.1"), attribution.reservations.preview()) == "STRESS_LOSS_EXCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["depth", "fill", "journal"])
async def test_actual_executor_final_check_repeats_stress_and_rolling_capacity(tmp_path, change):
    values = await runtime_setup(tmp_path)
    controller, connector, wal, reservations, _, _, _, quote, executor, watchdog = values
    try:
        wal.initialize_empty()
        session = controller._order_safety_manager.current_session
        loss = LossBudgetLedger(tmp_path / "loss_budget.json", campaign_id="life",
                                campaign_limit_quote=D("1"), day_limit_quote=D("1"),
                                session_limit_quote=D("1"))
        loss.record("opening", D("0"), session_id=session.session_id, at_utc=AT)
        attribution = ReconciledFillAttributor(
            tmp_path / "attribution.json", wal=wal, reservations=reservations, loss_budget=loss,
            opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
            independent_value=lambda fill: IndependentFillObservation(D("1"), AT_MS, AT_MS, "independent_market"),
            max_reference_skew_ms=100, create=True)
        controller._order_safety_gateway.apply_fills = SpotReservationReconciler(
            wal, reservations, require_fees=True).apply_fills
        controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
        controller.install_fill_attributor(attribution)
        risk = controller.config.strategy.risk.model_copy(update={
            "rolling_fill_window": "1s", "max_filled_base_per_window": D("1"),
            "stress_loss_budget_quote": D("0.2")})
        controller.config = controller.config.model_copy(update={
            "strategy": controller.config.strategy.model_copy(update={"risk": risk})})
        stress = {"value": SpotStressObservation(AT_MS, AT_MS, "independent_market", D("1"),
                                                 D("0.99"), D("1.01"), D("20"), D("10"), D("0"), D("0"))}
        binding = SpotRiskBinding(tmp_path / "spot_risk.json", attributor=attribution,
                                  utc_clock_ms=lambda: AT_MS + 1, stress_observation=lambda: stress["value"],
                                  window_ms=1000, max_filled_base=D("1"), target_inventory_base=D("10"),
                                  max_stress_loss_quote=D("0.2"), max_observation_age_ms=2000, create=True)
        controller.install_spot_risk_binding(binding)
        planner = controller._quote_action_planner
        planner.snapshot = lambda: replace(quote["snapshot"], loss_budget_status=loss.verified_status(
            session_id=controller._order_safety_manager.current_session.session_id, at_utc=AT))
        assert controller.allow_create_executor_actions()
        assert planner.authorizes_config(executor.config)
        executor.place_open_order()
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT", "side": "buy",
                "ordType": "post_only", "tdMode": "cash", "px": str(executor.config.price), "sz": "1"}
        connector.sent[0]["pre_send_check"](wire)
        if change == "depth":
            stress["value"] = replace(stress["value"], exit_depth_base=D("0"))
        elif change == "journal":
            binding.journal.path.unlink()
        else:
            fill = SpotFill("partial", D("0.1"), executor.config.price, "USDT", D("0"), AT_MS)
            assert controller._order_safety_gateway.apply_fills(executor._order.order_id, (fill,), D("0.1"))
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert reservations.has_open_intent(executor.config.id)
        assert len(connector.sent) == 1
    finally:
        watchdog.cancel()
