"""Queued and retried executor requests must obey real spot and safety gates."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.offline_market import disconnect, install_market
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from test.hummingbot.strategy_v2.life_liquidity.test_subsidy_runtime_binding import NOW_MS

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

D = Decimal
CHANGES = ("disconnect", "resync", "stale_book", "stale_risk", "halt",
           "invalid_config", "quote_expiry", "session_expiry")


async def setup(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    del controller.allow_create_executor_actions
    feed = await install_market(controller, connector, exchange_ms=NOW_MS)
    watchdog = asyncio.create_task(asyncio.Event().wait())
    controller.order_safety_watchdog_task = watchdog
    risk = {"now": 100}
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=D("500"),
                      min_margin_buffer_quote=D("0"), stable_data_ms=0,
                      recovery_probe_base=D("1"))
    gate.initialize_empty()
    controller.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(100, True, True, True, True, D("0"), D("100")),
        monotonic_clock_ms=lambda: risk["now"], max_observation_age_ms=5)
    assert not controller.allow_create_executor_actions()
    assert controller.allow_create_executor_actions()
    quote, executor = _attach_quote_planner(controller, template, wal, reservations)
    return controller, connector, wal, reservations, feed, risk, gate, quote, executor, watchdog


def revoke(change, controller, feed, risk, gate, quote):
    if change == "disconnect":
        disconnect(feed)
    elif change == "resync":
        feed["health"] = replace(feed["health"], epoch=2)
    elif change == "stale_book":
        feed["now"] = 103
    elif change == "stale_risk":
        risk["now"] = 106
    elif change == "halt":
        gate.halt("OFFLINE_OPERATOR_STOP")
    elif change == "invalid_config":
        strategy = controller.config.strategy.model_copy(update={"quotes": QuotesConfig(
            spreads_bps=(D("40"),), sizes_base=(D("1"),))})
        with pytest.raises(ValueError):
            controller.update_config(controller.config.model_copy(update={"strategy": strategy}))
    elif change == "quote_expiry":
        quote["now"] = 101
    else:
        controller._order_safety_manager.wall_clock = lambda: controller._order_safety_manager.current_session.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("change", CHANGES)
async def test_revocation_rejects_actual_v2_queued_action(tmp_path, change):
    controller, connector, wal, reservations, feed, risk, gate, quote, executor, watchdog = await setup(tmp_path)
    executors = []
    runner = _runner(controller, connector, executors, executor.config)
    try:
        revoke(change, controller, feed, risk, gate, quote)
        queued = CreateExecutorAction(controller_id="life", executor_config=executor.config)
        assert StrategyV2Base._filter_authorized_actions(runner, [queued]) == []
        runner.tick(1)
        assert connector.sent == []
        assert executors == []
        assert wal.all_records() == ()
        assert reservations.reservation_ids == frozenset()
        assert controller._quote_action_planner.action_journal.get(executor.config.id).state == "REJECTED"
    finally:
        watchdog.cancel()
        runner.listen_to_executor_actions_task.cancel()
        if controller.order_safety_task is not None:
            await controller.order_safety_task
        await asyncio.gather(watchdog, runner.listen_to_executor_actions_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", CHANGES)
async def test_revocation_rejects_final_wire_and_actual_executor_retry(tmp_path, change):
    controller, connector, wal, reservations, feed, risk, gate, quote, executor, watchdog = await setup(tmp_path)
    executors = []
    runner = _runner(controller, connector, executors, executor.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == len(executors) == 1
        actual = executors[0]
        wire = {"clOrdId": actual._order.order_id, "instId": "LIFE-USDT",
                "side": "buy", "ordType": "post_only", "tdMode": "cash",
                "px": str(actual.config.price), "sz": str(actual.config.amount)}
        connector.sent[0]["pre_send_check"](wire)
        # Even healthy data cannot justify blindly retrying an unknown send.
        with pytest.raises(PermissionError, match="QUOTE_ACTION_NOT_AUTHORIZED"):
            actual.place_open_order()
        revoke(change, controller, feed, risk, gate, quote)
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        with pytest.raises((PermissionError, ValueError)):
            actual.place_open_order()
        assert len(connector.sent) == 1
        assert wal.get(actual.config.id).state == "SEND_UNKNOWN"
        assert reservations.has_open_intent(actual.config.id)
    finally:
        watchdog.cancel()
        runner.listen_to_executor_actions_task.cancel()
        await asyncio.gather(watchdog, runner.listen_to_executor_actions_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("disk_failure", [False, True])
async def test_late_stop_revokes_healthy_already_dispatched_final_send(tmp_path, disk_failure):
    from unittest.mock import patch

    from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
    c, connector, wal, ledger, _, _, _, _, executor, watchdog = await setup(tmp_path)
    try:
        executor.place_open_order()
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT", "side": "buy",
                "ordType": "post_only", "tdMode": "cash", "px": str(executor.config.price), "sz": "1"}
        connector.sent[0]["pre_send_check"](wire)
        assert c.allow_create_executor_actions()
        stop = StopExecutorAction(controller_id="life", executor_id=executor.config.id)
        runner = type("Runner", (), {"controllers": {"life": c}, "logger": c.logger})()
        if disk_failure:
            with patch.object(wal, "mark_cancel_requested", side_effect=OSError("checkpoint failed")):
                assert StrategyV2Base._filter_authorized_actions(runner, [stop]) == [stop]
        else:
            assert StrategyV2Base._filter_authorized_actions(runner, [stop]) == [stop]
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert not ledger.is_terminal_intent(executor.config.id)
        assert not c.allow_create_executor_actions()
        assert len(connector.sent) == 1
    finally:
        watchdog.cancel()
