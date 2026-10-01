from decimal import Decimal

import pytest
from pydantic import ValidationError

from test.hummingbot.strategy_v2.life_liquidity.config_test_support import (
    load_config_module,
    simulation_config_data,
)


config_module = load_config_module()


def test_lookback_and_session_duration_change_independently():
    original = config_module.StrategyConfig.model_validate(simulation_config_data())
    changed_lookback = config_module.apply_update(original, {"reference": {"lookback": "30m"}})
    assert changed_lookback.reference.lookback_seconds == Decimal("1800")
    assert changed_lookback.session.duration_seconds == Decimal("14400")
    changed_duration = config_module.apply_update(changed_lookback, {"session": {"duration": "12h"}})
    assert changed_duration.session.duration_seconds == Decimal("43200")
    assert changed_duration.reference.lookback_seconds == Decimal("1800")
    assert changed_duration.config_version == original.config_version + 2


def test_invalid_update_is_atomic_and_cannot_enable_live():
    original = config_module.StrategyConfig.model_validate(simulation_config_data())
    original_dump = original.model_dump(mode="json")
    with pytest.raises(ValidationError):
        config_module.apply_update(original, {"quotes": {"spreads_bps": ["10", "20"]}})
    assert original.model_dump(mode="json") == original_dump
    with pytest.raises(ValueError):
        config_module.apply_update(original, {"execution_mode": "live"})
    assert original.model_dump(mode="json") == original_dump


def test_valid_multi_field_update_commits_one_version():
    original = config_module.StrategyConfig.model_validate(simulation_config_data())
    changed = config_module.apply_update(
        original, {"session": {"duration": "2h"}, "reference": {"lookback": "30m"}},
    )
    assert changed.config_version == original.config_version + 1
    assert changed.session.duration_seconds == Decimal("7200")
    assert changed.reference.lookback_seconds == Decimal("1800")
    assert original.session.duration_seconds == Decimal("14400")


def test_nested_quote_levels_cannot_mutate_without_a_new_validated_version():
    original = config_module.StrategyConfig.model_validate(simulation_config_data())
    with pytest.raises(TypeError):
        original.quotes.sizes_base[0] = Decimal("999")
    with pytest.raises(ValidationError):
        original.session.duration = "12h"
    assert original.quotes.sizes_base == (Decimal("10"),)
    assert original.config_version == 1
