import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from test.hummingbot.strategy_v2.life_liquidity.config_test_support import (
    load_config_module,
    simulation_config_data,
)


config_module = load_config_module()


def test_simulation_defaults_are_nontrading_and_decimal_precise():
    config = config_module.StrategyConfig.model_validate(simulation_config_data())
    assert config.execution_mode == "simulation"
    assert config.spot.pair == "LIFE-USDT"
    assert config.session.duration_seconds == Decimal("14400")
    assert config.reference.lookback_seconds == Decimal("900")
    assert config.quotes.spreads_bps == (Decimal("30"),)
    assert config.quotes.sizes_base == (Decimal("10"),)


def test_decimal_values_survive_json_round_trip():
    data = simulation_config_data()
    data["quotes"]["sizes_base"] = ["0.123456789012345678"]
    config = config_module.StrategyConfig.model_validate(data)
    encoded = json.dumps(config.model_dump(mode="json"))
    restored = config_module.StrategyConfig.model_validate_json(encoded)
    assert restored.quotes.sizes_base == (Decimal("0.123456789012345678"),)


def test_unknown_top_level_and_nested_fields_are_rejected():
    data = simulation_config_data()
    data["magic_live_override"] = True
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    del data["magic_live_override"]
    data["session"]["reset_clock_on_restart"] = True
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_spread_and_size_level_counts_must_match():
    data = simulation_config_data()
    data["quotes"]["sizes_base"] = ["1", "2"]
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0"])
def test_quote_sizes_must_be_finite_and_positive(bad):
    data = simulation_config_data()
    data["quotes"]["sizes_base"] = [bad]
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_benchmark_weights_and_currency_must_be_supported():
    data = simulation_config_data()
    data["reference"] = {
        "mode": "bounded_benchmark", "lookback": "15m", "influence": "0.1",
        "sources": [{"connector": "okx", "pair": "BTC-USDT", "quote_currency": "USDT", "weight": "0.4"}],
    }
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["reference"]["sources"][0]["weight"] = "1"
    data["reference"]["sources"][0]["quote_currency"] = "BTC"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_benchmark_basket_requires_a_later_feature_gate():
    data = simulation_config_data()
    data["reference"] = {
        "mode": "bounded_benchmark", "lookback": "15m", "influence": "0.1",
        "sources": [
            {"connector": "okx", "pair": "BTC-USDT", "quote_currency": "USDT", "weight": "0.5"},
            {"connector": "okx", "pair": "ETH-USDT", "quote_currency": "USDT", "weight": "0.5"},
        ],
    }
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_synthetic_bootstrap_is_simulation_only():
    data = simulation_config_data()
    data["execution_mode"] = "shadow"
    data["reference"]["mode"] = "bootstrap_simulation"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_successor_duration_is_required_and_finite_for_switch():
    data = simulation_config_data()
    data["session"]["on_expiry"] = "switch_to_market_reference"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["session"]["successor_duration"] = "30m"
    config = config_module.StrategyConfig.model_validate(data)
    assert config.session.successor_duration_seconds == Decimal("1800")


def test_unverified_margin_mode_is_rejected():
    data = simulation_config_data()
    data["perpetual"] = {"enabled": True, "position_mode": "ONEWAY", "margin_mode": "isolated", "leverage": 2}
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_live_requires_economic_budgets_and_remains_feature_gated():
    data = simulation_config_data()
    data["execution_mode"] = "live"
    with pytest.raises(ValidationError, match="live configuration requires"):
        config_module.StrategyConfig.model_validate(data)


def test_live_requires_explicit_objective_instead_of_using_simulation_default():
    data = simulation_config_data()
    data["execution_mode"] = "live"
    data["economics"] = {}
    with pytest.raises(ValidationError, match="explicit economics.objective"):
        config_module.StrategyConfig.model_validate(data)


def test_schema_export_has_no_credentials_or_implicit_live_default():
    schema = config_module.StrategyConfig.model_json_schema()
    serialized = json.dumps(schema)
    assert "api_key" not in serialized.lower()
    assert "secret" not in serialized.lower()
    assert schema["properties"]["execution_mode"]["default"] == "simulation"


