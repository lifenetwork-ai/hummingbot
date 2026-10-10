"""DEGRADED recovery is opt-in, sequential, small, and bounded across restart."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_o2_revocation_replay import setup

import pytest

from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.config import ResumePolicyConfig
from hummingbot.strategy_v2.life_liquidity.recovery_probe import RecoveryProbeGuard
from hummingbot.strategy_v2.life_liquidity.safety import SafetyObservation
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

D = Decimal


@pytest.mark.asyncio
async def test_degraded_quote_is_clipped_and_final_send_rechecks_probe_capacity(tmp_path):
    controller, connector, wal, ledger, _, risk, gate, quote, previous, watchdog = await setup(tmp_path)
    try:
        planner = controller._quote_action_planner
        assert planner.on_runner_action_rejected(CreateExecutorAction(
            controller_id="life", executor_config=previous.config))
        planner.intent_id_factory = lambda: "recovery-1"
        resume = ResumePolicyConfig(stable_data_duration="1s", probe_size_base=D("0.5"))
        configured = controller.config.strategy.risk.model_copy(update={"resume_policy": resume})
        controller.config = controller.config.model_copy(update={
            "strategy": controller.config.strategy.model_copy(update={"risk": configured})})
        gate.stable_data_ms = 1000
        gate.recovery_probe_base = D("0.5")
        gate.invalidate("SYNTHETIC_FEED_RECOVERY")
        controller._runtime_risk_observation = lambda: SafetyObservation(
            risk["now"], True, True, True, True, D("0"), D("100"))
        guard = RecoveryProbeGuard(tmp_path / "recovery_probe.json", ledger,
                                   max_quote_base=D("0.5"), max_campaign_base=D("1"), create=True)
        controller.install_recovery_probe_guard(guard)
        assert not controller.allow_create_executor_actions()
        risk["now"] += 1000
        assert controller.allow_create_executor_actions()
        assert gate.state == "DEGRADED"
        actions = controller.determine_executor_actions()
        assert len(actions) == 1
        assert actions[0].executor_config.amount == D("0.5")
        executor = OrderExecutor(previous._strategy, actions[0].executor_config)
        executor.get_order_price = lambda: executor.config.price
        executor.place_open_order()
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT", "side": "buy",
                "ordType": "post_only", "tdMode": "cash", "px": str(executor.config.price), "sz": "0.5"}
        connector.sent[0]["pre_send_check"](wire)
        assert controller.determine_executor_actions() == []
        restored = RecoveryProbeGuard(guard.journal.path, ledger,
                                      max_quote_base=D("0.5"), max_campaign_base=D("1"), create=False)
        assert not restored.authorizes("BUY", D("0.5"))  # Unknown first probe is still held.
        with pytest.raises(ValueError):
            RecoveryProbeGuard(guard.journal.path, ledger, max_quote_base=D("1"),
                               max_campaign_base=D("10"), create=False)
        guard.journal.path.unlink()
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert ledger.has_open_intent(executor.config.id)
    finally:
        watchdog.cancel()


def test_filled_probe_capacity_survives_terminal_restart_and_policy_mutation(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
    ledger = ReservationLedger(life_balance=D("10"), usdt_balance=D("10"),
                               limits=RiskLimits(D("0"), D("20"), D("30"), D("20")),
                               path=tmp_path / "reserves.json")
    guard = RecoveryProbeGuard(tmp_path / "probes.json", ledger,
                               max_quote_base=D("0.5"), max_campaign_base=D("0.5"), create=True)
    assert guard.authorizes("BUY", D("0.5"))
    intent = SpotIntent("p1", "BUY", D("0.5"), D("1"), "session-1", 1)
    assert ledger.reserve(intent, reference_price=D("1")).allowed
    ledger.record_fill("p1", "fill", D("0.5"), D("1"))
    ledger.confirm_terminal("p1", cumulative_filled=D("0.5"), fills_reconciled=True, exchange_state="FILLED")
    restored = RecoveryProbeGuard(guard.journal.path, ReservationLedger.restore(ledger.path, limits=ledger.limits),
                                  max_quote_base=D("0.5"), max_campaign_base=D("0.5"), create=False)
    assert not restored.capacity_available()
    assert not restored.authorizes("SELL", D("0.1"))
    restored.max_campaign_base = D("100")
    assert not restored.capacity_available()
