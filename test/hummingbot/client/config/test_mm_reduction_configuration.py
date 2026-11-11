"""Strict opt-in policy, preserved disabled hashes and unsupported rollback."""
import random
from pathlib import Path
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller

import pytest
from pydantic import ConfigDict, ValidationError, create_model

from controllers.market_making.life_mm import LifeMMConfig
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSession, MMSessionRequest

DEMO = Path('deploy/cloudrun/demo/life_mm_profiles.yaml')
POLICY = dict(mode='close_first', price_mode='breakeven')
GUARDS = dict(enabled=True, max_deviation_bps='200', resume_deviation_bps='100')


@pytest.mark.asyncio
@pytest.mark.parametrize('path,expected', [
    ('deploy/cloudrun/conf/life_mm_profiles.yaml', 'ac7dba2cd8b2b3585a225dcce5cf0ed2ea5d397da97d6e61be560b4ea424c0ce'),  # noqa: documentation
    (str(DEMO), 'a37de043654129137af216a52e8fa2af99389d9a48a6a4e28e4c3c5444f3416b'),  # noqa: documentation
])
async def test_disabled_policy_preserves_the_pre_reduction_catalogue_hash(path, expected):
    catalogue = await MMProfilesConfig.load(path)
    assert catalogue.catalogue_hash() == expected
    raw = catalogue.model_dump(mode='json')
    assert all('inventory_reduction' not in template for template in raw['markets'].values())
    for template in raw['markets'].values():
        template['inventory_reduction'] = {'mode': 'disabled'}
    assert MMProfilesConfig.model_validate(raw).catalogue_hash() == expected


@pytest.mark.asyncio
async def test_enabled_policy_is_pinned_in_the_session_and_old_strict_market_shape_rejects_it():
    raw = (await MMProfilesConfig.load(DEMO)).model_dump(mode='json')
    old_market = create_model('PreReductionMarket', __config__=ConfigDict(extra='forbid'),
                              **{name: (object, None) for name in LifeMMConfig.model_fields if name != 'inventory_reduction'})
    old_market.model_validate(raw['markets']['perpetual'])
    raw['markets']['perpetual']['inventory_reduction'] = POLICY
    catalogue = MMProfilesConfig.model_validate(raw)
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='both', profile_id='follow_market',
                                                           duration_mode='until_stopped'), rng=random.Random(1))
    saved = session.model_dump(mode='json')
    restored = MMSession.model_validate(saved)
    assert restored.catalogue.markets['perpetual'].inventory_reduction.price_mode == 'breakeven'
    for template in (raw['markets']['perpetual'], saved['catalogue']['markets']['perpetual']):
        with pytest.raises(ValidationError) as error:
            old_market.model_validate(template)
        assert any(item['loc'] == ('inventory_reduction',) and item['type'] == 'extra_forbidden'
                   for item in error.value.errors())


@pytest.mark.parametrize('policy', [
    {'mode': 'close_first'}, {'mode': 'close_first', 'price_mode': 'market'},
    {**POLICY, 'take_profit': '.1'}, {'mode': 'disabled', 'price_mode': 'breakeven'}, {'mode': 'unknown'},
])
def test_policy_does_not_ignore_unknown_fields_or_enable_loss_taking(policy):
    with pytest.raises(ValueError):
        configuration(connector_name='okx_perpetual', inventory_reduction=policy,
                      quote_guards=GUARDS, spread_policy={'fee_floor': True})


def test_reduction_requires_perpetual_guards_native_positions_and_managed_runtime():
    for changes in ({}, {'connector_name': 'okx_perpetual', 'quote_guards': {'enabled': False}},
                    {'connector_name': 'okx_perpetual', 'spread_policy': {'fee_floor': False}},
                    {'connector_name': 'okx_perpetual', 'initial_positions': [
                        dict(connector_name='okx_perpetual', trading_pair='LIFE-USDT', side='BUY', amount='1')]}):
        values = dict(inventory_reduction=POLICY, quote_guards=GUARDS, spread_policy={'fee_floor': True})
        values.update(changes)
        with pytest.raises(ValueError):
            configuration(**values)
    config = configuration(connector_name='okx_perpetual', inventory_reduction=POLICY,
                           quote_guards=GUARDS, spread_policy={'fee_floor': True})
    with pytest.raises(ValueError, match='managed'):
        make_controller(config)
