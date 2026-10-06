"""P5.7 safety checks at the actual V2 runner and OrderExecutor boundary."""

import asyncio
import json
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import _controller, _install
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors import TrackedOrder


def _executor(wire_id="wire-1", *, intent_id="i1"):
    strategy = MagicMock(spec=StrategyV2Base)
    strategy.connectors = {"okx": MagicMock()}
    config = OrderExecutorConfig(
        id=intent_id, timestamp=1.0, controller_id="life", side=TradeType.BUY,
        connector_name="okx", trading_pair="LIFE-USDT", amount=Decimal("1"),
        price=Decimal("1"), execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    executor = OrderExecutor(strategy, config)
    executor._status = RunnableStatus.RUNNING
    executor._order = TrackedOrder(wire_id)
    return executor


def _stored_info(wire_ids=("wire-1",), *, intent_id="i1", pair="LIFE-USDT",
                 include_history=True):
    executor = _executor(wire_ids[0] if wire_ids else None)
    executor.config = executor.config.model_copy(update={"id": intent_id,
                                                         "trading_pair": pair})
    executor._status = RunnableStatus.TERMINATED
    info = executor.executor_info
    custom_info = dict(info.custom_info)
    if include_history:
        custom_info["recovery_order_ids"] = list(wire_ids)
    else:
        custom_info.pop("recovery_order_ids", None)
    return info.model_copy(update={"id": intent_id, "custom_info": custom_info})


def _runner(controller, orchestrator, *, stored=()):
    orchestrator.get_stored_executors_by_controller = lambda _controller_id: stored
    runner = SimpleNamespace(controllers={"life": controller}, executor_orchestrator=orchestrator,
                             logger=lambda: MagicMock())
    runner._run_safety_callbacks = lambda timestamp: StrategyV2Base._run_safety_callbacks(runner, timestamp)
    return runner


def test_runner_does_not_halt_executor_during_authorized_active_session(tmp_path):
    controller = _controller()
    _install(controller, tmp_path, FakeClock(), FakeOkx())
    controller.trading_permissions_ready = lambda: True
    controller.on_safety_tick = lambda _: None
    executor = _executor()
    runner = _runner(controller, SimpleNamespace(active_executors={"life": [executor]}))

    runner._run_safety_callbacks(10)

    assert executor.status == RunnableStatus.RUNNING


@pytest.mark.asyncio
async def test_runner_halts_before_cancel_when_session_revokes_on_this_tick(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    controller = _controller()
    _install(controller, tmp_path, FakeClock(), connector)
    controller.trading_permissions_ready = lambda: True
    executor = _executor()
    gateway = controller._order_safety_gateway
    original_cancel = gateway.request_cancel

    async def cancel_after_halt(session_id, epoch):
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        await original_cancel(session_id, epoch)

    gateway.request_cancel = cancel_after_halt
    runner = _runner(controller, SimpleNamespace(active_executors={"life": [executor]}))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_runner_halts_known_executor_before_gateway_reconciles(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, clock, connector)
    executor = _executor()
    runner = _runner(controller, SimpleNamespace(active_executors={"life": [executor]}))

    runner._run_safety_callbacks(10)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    await controller.order_safety_task
    assert wal.get("i1").state == "TERMINAL"
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    assert not controller.allow_create_executor_actions()

    # An executor cancel event clears its tracked order. A running executor
    # would replenish it on the next control task; safety shutdown must not.
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id="wire-1"))
    executor._sleep = AsyncMock()
    await executor.control_task()
    executor._strategy.buy.assert_not_called()
    executor._strategy.sell.assert_not_called()
    assert executor.status == RunnableStatus.TERMINATED


