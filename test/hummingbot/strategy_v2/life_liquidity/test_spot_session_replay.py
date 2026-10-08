"""P5.8 synthetic LIFE session through the V2 runner and spot recovery path."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import Connector, _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_reference_transition import (
    manager as transition_manager,
    reconciled,
    start,
)
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class FakeTradingOkx(Connector, FakeOkx):
    def __init__(self):
        Connector.__init__(self)
        FakeOkx.__init__(self)


def _runner(controller, connector, executors, queued_config):
    orchestrator = MagicMock()
    orchestrator.active_executors = {"life": executors}
    orchestrator.get_stored_executors_by_controller.return_value = ()
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: orchestrator):
        runner = StrategyV2Base({}, config=None)
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": connector}
    runner.ready_to_trade = True
    runner.market_data_provider = SimpleNamespace(ready=True)
    runner.update_executors_info = lambda: None
    runner.update_controllers_configs = lambda: None
    queued = [CreateExecutorAction(controller_id="life", executor_config=queued_config)]
    runner.determine_executor_actions = lambda: list(queued)

    def execute(action):
        queued.clear()
        executor = OrderExecutor(runner, action.executor_config)
        executor.get_order_price = lambda: action.executor_config.price
        executors.append(executor)
        executor.place_open_order()

    orchestrator.execute_action.side_effect = execute
    return runner


@pytest.mark.asyncio
async def test_qualified_snapshot_keeps_session_active_on_actual_runner_safety_tick(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, _, _, _ = _setup(tmp_path, connector=connector)
    _, proposed = _attach_quote_planner(controller, template, controller._order_safety_wal,
                                        controller._order_safety_reservations)
    controller._spot_quote_gates_ready = lambda: True
    runner = _runner(controller, connector, [], proposed.config)
    try:
        runner.tick(1)
        assert controller._order_safety_manager.state == "ACTIVE"
        assert len(connector.sent) == 1
        status = " ".join(controller.to_format_status())
        assert "LIFE-USDT" in status
        assert "reference: 1" in status
        assert controller._order_safety_manager.current_session.expires_at.isoformat() in status
        assert "session: ACTIVE" in status
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_stale_qualified_snapshot_pauses_session_and_clears_status_reference(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    state, _ = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    runner = SimpleNamespace(controllers={"life": controller},
                             executor_orchestrator=SimpleNamespace(
                                 active_executors={"life": []},
                                 get_stored_executors_by_controller=lambda _: ()),
                             logger=lambda: MagicMock())
    try:
        StrategyV2Base._run_safety_callbacks(runner, 1)
        assert controller._order_safety_manager.state == "ACTIVE"
        state["now"] = 102
        StrategyV2Base._run_safety_callbacks(runner, 2)
        await controller.order_safety_task
        assert controller._order_safety_manager.state == "PAUSED"
        status = " ".join(controller.to_format_status())
        assert "reference: unavailable" in status
        assert "pause: SESSION_GATE_UNAVAILABLE" in status
        assert connector.sent == []
    finally:
        if controller.order_safety_task is not None and not controller.order_safety_task.done():
            controller.order_safety_task.cancel()


@pytest.mark.asyncio
async def test_recovered_reference_waits_for_pause_reconciliation_before_resuming(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    state, _ = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    runner = SimpleNamespace(controllers={"life": controller},
                             executor_orchestrator=SimpleNamespace(
                                 active_executors={"life": []},
                                 get_stored_executors_by_controller=lambda _: ()),
                             logger=lambda: MagicMock())
    StrategyV2Base._run_safety_callbacks(runner, 1)
    assert controller._order_safety_manager.state == "ACTIVE"
    state["now"] = 102
    StrategyV2Base._run_safety_callbacks(runner, 2)
    assert controller._order_safety_manager.state == "PAUSED"
    state["snapshot"] = replace(state["snapshot"], observed_monotonic=102,
                                expires_monotonic=104)
    StrategyV2Base._run_safety_callbacks(runner, 3)
    assert controller._order_safety_manager.state == "PAUSED"
    await controller.order_safety_task
    assert controller.order_safety_reason_code == "OLD_ORDERS_RECONCILED"
    StrategyV2Base._run_safety_callbacks(runner, 4)
    assert controller._order_safety_manager.state == "ACTIVE"


@pytest.mark.asyncio
async def test_successor_safety_tick_requires_independent_market_reference(tmp_path):
    clock = FakeClock()
    manager = transition_manager(tmp_path, clock)
    primary = start(manager)
    clock.advance(10)
    assert manager.tick(reference_ready=True, all_gates_ready=True) == "TRANSITIONING"
    assert manager.tick(reference_ready=True, all_gates_ready=True,
                        reconciliation=reconciled(primary, clock),
                        market_reference_ready=True,
                        market_anchor_usdt=Decimal("1")) == "ACTIVE"
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    controller.install_order_safety(manager, controller._order_safety_gateway, wal,
                                    reservations=reservations)
    controller._protected_spot_sender.manager = manager
    state, _ = _attach_quote_planner(controller, template, wal, reservations)
    state["snapshot"] = replace(state["snapshot"], market_reference_ready=False)
    controller._spot_quote_gates_ready = lambda: True
    runner = SimpleNamespace(controllers={"life": controller},
                             executor_orchestrator=SimpleNamespace(
                                 active_executors={"life": []},
                                 get_stored_executors_by_controller=lambda _: ()),
                             logger=lambda: MagicMock())
    try:
        StrategyV2Base._run_safety_callbacks(runner, 1)
        assert manager.state == "PAUSED"
        assert manager.reason_code == "MARKET_REFERENCE_UNAVAILABLE"
        assert controller.determine_executor_actions() == []
    finally:
        if controller.order_safety_task is not None:
            await controller.order_safety_task


@pytest.mark.asyncio
async def test_partial_fill_cancel_and_terminal_reconciliation_stays_scoped(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    manager = controller._order_safety_manager
    wall_time = [manager.wall_clock()]
    monotonic_time = [manager.monotonic_clock()]
    manager.wall_clock = lambda: wall_time[0]
    manager.monotonic_clock = lambda: monotonic_time[0]
    _, proposed = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    reconciler = SpotReservationReconciler(wal, reservations)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: wall_time[0],
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, reservations).check)
    controller.install_order_safety(manager, gateway, wal,
                                    reservations=reservations)
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    try:
        runner.tick(1)
        assert len(executors) == 1
        wire_id = connector.sent[0]["order_id"]
        connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                     "state": "partially_filled", "accFillSz": "0.4"}
        connector.fills["exchange-1"] = [
            {"tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
             "fillPx": "0.99"}]
        connector.cash_balances = {"LIFE": "10.4", "USDT": "9.604"}
        connector.open_pages[None] = [
            {"clOrdId": wire_id, "ordId": "exchange-1", "instId": "LIFE-USDT",
             "state": "partially_filled"}]
        wall_time[0] += timedelta(seconds=10)
        monotonic_time[0] += 10
        runner.tick(2)
        await controller.order_safety_task
        assert controller._order_safety_manager.state == "EXPIRED"
        assert reservations.requires_reconciliation(executors[0].config.id)
        assert wal.get(executors[0].config.id).state != "TERMINAL"
        assert connector.cancels == [("LIFE-USDT", wire_id)]

        connector.status[wire_id]["state"] = "canceled"
        connector.open_pages[None] = []
        runner.tick(3)
        await controller.order_safety_task
        assert wal.get(executors[0].config.id).state == "TERMINAL"
        assert not reservations.requires_reconciliation(executors[0].config.id)
        assert reservations.life_balance == Decimal("10.4")
        assert reservations.usdt_balance == Decimal("9.604")
        assert len(connector.sent) == 1
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
