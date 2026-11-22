"""Strict opt-in policy, preserved disabled hashes and unsupported rollback."""
import random
from decimal import Decimal
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
    raw = (await MMProfilesConfig.load(path)).model_dump(mode='json')
    # Demo now opts into continuous close-first operation. Reconstruct the
    # explicitly disabled historical fixture, preserving its original hash.
    raw.pop('default_duration_mode', None)
    for template in raw['markets'].values():
        template.pop('inventory_reduction', None)
    if Path(path) == DEMO:
        # Reconstruct the Demo settings from the pre-reduction catalogue.
        raw['profiles']['sideways'].pop('quote_levels', None)
        raw.update(max_global_drawdown_quote='2000', max_controller_drawdown_quote='1000')
        perpetual = raw['markets']['perpetual']
        perpetual.pop('perpetual_exposure_budget', None)
        perpetual.update(max_long_base='0.05', max_short_base='0.05')
        skew = raw['markets']['spot']['inventory_skew']
        skew.pop('inventory_range_pct', None)
        skew['inventory_range_multiplier'] = '1'
    catalogue = MMProfilesConfig.model_validate(raw)
    assert catalogue.catalogue_hash() == expected
    raw = catalogue.model_dump(mode='json')
    assert all('inventory_reduction' not in template for template in raw['markets'].values())
    for template in raw['markets'].values():
        template['inventory_reduction'] = {'mode': 'disabled'}
    assert MMProfilesConfig.model_validate(raw).catalogue_hash() == expected


@pytest.mark.asyncio
async def test_current_reviewed_demo_drawdown_is_hashed_and_pinned_without_historical_normalization():
    catalogue = await MMProfilesConfig.load(DEMO)
    policy = catalogue.drawdown_policy()
    assert policy['max_controller_drawdown_quote'] == '2500'
    assert policy['max_global_drawdown_quote'] == '5000'
    old = MMProfilesConfig.model_validate({**catalogue.model_dump(mode='json'),
                                           'max_controller_drawdown_quote': '1000', 'max_global_drawdown_quote': '2000'})
    assert old.catalogue_hash() != catalogue.catalogue_hash()
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='both', profile_id='follow_market',
                                                           duration_mode='until_stopped'), rng=random.Random(1))
    restored = MMSession.model_validate(session.model_dump(mode='json'))
    assert restored.catalogue.catalogue_hash() == catalogue.catalogue_hash()
    assert restored.catalogue.drawdown_policy() == policy


@pytest.mark.asyncio
async def test_enabled_policy_is_pinned_in_the_session_and_old_strict_market_shape_rejects_it():
    raw = (await MMProfilesConfig.load(DEMO)).model_dump(mode='json')
    assert raw['markets']['perpetual']['inventory_reduction'] == POLICY
    old_market = create_model('PreReductionMarket', __config__=ConfigDict(extra='forbid'),
                              **{name: (object, None) for name in LifeMMConfig.model_fields if name != 'inventory_reduction'})
    old_market.model_validate({key: value for key, value in raw['markets']['perpetual'].items()
                              if key != 'inventory_reduction'})
    catalogue = MMProfilesConfig.model_validate(raw)
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='both', profile_id='follow_market',
                                                           duration_mode='until_stopped'), rng=random.Random(1))
    saved = session.model_dump(mode='json')
    restored = MMSession.model_validate(saved)
    perpetual = restored.catalogue.markets['perpetual']
    assert perpetual.inventory_reduction.price_mode == 'breakeven'
    for template in (raw['markets']['perpetual'], saved['catalogue']['markets']['perpetual']):
        with pytest.raises(ValidationError) as error:
            old_market.model_validate(template)
        assert any(item['loc'] == ('inventory_reduction',) and item['type'] == 'extra_forbidden'
                   for item in error.value.errors())


@pytest.mark.asyncio
async def test_perpetual_exposure_budget_is_optional_and_stays_pinned_after_reload():
    catalogue = await MMProfilesConfig.load(DEMO)
    raw = catalogue.model_dump(mode='json')
    perpetual = raw['markets']['perpetual']
    budget = perpetual['perpetual_exposure_budget']
    fixed = {**perpetual, 'max_long_base': '0.1', 'max_short_base': '0.05'}
    fixed.pop('perpetual_exposure_budget')
    fixed_catalogue = MMProfilesConfig.model_validate({**raw, 'markets': {**raw['markets'], 'perpetual': fixed}})
    assert fixed_catalogue.catalogue_hash() != catalogue.catalogue_hash()
    request = MMSessionRequest(market_mode='both', profile_id='follow_market', duration_mode='until_stopped')
    session = MMSession.create(catalogue, request, rng=random.Random(1))
    restored = MMSession.model_validate(session.model_dump(mode='json'))
    pinned = restored.catalogue.markets['perpetual']
    assert pinned.perpetual_exposure_budget.model_dump(mode='json') == dict(
        allocated_capital_quote='10000', max_long_pct='0.50', max_short_pct='0.25', max_gross_pct='0.75', max_mark_age_seconds=15)
    assert pinned.max_gross_base == Decimal('0.1')
    assert pinned.max_long_base is None and pinned.max_short_base is None
    with pytest.raises(ValidationError, match='max_gross_base'):
        MMProfilesConfig.model_validate({**raw, 'markets': {**raw['markets'], 'perpetual': {**perpetual, 'max_gross_base': None}}})
    with pytest.raises(ValidationError, match='long, short and gross exposure caps'):
        MMProfilesConfig.model_validate({**raw, 'markets': {**raw['markets'], 'perpetual': {**perpetual, 'perpetual_exposure_budget': None}}})
    budget['max_gross_pct'] = '1.01'
    with pytest.raises(ValidationError):
        MMProfilesConfig.model_validate(raw)
    with pytest.raises(ValidationError, match='requires close-first'):
        configuration(connector_name='okx_perpetual', perpetual_exposure_budget={**budget, 'max_gross_pct': '.4'})


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