@pytest.mark.asyncio
async def test_real_strategy_tick_halts_executor_while_market_is_unready(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    controller = _controller()
    _, wal, reservations = _install(controller, tmp_path, clock, connector)
    executor = _executor()
    orchestrator = MagicMock()
    orchestrator.active_executors = {"life": [executor]}
    orchestrator.get_stored_executors_by_controller.return_value = ()
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: orchestrator):
        runner = StrategyV2Base({}, config=None)
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": SimpleNamespace(ready=False, name="okx")}
    runner.market_data_provider = SimpleNamespace(ready=False)
    try:
        runner.tick(10)
        assert executor.status == RunnableStatus.SHUTTING_DOWN
        await controller.order_safety_task
        assert wal.get("i1").state != "TERMINAL"
        assert reservations.requires_reconciliation("i1")
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
        orchestrator.execute_action.assert_not_called()
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_runner_unknown_executor_order_blocks_terminal_release(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, reservations = _install(controller, tmp_path, clock, connector)
    runner = _runner(controller, SimpleNamespace(active_executors={"life": [_executor("untracked")]}))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task
    assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
    assert wal.get("i1").state != "TERMINAL"
    assert reservations.requires_reconciliation("i1")
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_runner_executor_id_must_match_wal_intent(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    runner = _runner(controller, SimpleNamespace(active_executors={
        "life": [_executor(intent_id="wrong-intent")]}))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert wal.get("i1").state != "TERMINAL"
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_untracked_executor_cannot_disappear_before_scope_check(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    orchestrator = SimpleNamespace(active_executors={"life": [_executor("untracked")]})
    runner = _runner(controller, orchestrator)

    runner._run_safety_callbacks(10)
    orchestrator.active_executors["life"] = []
    await controller.order_safety_task

    assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
    assert wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_runner_missing_executor_snapshot_blocks_terminal_release(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, clock, connector)
    runner = _runner(controller, SimpleNamespace())

    runner._run_safety_callbacks(10)
    await controller.order_safety_task
    assert wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_runner_unsupported_executor_blocks_completion_but_still_cancels_wal(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, clock, connector)
    runner = _runner(controller, SimpleNamespace(active_executors={"life": [MagicMock()]}))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert wal.get("i1").state != "TERMINAL"
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_stored_executor_scope_survives_empty_active_map_after_restart(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    runner = _runner(controller, SimpleNamespace(active_executors={"life": []}),
                     stored=(_stored_info(),))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert wal.get("i1").state == "TERMINAL"
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_real_strategy_tick_uses_stored_executor_scope_when_market_unready(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    orchestrator = MagicMock()
    orchestrator.active_executors = {"life": []}
    orchestrator.get_stored_executors_by_controller.return_value = (_stored_info(),)
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: orchestrator):
        runner = StrategyV2Base({}, config=None)
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": SimpleNamespace(ready=False, name="okx")}
    runner.market_data_provider = SimpleNamespace(ready=False)
    try:
        runner.tick(10)
        await controller.order_safety_task
        assert wal.get("i1").state == "TERMINAL"
        orchestrator.get_stored_executors_by_controller.assert_called_with("life")
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [
    lambda: (_stored_info(("untracked",)),),
    lambda: (_stored_info(include_history=False),),
    lambda: (_stored_info(pair="OTHER-USDT"),),
    lambda: (_stored_info(), _stored_info()),
    lambda: (_stored_info((), intent_id="untracked-intent"),),
])
async def test_untrusted_stored_executor_blocks_release_but_cancels_wal(tmp_path, stored):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, reservations = _install(controller, tmp_path, FakeClock(), connector)
    runner = _runner(controller, SimpleNamespace(active_executors={"life": []}),
                     stored=stored())

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
    assert wal.get("i1").state != "TERMINAL"
    assert reservations.requires_reconciliation("i1")
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_stored_executor_database_error_blocks_release(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    orchestrator = SimpleNamespace(active_executors={"life": []})
    runner = _runner(controller, orchestrator)
    orchestrator.get_stored_executors_by_controller = (
        lambda _controller_id: (_ for _ in ()).throw(OSError("database unavailable")))

    runner._run_safety_callbacks(10)
    await controller.order_safety_task

    assert wal.get("i1").state != "TERMINAL"
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_stored_executor_disappearance_latches_incomplete_scope(tmp_path):
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    controller = _controller()
    _, wal, _ = _install(controller, tmp_path, FakeClock(), connector)
    stored_rows = [_stored_info()]
    orchestrator = SimpleNamespace(active_executors={"life": []})
    runner = _runner(controller, orchestrator)
    orchestrator.get_stored_executors_by_controller = lambda _: tuple(stored_rows)

    runner._run_safety_callbacks(10)
    await controller.order_safety_task
    assert wal.get("i1").state != "TERMINAL"

    stored_rows.clear()
    connector.status["wire-1"]["state"] = "canceled"
    runner._run_safety_callbacks(11)
    await controller.order_safety_task

    assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
    assert wal.get("i1").state != "TERMINAL"


def test_order_executor_reports_current_renewed_and_held_wire_ids():
    executor = _executor("current")
    executor._partial_filled_orders = [TrackedOrder("renewed")]
    executor._canceled_orders = [TrackedOrder("canceled")]
    executor._failed_orders = [TrackedOrder("failed")]
    executor._held_position_orders = [{"client_order_id": "held"}]

    assert executor.recovery_order_ids() == (
        "current", "renewed", "canceled", "failed", "held")
    assert executor.executor_info.custom_info["recovery_order_ids"] == [
        "current", "renewed", "canceled", "failed", "held"]
    stored = json.loads(executor.executor_info.model_dump_json())
    assert stored["custom_info"]["recovery_order_ids"] == [
        "current", "renewed", "canceled", "failed", "held"]


def test_orchestrator_reads_stored_executor_snapshot_from_recorder():
    stored = _stored_info()
    recorder = MagicMock()
    recorder.get_executors_by_controller.return_value = [stored]
    with patch("hummingbot.strategy_v2.executors.executor_orchestrator.MarketsRecorder.get_instance",
               return_value=recorder):
        orchestrator = object.__new__(ExecutorOrchestrator)
        assert orchestrator.get_stored_executors_by_controller("life") == (stored,)
    recorder.get_executors_by_controller.assert_called_once_with("life")


def test_direct_runner_tick_rechecks_create_permission_before_dispatch():
    controller = _controller()
    action = CreateExecutorAction.model_construct(controller_id="life", executor_config=None)
    runner = SimpleNamespace(
        controllers={"life": controller}, market_data_provider=SimpleNamespace(ready=True),
        _is_stop_triggered=False, executor_orchestrator=MagicMock(),
        update_executors_info=MagicMock(), update_controllers_configs=MagicMock(),
        determine_executor_actions=MagicMock(return_value=[action]),
        logger=lambda: MagicMock())
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)

    StrategyV2Base.on_tick(runner)

    runner.executor_orchestrator.execute_action.assert_not_called()
