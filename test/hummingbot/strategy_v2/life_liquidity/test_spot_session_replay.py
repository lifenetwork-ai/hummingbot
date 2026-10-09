"""P5.8 synthetic LIFE session through the V2 runner and spot recovery path."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_account_bills import approval_file, bill
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import Connector, _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_reference_transition import (
    manager as transition_manager,
    reconciled,
    start,
)
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, manager as session_manager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class FakeTradingOkx(Connector, FakeOkx):
    def __init__(self):
        Connector.__init__(self)
        FakeOkx.__init__(self)


@pytest.mark.asyncio
async def test_lost_ack_terminal_proof_allows_one_fresh_quote_replacement(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    _, first_executor = _attach_quote_planner(
        controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT",
        clock=lambda: controller._order_safety_manager.wall_clock(),
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, reservations).check)
    controller.install_order_safety(controller._order_safety_manager, gateway, wal,
                                    reservations=reservations)
    executors = []
    runner = _runner(controller, connector, executors, first_executor.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        assert wal.get(first_executor.config.id).state == "SEND_UNKNOWN"  # Lost ACK.
        connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                     "state": "canceled", "accFillSz": "0"}
        connector.fills["exchange-1"] = []
        connector.open_pages[None] = []
        connector.cash_balances = {"LIFE": "10", "USDT": "10"}
        session = controller._order_safety_manager.current_session
        assert await SpotAccountReconciler(connector, reservations).check()
        assert await gateway._account_scope_complete({wire_id}, {wire_id: "exchange-1"})
        assert reservations.has_open_intent(first_executor.config.id)
        controller._runner_halt_ok = controller._halt_runner_orders()
        assert controller._runner_halt_ok
        result = await gateway.reconcile(session.session_id, session.epoch)
        assert result.scope_complete and result.trade_events_reconciled
        assert not result.open_order_ids and not result.unknown_order_ids
        assert wal.get(first_executor.config.id).state == "TERMINAL"
        assert reservations.is_terminal_intent(first_executor.config.id)
        executors[0]._status = RunnableStatus.TERMINATED
        controller._quote_action_planner.intent_id_factory = lambda: "quote-replacement"
        replacement = controller.determine_executor_actions()
        assert len(replacement) == 1
        runner.determine_executor_actions = lambda: replacement
        runner.tick(2)
        assert len(connector.sent) == 2
        assert connector.sent[1]["order_id"] != wire_id
        assert wal.get("quote-replacement").state == "SEND_UNKNOWN"
        assert reservations.has_open_intent("quote-replacement")
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


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
async def test_partial_fill_fee_cancel_and_expiry_survive_journal_restart(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    manager = controller._order_safety_manager
    wall_time = [manager.wall_clock()]
    monotonic_time = [manager.monotonic_clock()]
    manager.wall_clock = lambda: wall_time[0]
    manager.monotonic_clock = lambda: monotonic_time[0]
    _, proposed = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
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
        connector.algo_pages["conditional,oco"] = {"code": "0", "data": [
            {"algoId": "manual-1", "instType": "SPOT", "instId": "BTC-USDT"}]}
        runner.tick(3)
        await controller.order_safety_task
        assert wal.get(executors[0].config.id).state != "TERMINAL"
        assert reservations.requires_reconciliation(executors[0].config.id)

        connector.algo_pages["conditional,oco"] = {"code": "0", "data": []}
        runner.tick(4)
        await controller.order_safety_task
        assert wal.get(executors[0].config.id).state != "TERMINAL"
        assert reservations.requires_reconciliation(executors[0].config.id)
        assert reservations.trade_intent_id("trade-1") is None

        connector.fills["exchange-1"][0].update(
            fee="-0.01", feeCcy="USDT",
            fillTime=str(int(wall_time[0].timestamp() * 1000)))
        connector.cash_balances["USDT"] = "9.594"
        runner.tick(5)
        await controller.order_safety_task
        assert wal.get(executors[0].config.id).state == "TERMINAL"
        assert not reservations.requires_reconciliation(executors[0].config.id)
        assert reservations.is_terminal_intent(executors[0].config.id)
        assert reservations.life_balance == Decimal("10.4")
        assert reservations.usdt_balance == Decimal("9.594")
        assert reservations.fee_for_trade("trade-1") == ("USDT", Decimal("-0.01"))
        restarted = type(reservations).restore(reservations.path, limits=reservations.limits)
        assert restarted.life_balance == Decimal("10.4")
        assert restarted.usdt_balance == Decimal("9.594")
        assert restarted.trade_intent_id("trade-1") == executors[0].config.id
        assert restarted.fee_for_trade("trade-1") == ("USDT", Decimal("-0.01"))
        assert type(wal)(wal.path).get(executors[0].config.id).state == "TERMINAL"
        restarted_clock = FakeClock()
        restarted_clock.wall = wall_time[0]
        restarted_clock.mono = monotonic_time[0]
        restored_session = session_manager(tmp_path, restarted_clock)
        assert restored_session.state == "EXPIRED"
        assert not restored_session.can_quote(reference_ready=True, all_gates_ready=True)
        assert len(connector.sent) == 1
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_filled_old_quote_reconciles_before_successor_quote(tmp_path):
    connector = FakeTradingOkx()
    controller, template, _, wal, reservations, _ = _setup(tmp_path, connector=connector)
    connector.bill_pages[None] = [bill("100")]
    bills = SpotBillReconciler(
        connector, reservations, wal,
        CashflowApprovals.load(approval_file(tmp_path)))
    clock = FakeClock()
    manager = transition_manager(tmp_path, clock)
    primary = start(manager)
    controller._protected_spot_sender.manager = manager
    reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: clock.wall,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, reservations, bills=bills).check)
    controller.install_order_safety(manager, gateway, wal, reservations=reservations)
    state, proposed = _attach_quote_planner(controller, template, wal, reservations)
    controller._spot_quote_gates_ready = lambda: True
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        assert wal.get(proposed.config.id).state == "SEND_UNKNOWN"  # No create ACK.
        connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                     "state": "partially_filled", "accFillSz": "0.4"}
        connector.fills["exchange-1"] = [
            {"tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
             "fillPx": "0.99", "fee": "-0.01", "feeCcy": "USDT",
             "fillTime": str(int(clock.wall.timestamp() * 1000))}]
        connector.bill_pages[None] = [
            bill("101", kind="2", subtype="1", currency="USDT", change="-0.01",
                 trade_id="trade-1", order_id="exchange-1", inst_id="LIFE-USDT",
                 fee="-0.01"),
            bill("100")]
        owned_fill = {"billId": "101", "tradeId": "trade-1", "ordId": "exchange-1",
                      "clOrdId": wire_id, "instType": "SPOT", "instId": "LIFE-USDT",
                      "fillSz": "0.4", "fillPx": "0.99", "feeCcy": "USDT",
                      "fee": "-0.01"}
        connector.all_fill_history_pages[None] = [owned_fill]
        connector.cash_balances = {"LIFE": "10.4", "USDT": "9.594"}
        connector.open_pages[None] = [
            {"clOrdId": wire_id, "ordId": "exchange-1", "instId": "LIFE-USDT",
             "state": "partially_filled"}]
        clock.advance(10)
        state["now"] = 110
        state["snapshot"] = replace(state["snapshot"], observed_monotonic=109,
                                    expires_monotonic=111)
        runner.tick(2)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"
        assert manager.current_session.session_id == primary.session_id
        assert connector.cancels == [("LIFE-USDT", wire_id)]
        assert reservations.requires_reconciliation(proposed.config.id)

        connector.status[wire_id]["state"] = "canceled"
        connector.open_pages[None] = []
        connector.algo_pages["conditional,oco"] = {"code": "0", "data": [
            {"algoId": "foreign-algo", "instType": "SPOT", "instId": "BTC-USDT"}]}
        state["snapshot"] = replace(state["snapshot"], market_anchor_usdt=Decimal("1.02"))
        runner.tick(3)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"
        assert wal.get(proposed.config.id).state != "TERMINAL"

        connector.algo_pages["conditional,oco"] = {"code": "0", "data": []}
        connector.all_fill_history_pages[None] = [
            {**owned_fill, "billId": "102", "tradeId": "foreign-trade",
             "ordId": "foreign-order", "clOrdId": "", "instId": "BTC-USDT"},
            owned_fill]
        runner.tick(4)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"
        assert wal.get(proposed.config.id).state != "TERMINAL"

        connector.all_fill_history_pages[None] = [owned_fill]
        state["snapshot"] = replace(state["snapshot"], market_anchor_usdt=None)
        runner.tick(5)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"
        assert manager.reason_code == "MARKET_REFERENCE_UNAVAILABLE"
        assert wal.get(proposed.config.id).state == "TERMINAL"

        state["snapshot"] = replace(state["snapshot"], market_anchor_usdt=Decimal("NaN"))
        runner.tick(6)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"
        assert manager.reason_code == "MARKET_REFERENCE_UNAVAILABLE"

        state["snapshot"] = replace(
            state["snapshot"], market_anchor_usdt=Decimal("1.02"), market_reference_ready=False)
        runner.tick(7)
        await controller.order_safety_task
        assert manager.state == "TRANSITIONING"

        state["snapshot"] = replace(state["snapshot"], market_reference_ready=True)
        runner.tick(8)
        await controller.order_safety_task
        assert manager.state == "ACTIVE"
        successor = manager.current_session
        assert successor.session_id != primary.session_id
        assert successor.epoch == primary.epoch + 1
        assert successor.anchors["LIFE-USDT"] == Decimal("1.02")
        assert reservations.fee_for_trade("trade-1") == ("USDT", Decimal("-0.01"))
        assert reservations.life_balance == Decimal("10.4")
        assert reservations.usdt_balance == Decimal("9.594")

        state["snapshot"] = replace(
            state["snapshot"], session_id=successor.session_id, epoch=successor.epoch,
            qualified_reference_usdt=Decimal("1.02"),
            qualified_exit_value_usdt=Decimal("1.02"),
            best_bid_usdt=Decimal("1.00"), best_ask_usdt=Decimal("1.04"))
        executors[0]._status = RunnableStatus.TERMINATED
        controller._quote_action_planner.intent_id_factory = lambda: "successor-quote"
        replacement = controller.determine_executor_actions()
        assert len(replacement) == 1
        runner.determine_executor_actions = lambda: replacement
        runner.tick(9)
        assert len(connector.sent) == 2
        assert connector.sent[1]["order_id"] != wire_id
        assert wal.get("successor-quote").session_id == successor.session_id
        assert wal.get("successor-quote").state == "SEND_UNKNOWN"
        assert reservations.has_open_intent("successor-quote")

        restarted_clock = FakeClock()
        restarted_clock.wall = clock.wall
        restarted_clock.mono = clock.mono
        assert transition_manager(tmp_path, restarted_clock).current_session == successor
        restored = type(reservations).restore(reservations.path, limits=reservations.limits)
        assert restored.fee_for_trade("trade-1") == ("USDT", Decimal("-0.01"))
        assert restored.has_open_intent("successor-quote")
        assert type(wal)(wal.path).get(proposed.config.id).state == "TERMINAL"
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
