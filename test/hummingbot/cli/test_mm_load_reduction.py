"""Opt-in load evidence must exercise actual closes and preserve default fixtures."""
import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from hummingbot.client.config.mm_profiles import MMProfilesConfig


@pytest.mark.asyncio
async def test_explicit_reduction_load_fixture_preserves_spot_and_requires_guarded_perpetual_policy():
    from scripts.mm_load_reduction import enable_reduction

    profiles = yaml.safe_load(Path('deploy/cloudrun/conf/life_mm_profiles.yaml').read_text())
    before = copy.deepcopy(profiles)
    enable_reduction(profiles)
    assert profiles['markets']['spot'] == before['markets']['spot']
    catalogue = MMProfilesConfig.model_validate(profiles)
    for name in catalogue.profiles:
        configs = catalogue.controller_configs(name, 'both', None)
        perpetual = next(c for c in configs if c.connector_name == 'okx_perpetual')
        assert perpetual.inventory_reduction.mode == 'close_first'
        assert perpetual.inventory_reduction.price_mode == 'breakeven'
        assert perpetual.quote_guards.enabled and perpetual.spread_policy.enabled
    assert MMProfilesConfig.model_validate(before).markets['perpetual'].inventory_reduction.mode == 'disabled'


def test_reduction_load_requires_verified_fills_for_both_hedge_close_directions():
    from scripts.mm_load_reduction import closing_activity

    orders = [dict(ordId='long-close', instId='LIFE-USDT-SWAP', posSide='long', side='sell'),
              dict(ordId='short-close', instId='LIFE-USDT-SWAP', posSide='short', side='buy'),
              dict(ordId='open', instId='LIFE-USDT-SWAP', posSide='long', side='buy')]
    fills = [dict(ordId='open', tradeId='1', fillSz='10')]
    with pytest.raises(AssertionError, match='closing fill'):
        closing_activity(dict(orders=orders, fills=fills))
    fills += [dict(ordId='long-close', tradeId='2', fillSz='2'), dict(ordId='short-close', tradeId='3', fillSz='3')]
    result = closing_activity(dict(orders=orders, fills=fills))
    assert result['closing_orders'] == 2 and result['closing_fills'] == 2
    assert Decimal(result['closed_base']) == Decimal('.05')
    with pytest.raises(AssertionError, match='duplicate'):
        closing_activity(dict(orders=orders, fills=fills + [fills[-1]]))
