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
async def test_manual_sideways_range_survives_effective_catalogue_and_only_applies_to_its_profile():
    original = await MMProfilesConfig.load(CATALOGUE)
    raw = original.model_dump(mode="json")
    raw["profiles"]["sideways"]["sideways_range"] = dict(
        lower_price=".09", upper_price=".11", resume_lower_price=".095", resume_upper_price=".105")
    for market in raw["markets"].values():
        market["quote_guards"] = dict(enabled=True, max_deviation_bps="1000", resume_deviation_bps="500")
    catalogue = MMProfilesConfig.model_validate(raw)
    assert all(c.sideways_range.lower_price == Decimal(".09") for c in catalogue.controller_configs("sideways", "both", None))
    assert all(c.sideways_range is None for c in catalogue.controller_configs("follow_market", "both", None))
    raw["profiles"]["follow_market"]["sideways_range"] = raw["profiles"]["sideways"]["sideways_range"]
    with pytest.raises(ValueError):
        MMProfilesConfig.model_validate(raw)


@pytest.mark.asyncio
async def test_deployment_packages_three_default_profiles_and_two_market_templates():
    data = await load_yml_into_dict(str(CATALOGUE))
    assert data.get("reference_connector") == "okx"
    assert set(data["profiles"]) == {"follow_market", "sideways", "follow_token"}
    assert set(data["markets"]) == {"spot", "perpetual"}
    assert data["markets"]["spot"]["leverage"] == 1
    assert data["markets"]["perpetual"]["position_mode"] == "HEDGE"


@pytest.mark.asyncio
async def test_new_supported_reference_can_override_yaml_without_mutating_defaults():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    defaults = catalogue.model_dump(mode="json")
    supported = {"okx": {"LIFE-USDT", "AVAX-USDT"}, "okx_perpetual": {"LIFE-USDT"}}
    configs = catalogue.controller_configs("follow_token", "both", supported,
                                           references=[{"pair": "AVAX-USDT", "weight": "1"}],
                                           available_quote=Decimal("200"))
    assert len(configs) == 2
    assert {config.connector_name for config in configs} == {"okx", "okx_perpetual"}
    assert all(config.references[0].pair == "AVAX-USDT" and config.runtime_config_managed for config in configs)
    assert catalogue.model_dump(mode="json") == defaults


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,supported,available", [
    ("both", {"okx": {"LIFE-USDT"}}, "200"),
    ("both", {"okx": {"LIFE-USDT"}, "okx_perpetual": {"LIFE-USDT"}}, "199"),
    ("paper", {"okx": {"LIFE-USDT"}}, "200"),
])
async def test_selection_rejects_missing_market_and_shared_collateral_overallocation(mode, supported, available):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    with pytest.raises(ValueError):
        catalogue.controller_configs("sideways", mode, supported, available_quote=Decimal(available))


@pytest.mark.asyncio
async def test_unknown_reference_and_non_usdt_override_are_rejected():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    supported = {"okx": {"LIFE-USDT", "BTC-USDT"}}
    for pair in ("UNKNOWN-USDT", "BTC-USDC"):
        with pytest.raises(ValueError):
            catalogue.controller_configs("follow_token", "spot", supported,
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
        catalogue.controller_configs("follow_market", "spot", {"okx": {"LIFE-USDT", "BTC-USDT", "ETH-USDT"}})


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
            "sideways", "spot", {"okx": {"LIFE-USDT"}}, available_quote=Decimal("200"))[0]
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


@pytest.mark.asyncio
async def test_existing_runner_loads_two_effective_configs_without_writing_yaml(tmp_path):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    selected = catalogue.controller_configs("sideways", "both", None)
    config = StrategyV2ConfigBase(controller_config_overrides=[item.model_dump(mode="json") for item in selected])
    with patch("hummingbot.client.settings.CONTROLLERS_CONF_DIR_PATH", tmp_path):
        loaded = config.load_controller_configs()
    assert {item.connector_name for item in loaded} == {"okx", "okx_perpetual"}
    assert all(item.get_controller_class() is LifeMMController for item in loaded)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_random_defaults_use_explicit_profile_ids_including_custom_names():
    data = await load_yml_into_dict(str(CATALOGUE))
    data["profiles"]["basket_custom"] = data["profiles"]["follow_market"]
    data["random"] = {"profile_ids": ["basket_custom", "sideways"], "step_duration_seconds": 600}
    catalogue = MMProfilesConfig.model_validate(data)
    assert catalogue.random.profile_ids == ["basket_custom", "sideways"]
    assert "scenarios" not in catalogue.random.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("pool", [[], ["sideways", "sideways"], ["unknown_profile"]])
async def test_random_defaults_reject_empty_duplicate_and_unknown_profiles(pool):
    data = await load_yml_into_dict(str(CATALOGUE))
    data["random"] = {"profile_ids": pool, "step_duration_seconds": 600}
    with pytest.raises(ValueError):
        MMProfilesConfig.model_validate(data)
