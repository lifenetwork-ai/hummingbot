from decimal import Decimal

import pytest

from test.hummingbot.strategy_v2.life_liquidity.config_test_support import load_config_module


config_module = load_config_module()


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("30m", "1800"), ("4h", "14400"), ("12h", "43200"), ("1.5h", "5400"), ("2d", "172800")],
)
def test_duration_units_are_explicit_and_not_fixed_to_four_hours(value, seconds):
    assert config_module.parse_duration_seconds(value) == Decimal(seconds)


@pytest.mark.parametrize("value", ["0h", "-1h", "NaNm", "Infh", "4", "4hours", "1e3h", "", " 4h", "4h "])
def test_invalid_duration_is_rejected(value):
    with pytest.raises(ValueError):
        config_module.parse_duration_seconds(value)


def test_one_hundred_basis_points_equals_one_percent():
    assert config_module.bps_to_fraction(Decimal("100")) == Decimal("0.01")