def test_example_yaml_round_trips_and_matches_exported_schema():
    root = Path(__file__).resolve().parents[4]
    example = root / "docs/examples/life_liquidity/simulation.yml.example"
    schema_path = root / "docs/examples/life_liquidity/config.schema.json"
    data = yaml.safe_load(example.read_text())
    config = config_module.StrategyConfig.model_validate(data)
    assert config.execution_mode == "simulation"
    assert config_module.StrategyConfig.model_validate_json(config.model_dump_json()) == config
    exported = json.loads(schema_path.read_text())
    generated = config_module.StrategyConfig.model_json_schema()
    assert exported["properties"].keys() == generated["properties"].keys()
    assert exported["properties"]["execution_mode"]["default"] == "simulation"
    for definition, model in generated["$defs"].items():
        assert exported["$defs"][definition]["properties"].keys() == model["properties"].keys()


def test_live_is_still_disabled_after_all_current_numeric_fields_are_filled():
    data = simulation_config_data()
    data["execution_mode"] = "live"
    data["risk"] = {
        "max_gross_quote": "100", "max_net_base": "10", "max_drawdown_bps": "100",
        "stress_loss_budget_quote": "10", "execution_loss_budget_quote": "10",
    }
    data["reference"].update({"max_age": "5s", "max_deviation_bps": "100"})
    data["economics"].update({
        "min_net_edge_bps": "10", "uncertainty_buffer_bps": "5",
        "fee_max_age": "1h", "holding_horizon": "4h",
        "fee_policy": "pause",
    })
    with pytest.raises(ValidationError, match="live execution remains disabled"):
        config_module.StrategyConfig.model_validate(data)


def test_fee_ceiling_policy_requires_a_ceiling_and_float_inputs_are_rejected():
    data = simulation_config_data()
    data["economics"]["fee_policy"] = "conservative_ceiling"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["economics"]["fee_ceiling_bps"] = "20"
    data["quotes"]["sizes_base"] = [0.1]
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_explicit_start_time_must_be_utc():
    data = simulation_config_data()
    data["session"]["start_policy"] = "2026-10-01T12:00:00"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["session"]["start_policy"] = "2026-10-01T12:00:00Z"
    config = config_module.StrategyConfig.model_validate(data)
    assert config.session.start_policy.endswith("Z")


def test_inventory_and_hedge_limits_preserve_decimal_price_and_quantity():
    data = simulation_config_data()
    data["inventory"] = {
        "target_base": "25.125", "deadline": "2h", "price_limit": "1.234567890123456789",
        "max_participation": "0.05",
    }
    data["hedge"] = {
        "deadband_base": "0.1", "max_unhedged_base": "2", "max_unhedged_duration": "30s",
        "max_slippage_bps": "25", "basis_limit_bps": "100", "funding_budget_quote": "10",
    }
    config = config_module.StrategyConfig.model_validate(data)
    assert config.inventory.price_limit == Decimal("1.234567890123456789")
    assert config.inventory.max_participation == Decimal("0.05")
    assert config.hedge.max_unhedged_duration_seconds == Decimal("30")


def test_inventory_and_hedge_limits_reject_invalid_bounds():
    data = simulation_config_data()
    data["inventory"] = {"target_base": "20", "max_participation": "1.5"}
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["inventory"] = {"target_base": "20", "deadline": "1h", "max_participation": "0.05"}
    data["hedge"] = {"max_unhedged_base": "2", "max_unhedged_duration": "-1s"}
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_markout_and_fill_window_settings_require_complete_valid_pairs():
    data = simulation_config_data()
    data["risk"] = {"markout_horizons": ["1s", "30s"], "markout_min_samples": 10,
                    "rolling_fill_window": "1h", "max_filled_base_per_window": "50",
                    "resume_policy": {"stable_data_duration": "5m", "probe_size_base": "0.1"}}
    config = config_module.StrategyConfig.model_validate(data)
    assert config.risk.markout_horizons == ("1s", "30s")
    data["risk"]["markout_horizons"] = ["1s", "1s"]
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["risk"]["markout_horizons"] = ["1s", "30s"]
    del data["risk"]["max_filled_base_per_window"]
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)


def test_liquidity_service_requires_explicit_bounded_budget_windows():
    data = simulation_config_data()
    data["economics"] = {"objective": "liquidity_service"}
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
    data["economics"]["subsidy_budget_quote"] = {"session": "10", "day": "20", "campaign": "100"}
    config = config_module.StrategyConfig.model_validate(data)
    assert config.economics.subsidy_budget_quote.campaign == Decimal("100")
    data["economics"]["subsidy_budget_quote"]["session"] = "30"
    with pytest.raises(ValidationError):
        config_module.StrategyConfig.model_validate(data)
