"""Regression proofs for missing Overview sources and misleading comparisons."""
from copy import deepcopy
from decimal import Decimal
from test.hummingbot.cli.test_admin_overview import row
from test.hummingbot.cli.test_overview_capture import host
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from hummingbot.cli.admin_overview import build_overview
from hummingbot.cli.overview_capture import capture_overview
from hummingbot.core.overview_market_observations import capture_overview_markets


def test_cap_current_uses_fresh_engine_assessment_including_open_reservations():
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='spot', market='spot', pair='BTC-USDT', asset='BTC', inventory_limit='2',
        long_exposure='.5778', short_exposure='0', exposure_observed_at=1001,
        exposure_basis='engine_cap_assessment')]
    rule = next(r for r in build_overview(source).risk_rules.value if r.rule_id == 'inventory_base')
    assert rule.current == Decimal('.5778') and rule.status == 'ok'


@pytest.mark.parametrize('long,short,status', [('.0498', '0', 'ok'), ('.051', '0', 'violated')])
def test_perpetual_leg_and_gross_cap_currents_are_explicit(long, short, status):
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='perp', market='perpetual', pair='BTC-USDT', asset='BTC', inventory_limit=None,
        long_limit='.05', short_limit='.05', gross_limit='.1', long_exposure=long, short_exposure=short,
        exposure_observed_at=1001, exposure_basis='engine_cap_assessment')]
    rules = {r.rule_id: r for r in build_overview(source).risk_rules.value if r.rule_id.startswith('inventory_')}
    assert rules['inventory_long'].current == Decimal(long) and rules['inventory_long'].status == status
    assert rules['inventory_short'].current == 0
    assert rules['inventory_gross'].current == Decimal(long) + Decimal(short)


def test_stale_exposure_keeps_configured_limit_and_unknown_current():
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='spot', market='spot', pair='BTC-USDT', asset='BTC', inventory_limit='2',
        long_exposure='.5', short_exposure='0', exposure_observed_at=990,
        exposure_basis='engine_cap_assessment')]
    rule = next(r for r in build_overview(source).risk_rules.value if r.rule_id == 'inventory_base')
    assert rule.upper_limit == 2 and rule.current is None and rule.status == 'unknown'
    assert rule.unavailable_reason == 'exposure_observation_unavailable'


def test_collector_reads_cached_exposure_without_invoking_mutating_engine_sources():
    runtime, provider, controllers = host()
    item = controllers[0]
    identity = (item.config.id, 'okx', 'LIFE-USDT', 'session-a', 4)
    item._risk_exposure_observation = (100, identity, Decimal('500'), Decimal('0'))
    item._exposure = Mock(side_effect=AssertionError('no guard evaluation from collector'))
    result = capture_overview_markets(runtime, now=100)
    cap = result['inventory_limits']['value'][0]
    assert cap['long_exposure'] == '500' and cap['short_exposure'] == '0'
    assert cap['exposure_basis'] == 'engine_cap_assessment'
    item._exposure.assert_not_called()
    item._risk_exposure_observation = (100, (*identity[:-1], 3), Decimal('500'), Decimal('0'))
    assert capture_overview_markets(runtime, now=100)['inventory_limits']['value'][0]['long_exposure'] is None
    assert provider.created == 0


def test_coverage_publishes_approved_slo_and_retains_partial_window():
    runtime, _, _ = host()
    value = capture_overview(runtime, now=100, monotonic_now=100)['markets'][0]['coverage']['value']
    assert value['target_fraction'] == .99
    assert value['full_window'] is False


def test_spread_plan_is_two_sided_width_not_one_sided_offset():
    runtime, _, controllers = host()
    controllers[0].processed_data = dict(spread_policy=dict(
        enabled=True, state='static', stale=False, buy_spreads=['.0008'], sell_spreads=['.001']))
    plan = capture_overview_markets(runtime, now=100)['markets'][0]['spread_target']['value']
    expected = Decimal('.0018') / Decimal('1.0001') * 10000
    assert Decimal(plan['target_bps']) == expected
    assert plan['basis'] == 'two_sided_primary_plan' and plan['source'] == 'controller_spread_policy'


def test_quote_budget_comes_from_pinned_selected_markets_and_is_not_capital():
    source = row()
    source['mm_session'].update(catalogue=dict(markets={
        'spot': dict(id='spot', total_amount_quote='100'),
        'perpetual': dict(id='perp', total_amount_quote='100')}), request=dict(market_mode='both'))
    result = build_overview(source)
    assert result.session.quote_budget.value.total_usdt == 200
    assert result.session.quote_budget.value.source == 'pinned_session_catalogue'
    assert result.session.capital_allocated.value is None


def test_generic_spot_valuation_uses_asset_prices_without_aliasing_btc_to_life():
    source = row()
    source['status']['reporting']['accounts'][0]['balances'][1].update(currency='BTC', equity='.5')
    source['status']['mm_overview']['valuation_prices']['value'] = [dict(
        asset='BTC', pair='BTC-USDT', connector='okx', price_usdt='60000', observed_at=1001)]
    portfolio = build_overview(source).portfolio.value
    assert portfolio.total_usdt == 30100 and portfolio.life_usdt == 0
    assert portfolio.other_assets_usdt == 30000
    assert portfolio.assets[0].asset == 'BTC'


