"""Integration checks against Hummingbot's real CLI, controller, and V2 runner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.cli import bot, strategy_configs
from hummingbot.cli.main import app
from hummingbot.cli.strategy_configs import available_controllers, describe_strategy, edit_config, validate_controller
from hummingbot.client import settings
from hummingbot.client.config import config_helpers
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


def _controller():
    config = LifeLiquidityConfig.model_construct(id="life-test")
    return LifeLiquidityController(config, MagicMock(), MagicMock())


def test_real_cli_discovers_and_round_trips_nested_controller(tmp_path):
    assert "life_liquidity" in available_controllers()
    data, required, updatable = describe_strategy("controller", "life_liquidity")
    assert required == []
    assert "strategy" not in updatable
    assert data["strategy"]["session"]["duration"] == "4h"
    config_path = tmp_path / "life.yml"
    config_path.write_text(yaml.safe_dump(data))

    config, _ = validate_controller(config_path)
    assert isinstance(config, LifeLiquidityConfig)
    assert config.strategy.session.duration == "4h"
    value, _ = edit_config(config_path, "controller", "strategy.session.duration", "2h")
    assert value == "2h"
    updated, _ = validate_controller(config_path)
    assert updated.strategy.session.duration == "2h"


def test_hbot_create_command_writes_valid_nested_life_config(tmp_path, monkeypatch):
    monkeypatch.setitem(strategy_configs.TYPE_DIRS, "controller", tmp_path)
    loaded = []
    monkeypatch.setattr(bot, "write_loaded", lambda file, kind: loaded.append((file, kind)))

    result = CliRunner().invoke(app, [
        "create", "life_liquidity", "--controller", "--name", "life_cli.yml",
        "--set", "strategy.session.duration=2h",
    ])

    assert result.exit_code == 0, result.output
    assert loaded == [("life_cli.yml", "controller")]
    config, _ = validate_controller(tmp_path / "life_cli.yml")
    assert config.strategy.session.duration == "2h"
    assert config.strategy.execution_mode == "simulation"


def test_hbot_config_command_edits_nested_field_and_rolls_back_invalid_value(tmp_path, monkeypatch):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    path = tmp_path / "life_cli.yml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setitem(strategy_configs.TYPE_DIRS, "controller", tmp_path)
    monkeypatch.setattr(bot, "running", lambda: False)
    monkeypatch.setattr(bot, "read_loaded", lambda: {"file": path.name, "type": "controller"})
    monkeypatch.setattr(config_helpers, "load_client_config_map_from_file", lambda: SimpleNamespace(config_paths=lambda: []))

    valid = CliRunner().invoke(app, ["config", "strategy.session.duration", "3h"])
    assert valid.exit_code == 0, valid.output
    assert validate_controller(path)[0].strategy.session.duration == "3h"
    before_invalid = path.read_text()

    invalid = CliRunner().invoke(app, ["config", "strategy.session.duration", "invalid"])
    assert invalid.exit_code != 0
    assert path.read_text() == before_invalid


def test_real_cli_rejects_invalid_nested_edit_without_changing_file(tmp_path):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    config_path = tmp_path / "life.yml"
    config_path.write_text(yaml.safe_dump(data))
    before = config_path.read_text()

    with pytest.raises(Exception):
        edit_config(config_path, "controller", "strategy.session.duration", "invalid")

    assert config_path.read_text() == before


def test_real_v2_loader_resolves_life_controller_class(tmp_path, monkeypatch):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    (tmp_path / "life.yml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(settings, "CONTROLLERS_CONF_DIR_PATH", tmp_path)

    loaded = StrategyV2ConfigBase(controllers_config=["life.yml"]).load_controller_configs()

    assert len(loaded) == 1
    assert isinstance(loaded[0], LifeLiquidityConfig)
    assert loaded[0].get_controller_class() is LifeLiquidityController


def test_runner_notifies_life_controller_when_config_loading_fails(tmp_path, monkeypatch):
    controller = _controller()
    data = controller.config.model_dump(mode="json")
    data["strategy"]["session"]["duration"] = "invalid"
    (tmp_path / "life.yml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(settings, "CONTROLLERS_CONF_DIR_PATH", tmp_path)

    runner = SimpleNamespace(
        config=StrategyV2ConfigBase(controllers_config=["life.yml"]),
        controllers={controller.config.id: controller},
        _last_config_update_ts=0, config_update_interval=10, current_timestamp=11,
        logger=lambda: MagicMock(),
    )

    StrategyV2Base.update_controllers_configs(runner)

    assert controller.config_update_state.last_rejection.reason_code == "CONFIG_LOAD_FAILED"
    assert controller.config_update_state.config is controller.config.strategy
    assert controller.config_update_state.order_permission() is False


def test_runner_filters_queued_create_action_after_invalid_config():
    controller = _controller()
    controller.trading_permissions_ready = lambda: True
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]

    controller.on_config_load_failure()

    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []


def test_runner_keeps_unmanaged_script_create_actions():
    action = CreateExecutorAction.model_construct(controller_id="main", executor_config=None)
    runner = SimpleNamespace(controllers={}, logger=lambda: MagicMock())
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]


@pytest.mark.asyncio
async def test_real_runner_listener_drops_queued_create_after_rejection():
    controller = _controller()
    controller.on_config_load_failure()
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)

    class OneActionQueue:
        def __init__(self):
            self.delivered = False

        async def get(self):
            if self.delivered:
                await asyncio.Future()
            self.delivered = True
            return [action]

    queue = OneActionQueue()
    orchestrator = MagicMock()
    runner = SimpleNamespace(
        controllers={controller.config.id: controller}, actions_queue=queue,
        executor_orchestrator=orchestrator, logger=lambda: MagicMock(),
    )
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)

    task = asyncio.create_task(StrategyV2Base.listen_to_executor_actions(runner))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.delivered is True
    orchestrator.execute_actions.assert_not_called()
