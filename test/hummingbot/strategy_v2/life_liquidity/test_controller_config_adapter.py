"""Isolated adapter contract tests; full Hummingbot CLI imports need the repository environment."""

import importlib.util
import inspect
import sys
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.config_test_support import load_config_module, simulation_config_data
from types import ModuleType

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


@pytest.fixture
def adapter_module(monkeypatch):
    class StubControllerConfigBase(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str
        controller_type: str = "generic"
        controller_name: str = ""
        total_amount_quote: Decimal = Decimal("100")

    class StubControllerBase:
        def __init__(self, config, *args, **kwargs):
            self.config = config

    base_module = ModuleType("hummingbot.strategy_v2.controllers.controller_base")
    base_module.ControllerConfigBase = StubControllerConfigBase
    base_module.ControllerBase = StubControllerBase
    monkeypatch.setitem(sys.modules, base_module.__name__, base_module)
    monkeypatch.setitem(
        sys.modules, "hummingbot.strategy_v2.life_liquidity.config", load_config_module(),
    )
    path = Path(__file__).resolve().parents[4] / "controllers/generic/life_liquidity.py"
    spec = importlib.util.spec_from_file_location("life_liquidity_controller_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_controller_is_discoverable_by_v2_class_selection(adapter_module):
    config_classes = [member for _, member in inspect.getmembers(adapter_module, inspect.isclass)
                      if issubclass(member, adapter_module.ControllerConfigBase)
                      and member is not adapter_module.ControllerConfigBase]
    assert config_classes == [adapter_module.LifeLiquidityConfig]
    config = adapter_module.LifeLiquidityConfig.model_construct(id="offline")
    assert config.controller_type == "generic"
    assert config.controller_name == "life_liquidity"
    assert config.strategy.execution_mode == "simulation"
    assert config.total_amount_quote == Decimal("0")


def test_nested_yaml_and_json_round_trip_without_unit_changes(adapter_module):
    data = {
        "id": "life-offline",
        "controller_type": "generic",
        "controller_name": "life_liquidity",
        "strategy": simulation_config_data(),
    }
    data["strategy"]["session"]["duration"] = "12h"
    data["strategy"]["reference"]["lookback"] = "30m"
    data["strategy"]["quotes"]["sizes_base"] = ["0.123456789012345678"]
    config = adapter_module.LifeLiquidityConfig.model_validate(data)
    encoded = yaml.safe_dump(config.model_dump(mode="json"))
    restored = adapter_module.LifeLiquidityConfig.model_validate(yaml.safe_load(encoded))
    assert restored.strategy == config.strategy
    assert restored.strategy.quotes.sizes_base == (Decimal("0.123456789012345678"),)
    assert restored.strategy.session.duration_seconds == Decimal("43200")
    assert restored.strategy.reference.lookback_seconds == Decimal("1800")


def test_controller_example_loads_as_a_safe_simulation(adapter_module):
    path = Path(__file__).resolve().parents[4] / "docs/examples/life_liquidity/controller.simulation.yml.example"
    config = adapter_module.LifeLiquidityConfig.model_validate(yaml.safe_load(path.read_text()))
    assert config.controller_name == "life_liquidity"
    assert config.strategy.execution_mode == "simulation"
    assert config.total_amount_quote == Decimal("0")
    assert adapter_module.LifeLiquidityController(config).determine_executor_actions() == []


def test_invalid_nested_config_is_rejected_before_controller_creation(adapter_module):
    data = {"id": "offline", "strategy": simulation_config_data()}
    data["strategy"]["quotes"]["sizes_base"] = ["1", "2"]
    with pytest.raises(ValidationError):
        adapter_module.LifeLiquidityConfig.model_validate(data)


def test_adapter_cannot_generate_executor_actions(adapter_module):
    config = adapter_module.LifeLiquidityConfig.model_validate(
        {"id": "offline", "strategy": simulation_config_data()},
    )
    controller = adapter_module.LifeLiquidityController(config)
    assert controller.determine_executor_actions() == []
    assert "trading is disabled" in controller.to_format_status()[0]


def test_nested_hot_reload_is_explicitly_rejected_until_runner_gate_exists(adapter_module):
    config = adapter_module.LifeLiquidityConfig.model_validate(
        {"id": "offline", "strategy": simulation_config_data()},
    )
    controller = adapter_module.LifeLiquidityController(config)
    changed_data = config.model_dump(mode="json")
    changed_data["strategy"]["session"]["duration"] = "2h"
    changed = adapter_module.LifeLiquidityConfig.model_validate(changed_data)

    with pytest.raises(ValueError, match="nested hot reload is unavailable"):
        controller.update_config(changed)
    assert controller.config is config
    assert controller.config_update_state.last_rejection.reason_code == "CONFIG_UPDATE_UNSUPPORTED"
    assert controller.config_update_state.order_permission() is False
    assert controller.determine_executor_actions() == []


def test_loader_failure_records_reason_and_blocks_order_actions(adapter_module):
    config = adapter_module.LifeLiquidityConfig.model_validate(
        {"id": "offline", "strategy": simulation_config_data()},
    )
    controller = adapter_module.LifeLiquidityController(config)
    controller.trading_permissions_ready = lambda: True
    assert controller.allow_create_executor_actions() is True
    decision = controller.on_config_load_failure()
    assert decision.reason_code == "CONFIG_LOAD_FAILED"
    assert controller.config_update_state.config is config.strategy
    assert controller.allow_create_executor_actions() is False