def test_whole_account_usd_equity_is_not_summed_twice_or_relabelled_usdt():
    source = row()
    source['status']['reporting']['account_equities'] = [dict(
        account_id='17', environment='demo', site='www', connector=name, total_equity_usd='75000.1234',
        observed_at=1001, source='okx_account_balance_total_eq') for name in ('okx', 'okx_perpetual')]
    before = deepcopy(source)
    value = build_overview(source).account_equity.value
    assert value.total_usd == Decimal('75000.1234')
    assert value.currency == 'USD' and value.source == 'okx_account_balance_total_eq'
    assert source == before


@pytest.mark.parametrize('account,stamp', [('other', 1001), ('17', 900)])
def test_usd_equity_never_crosses_account_identity_or_stale_observations(account, stamp):
    source = row()
    source['status']['reporting']['account_equities'] = [dict(
        account_id=account, environment='demo', site='www', connector='okx', total_equity_usd='1',
        observed_at=stamp, source='okx_account_balance_total_eq')]
    assert build_overview(source).account_equity.value is None


def test_normal_balance_metadata_retains_exact_usd_total_without_engine_mutation():
    from hummingbot.connector.okx_account_observations import record_account_equity
    connector = SimpleNamespace(account_uid='17', trading_environment='demo', okx_environment=SimpleNamespace(site='www'))
    with patch('hummingbot.connector.okx_account_observations.time.time', return_value=1001):
        record_account_equity(connector, dict(totalEq='9007199254740993.125'))
    assert connector.account_equity_observation['total_equity_usd'] == Decimal('9007199254740993.125')
    record_account_equity(connector, dict(totalEq='NaN'))
    assert connector.account_equity_observation is None


@pytest.mark.parametrize('long,short', [(None, '0'), ('.5', None)])
def test_missing_exposure_leg_keeps_the_limit_visible(long, short):
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='spot', market='spot', pair='BTC-USDT', asset='BTC', inventory_limit='2',
        long_exposure=long, short_exposure=short, exposure_observed_at=1001,
        exposure_basis='engine_cap_assessment')]
    rule = next(r for r in build_overview(source).risk_rules.value if r.rule_id == 'inventory_base')
    assert rule.upper_limit == 2 and rule.current is None and rule.status == 'unknown'


def test_account_equity_unknown_on_conflicting_marks_or_inactive_connector():
    source = row()
    source['status']['reporting']['active_connectors'] = ['okx', 'okx_perpetual']
    marks = [dict(account_id='17', environment='demo', site='www', connector=name,
                  total_equity_usd=value, observed_at=1001, source='okx_account_balance_total_eq')
             for name, value in [('okx', '10'), ('okx_perpetual', '11')]]
    source['status']['reporting']['account_equities'] = marks
    assert build_overview(source).account_equity.unavailable_reason == 'account_equity_source_conflict'
    source['status']['reporting']['active_connectors'] = []
    assert build_overview(source).account_equity.value is None


@pytest.mark.parametrize('state,stale', [('waiting', False), ('dynamic', True)])
def test_unready_spread_policy_never_falls_back_to_a_fake_plan(state, stale):
    runtime, _, controllers = host()
    controllers[0].processed_data = dict(spread_policy=dict(
        enabled=True, state=state, stale=stale, buy_spreads=['.0008'], sell_spreads=['.0008']))
    assert capture_overview_markets(runtime, now=100)['markets'][0]['spread_target']['value'] is None


def test_enabled_spread_policy_without_runtime_evidence_stays_unknown():
    runtime, _, controllers = host()
    controllers[0].processed_data = {}
    controllers[0].config.spread_policy = SimpleNamespace(enabled=True)
    controllers[0].config.buy_spreads = [.0008]
    controllers[0].config.sell_spreads = [.0008]
    assert capture_overview_markets(runtime, now=100)['markets'][0]['spread_target']['value'] is None


@pytest.mark.parametrize('identity,generation', [('other', 0), ('session', 3)])
def test_projected_budget_never_crosses_session_identity(identity, generation):
    source = row()
    source['mm_session']['request'] = {'market_mode': 'spot'}
    source['overview_session_policy'] = dict(session_id=identity, generation=generation, markets={
        'spot': dict(id='spot', total_amount_quote='200')})
    assert build_overview(source).session.quote_budget.value is None


def test_projected_budget_is_independent_of_runtime_health_and_never_mutates_session():
    source = row()
    source['heartbeat_age_s'] = 99
    source['mm_session']['request'] = {'market_mode': 'spot'}
    source['overview_session_policy'] = dict(session_id='session', generation=0, markets={
        'spot': dict(id='spot', total_amount_quote='100')})
    before = deepcopy(source)
    result = build_overview(source)
    assert result.session.quote_budget.value.total_usdt == 100
    assert result.inventory_limits.value is None
    assert source == before and 'catalogue' not in source['mm_session']
