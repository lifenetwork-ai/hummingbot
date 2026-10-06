"""P2.9 runner contract: safety scheduling is independent of quote readiness."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.strategy.strategy_v2_base import StrategyV2Base


def _controller(controller_id):
    config = LifeLiquidityConfig.model_construct(id=controller_id)
    return LifeLiquidityController(config, MagicMock(), MagicMock())


def _runner(controllers):
    connector = SimpleNamespace(ready=False, name="okx")
    provider = SimpleNamespace(ready=False)
    logger = MagicMock()
    runner = SimpleNamespace(
        controllers=controllers, connectors={"okx": connector},
        market_data_provider=provider, ready_to_trade=False,
        _is_stop_triggered=False, logger=lambda: logger,
        executor_orchestrator=MagicMock(),
        update_executors_info=MagicMock(), update_controllers_configs=MagicMock(),
        determine_executor_actions=MagicMock(return_value=[]),
    )
    runner.on_tick = lambda: StrategyV2Base.on_tick(runner)
    runner._run_safety_callbacks = lambda timestamp: StrategyV2Base._run_safety_callbacks(runner, timestamp)
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)
    return runner, connector, provider


def test_safety_callback_runs_when_connector_feed_or_executor_event_is_unready():
    controller = _controller("life")
    times = []
    controller.on_safety_tick = times.append
    runner, connector, provider = _runner({"life": controller})

    StrategyV2Base.tick(runner, 1)
    assert times == [1]
    assert runner.ready_to_trade is False

    connector.ready = True
    runner.ready_to_trade = True
    controller.executors_update_event.clear()
    StrategyV2Base.tick(runner, 2)
    assert times == [1, 2]
    assert provider.ready is False
    runner.determine_executor_actions.assert_not_called()

    provider.ready = True
    StrategyV2Base.tick(runner, 3)
    assert times == [1, 2, 3]
    assert controller.executors_update_event.is_set() is False
    runner.executor_orchestrator.execute_action.assert_not_called()


def test_safety_callback_failure_does_not_skip_other_life_controllers():
    first, second = _controller("first"), _controller("second")
    calls = []
    first.on_safety_tick = MagicMock(side_effect=RuntimeError("stub failed"))
    second.on_safety_tick = calls.append
    runner, _, _ = _runner({"first": first, "second": second})

    StrategyV2Base.tick(runner, 4)

    first.on_safety_tick.assert_called_once_with(4)
    assert calls == [4]
    runner.logger().error.assert_called_once()


@pytest.mark.asyncio
async def test_actual_strategy_runner_dispatches_safety_hook_before_readiness_gates():
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: MagicMock()):
        runner = StrategyV2Base({}, config=None)
    controller = _controller("life")
    calls = []
    controller.on_safety_tick = calls.append
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": SimpleNamespace(ready=False, name="okx")}
    runner.market_data_provider = SimpleNamespace(ready=False)
    try:
        runner.tick(1)
        assert calls == [1]
        assert runner.ready_to_trade is False

        runner.ready_to_trade = True
        controller.executors_update_event.clear()
        runner.update_executors_info = MagicMock()
        runner.update_controllers_configs = MagicMock()
        runner.tick(2)
        assert calls == [1, 2]

        runner.market_data_provider.ready = True
        runner.determine_executor_actions = MagicMock(return_value=[])
        runner.tick(3)
        assert calls == [1, 2, 3]
        assert not controller.executors_update_event.is_set()
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
