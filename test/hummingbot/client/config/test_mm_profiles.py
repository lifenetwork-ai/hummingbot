from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from controllers.market_making.life_mm import LifeMMController
from hummingbot.client.config.config_helpers import load_yml_into_dict
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

CATALOGUE = Path(__file__).resolve().parents[4] / "deploy/cloudrun/conf/life_mm_profiles.yaml"


@pytest.mark.asyncio
async def test_deployment_packages_three_default_profiles_and_two_market_templates():
    data = await load_yml_into_dict(str(CATALOGUE))
    assert data.get("reference_connector") == "okx"
    assert set(data["profiles"]) == {"market_follow", "sideways_default", "trend_eth"}
    assert set(data["markets"]) == {"spot", "perpetual"}
    assert data["markets"]["spot"]["leverage"] == 1
    assert data["markets"]["perpetual"]["position_mode"] == "HEDGE"


@pytest.mark.asyncio
async def test_new_supported_reference_can_override_yaml_without_mutating_defaults():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    supported = {"okx": {"LIFE-USDT", "AVAX-USDT"}, "okx_perpetual": {"LIFE-USDT"}}
    configs = catalogue.controller_configs("trend_eth", "both", supported,
                                           references=[{"pair": "AVAX-USDT", "weight": "1"}],
                                           available_quote=Decimal("200"))
    assert len(configs) == 2
    assert {config.connector_name for config in configs} == {"okx", "okx_perpetual"}
    assert all(config.references[0].pair == "AVAX-USDT" and config.runtime_config_managed for config in configs)
    assert catalogue.profiles["trend_eth"].references[0].pair == "ETH-USDT"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,supported,available", [
    ("both", {"okx": {"LIFE-USDT"}}, "200"),
    ("both", {"okx": {"LIFE-USDT"}, "okx_perpetual": {"LIFE-USDT"}}, "199"),
    ("paper", {"okx": {"LIFE-USDT"}}, "200"),
])
async def test_selection_rejects_missing_market_and_shared_collateral_overallocation(mode, supported, available):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    with pytest.raises(ValueError):
        catalogue.controller_configs("sideways_default", mode, supported, available_quote=Decimal(available))


@pytest.mark.asyncio
async def test_unknown_reference_and_non_usdt_override_are_rejected():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    supported = {"okx": {"LIFE-USDT", "BTC-USDT"}}
    for pair in ("UNKNOWN-USDT", "BTC-USDC"):
        with pytest.raises(ValueError):
            catalogue.controller_configs("trend_eth", "spot", supported,
                                         references=[{"pair": pair, "weight": "1"}])


@pytest.mark.asyncio
async def test_duration_and_basket_limits():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    assert catalogue.validate_duration(3600) == 3600
    for duration in (True, 0, 86401, 60.5):
        with pytest.raises(ValueError):
            catalogue.validate_duration(duration)
    catalogue.markets["spot"].max_reference_pairs = 1
    with pytest.raises(ValueError):
        catalogue.controller_configs("market_follow", "spot", {"okx": {"LIFE-USDT", "BTC-USDT", "ETH-USDT"}})


@pytest.mark.asyncio
async def test_existing_v2_loader_discovers_controller_and_periodic_reload_preserves_api_selection(tmp_path):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    yaml_config = catalogue.markets["spot"].model_dump(mode="json")
    (tmp_path / "life.yaml").write_text(yaml.safe_dump(yaml_config))
    runner_config = StrategyV2ConfigBase(controllers_config=["life.yaml"])
    with patch("hummingbot.client.settings.CONTROLLERS_CONF_DIR_PATH", tmp_path):
        loaded = runner_config.load_controller_configs()
        assert loaded[0].get_controller_class() is LifeMMController
        runtime_config = catalogue.controller_configs(
            "sideways_default", "spot", {"okx": {"LIFE-USDT"}}, available_quote=Decimal("200"))[0]
        runtime_config.total_amount_quote = Decimal("20")
        controller = LifeMMController(runtime_config, MagicMock(), MagicMock())
        strategy = MagicMock()
        strategy.config = runner_config
        strategy.controllers = {runtime_config.id: controller}
        strategy.current_timestamp = 100
        strategy.config_update_interval = 1
        strategy._last_config_update_ts = 0
        StrategyV2Base.update_controllers_configs(strategy)
        assert controller.config.total_amount_quote == Decimal("20")
