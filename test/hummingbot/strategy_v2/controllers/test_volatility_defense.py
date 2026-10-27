"""Volatility defense contracts on the existing LIFE execution path."""
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy, SpreadPolicyConfig


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


@pytest.mark.parametrize('raw', ['0', '.02', '.08', None])
def test_existing_spread_controls_preserve_every_base_level(monkeypatch, raw):
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    base = [Decimal('.004'), Decimal('.009'), Decimal('.018')]
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True, volatility=dict(
        enabled=True, min_multiplier='1', on_unavailable='static',
        fallback_buy_spreads=base, fallback_sell_spreads=base)))
    if raw is not None:
        monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal(raw))
        policy.refresh(feed(), 1200)
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    for values in policy.compose(connector, 'LIFE-USDT', Decimal('.1'), base, base, [50, 30, 20], [50, 30, 20], now=1200):
        assert all(actual >= minimum for actual, minimum in zip(values, base))
        assert values == sorted(values)


def test_legacy_low_multiplier_and_tighter_static_fallback_stay_readable():
    config = configuration(spread_policy=dict(volatility=dict(
        enabled=True, min_multiplier='.5', on_unavailable='static',
        fallback_buy_spreads=['.001'], fallback_sell_spreads=['.001'])))
    restored = type(config).model_validate_json(config.model_dump_json())
    assert restored.spread_policy.volatility.min_multiplier == Decimal('.5')
    assert restored.spread_policy.volatility.fallback_buy_spreads == [Decimal('.001')]
