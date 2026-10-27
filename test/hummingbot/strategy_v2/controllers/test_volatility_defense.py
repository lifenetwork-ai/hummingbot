"""Volatility defense contracts on the existing LIFE execution path."""
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace

import pytest
import yaml

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.data_type.common import TradeType


@pytest.fixture
def clock():
    """Wall time and monotonic recovery time advance independently in tests."""
    return SimpleNamespace(epoch=1200, monotonic=100)


@pytest.mark.parametrize('connector', ['okx', 'okx_perpetual'])
def test_three_weighted_levels_use_one_market_budget(connector):
    controller, _ = make_controller(configuration(
        connector_name=connector, total_amount_quote='200',
        buy_spreads=[.004, .009, .018], sell_spreads=[.004, .009, .018],
        buy_amounts_pct=[50, 30, 20], sell_amounts_pct=[50, 30, 20]))
    controller.processed_data = {'reference_price': Decimal('.1'), 'spread_multiplier': Decimal('1')}
    notionals = []
    for side, prices in [('buy', ['.0996', '.0991', '.0982']), ('sell', ['.1004', '.1009', '.1018'])]:
        for level, (expected_price, allocation) in enumerate(zip(prices, [50, 30, 20])):
            price, amount = controller.get_price_and_amount(f'{side}_{level}')
            assert price == Decimal(expected_price)
            assert price * amount == pytest.approx(Decimal(allocation))
            notionals.append(price * amount)
    assert sum(notionals) == pytest.approx(Decimal('200'))


@pytest.mark.parametrize('mode,count', [('spot', 1), ('perpetual', 1), ('both', 2)])
def test_current_catalogue_keeps_two_equal_levels_and_market_selection(mode, count):
    catalogue = MMProfilesConfig.model_validate(yaml.safe_load(Path('deploy/cloudrun/conf/life_mm_profiles.yaml').read_text()))
    configs = catalogue.controller_configs('sideways', mode, None)
    assert len(configs) == count
    for config in configs:
        for side in (TradeType.BUY, TradeType.SELL):
            spreads, amounts = config.get_spreads_and_amounts_in_quote(side)
            assert spreads == [.01, .02]
            assert amounts == [Decimal('25'), Decimal('25')]
        assert not config.spread_policy.enabled
