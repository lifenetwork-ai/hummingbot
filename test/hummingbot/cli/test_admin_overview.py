"""Overview projection: exact scope, accounting basis and independent unknowns."""
from copy import deepcopy
from decimal import Decimal

import pytest

from hummingbot.cli.admin_overview import build_overview


def row():
    session = dict(id='session', generation=0, started_at=1000, status='running')

    def observation(value):
        return dict(value=value, observed_at=1001, stale=False, unavailable_reason=None)
    balances = [dict(currency=asset, cash_balance=cash, available_cash=free, equity=equity,
                     available_equity=free, observed_at=1001, connector='okx', source='okx_account_balance',
                     present_in_latest_snapshot=True) for asset, cash, free, equity in (
                         ('USDT', '90', '80', '100'), ('LIFE', '10', '8', '10'))]
    return dict(bot_id='bot', database_now=1002, owner_uuid='owner', heartbeat_age_s=1,
                desired_state='running', mm_session=session,
                status=dict(running=True, mm_session=session.copy(),
                            reporting=dict(active_connectors=['okx'], active_account_bindings={
                                'okx': dict(account_id='17', environment='demo')}, accounts=[dict(
                                    account_id='17', environment='demo', identity_verified=True, balances=balances)]),
                            mm_overview=dict(session_id='session', generation=0, observed_at=1001, markets=[],
                                             references=observation([]), inventory_limits=observation([]),
                                             valuation_prices=observation([dict(asset='LIFE', pair='LIFE-USDT', connector='okx',
                                                                                price_usdt='2', observed_at=1001)])),
                            mm_drawdown=dict(enabled=True, ready=True, stale=False, observed_at=1001,
                                             max_global_drawdown_quote='50', global_drawdown_quote='12',
                                             max_controller_drawdown_quote=None, controllers={})))


def test_portfolio_uses_account_equity_once_and_preserves_decimal_money():
    source = row()
    before = deepcopy(source)
    result = build_overview(source).model_dump(mode='json')
    assert result['portfolio']['value']['total_usdt'] == '120'
    assert result['portfolio']['value']['usdt'] == '100'
    assert result['portfolio']['value']['life_usdt'] == '20'
    assert result['portfolio']['value']['valuation']['valuation_mark'] == 'mid'
    assert result['account_scope'] == dict(account_ids=['17'], environment='demo', identity_verified=True)
    assert result['balances']['value'][0]['reserved_for_open_orders'] is None
    assert {item['asset']: item['free'] for item in result['balances']['value']} == {'LIFE': '8', 'USDT': '80'}
    assert source == before
    assert result['session']['capital_allocated']['value'] is None
    assert result['session']['delta']['value'] is None
    assert result['session']['benchmark_hold']['value'] is None
    assert result['risk_rules']['value'][0]['current'] == '12'


@pytest.mark.parametrize('change,reason', [
    ('owner', 'owner_observation_unavailable'), ('generation', 'session_observation_mismatch'),
    ('price', 'valuation_price_unavailable'), ('balance', 'balance_observation_unavailable'),
    ('unknown_asset', 'asset_valuation_unavailable'), ('identity', 'account_identity_unavailable'),
    ('removed_asset', 'balance_observation_unavailable'),
])
def test_incomplete_portfolio_never_becomes_a_partial_total(change, reason):
    source = row()
    if change == 'owner':
        source['heartbeat_age_s'] = 10
    elif change == 'generation':
        source['mm_session'] = {**source['mm_session'], 'generation': 1}
    elif change == 'price':
        source['status']['mm_overview']['valuation_prices']['value'] = None
    elif change == 'balance':
        source['status']['reporting']['accounts'][0]['balances'][0]['observed_at'] = 900
    elif change == 'identity':
        source['status']['reporting']['accounts'][0]['identity_verified'] = False
    elif change == 'removed_asset':
        source['status']['reporting']['accounts'][0]['balances'][1]['present_in_latest_snapshot'] = False
    else:
        source['status']['reporting']['accounts'][0]['balances'].append(dict(
            source['status']['reporting']['accounts'][0]['balances'][0], currency='BTC', equity='1'))
    result = build_overview(source)
    assert result.portfolio.value is None
    assert result.portfolio.unavailable_reason == reason


def test_old_rotated_account_is_not_added_to_current_wallet():
    source = row()
    old = deepcopy(source['status']['reporting']['accounts'][0])
    old['account_id'] = 'old'
    old['balances'][0]['equity'] = '99999'
    source['status']['reporting']['accounts'].append(old)
    assert build_overview(source).portfolio.value.total_usdt == Decimal('120')


def test_missing_clock_or_account_scope_is_explicitly_unknown():
    assert build_overview(dict(database_now=None)) is None
    source = row()
    source['status']['reporting'].pop('active_account_bindings')
    result = build_overview(source)
    assert result.account_scope.identity_verified is False
    assert result.portfolio.value is None


def test_corrupt_optional_market_does_not_discard_risk_or_balances():
    source = row()
    source['status']['mm_overview']['markets'] = [dict(market='spot', pair='LIFE-USDT', connector='okx',
                                                       depth={'value': {'bad': 'data'}}, coverage={}, slippage_estimate={}, post_fill_recovery={})]
    result = build_overview(source)
    assert result.portfolio.value.total_usdt == 120
    assert result.risk_rules.value[0].current == 12
    assert result.markets[0].depth.value is None


def test_stale_risk_keeps_limits_but_cannot_claim_ok():
    source = row()
    source['status']['mm_drawdown']['stale'] = True
    rule = build_overview(source).risk_rules.value[0]
    assert rule.upper_limit == 50
    assert rule.current is None and rule.status == 'unknown'


def test_published_iso_timestamps_are_accepted_without_losing_price_basis():
    from datetime import datetime, timezone
    source = row()
    runtime = source['status']['mm_overview']
    runtime['observed_at'] = datetime.fromtimestamp(1001, timezone.utc).isoformat()
    runtime['valuation_prices']['observed_at'] = runtime['observed_at']
    runtime['valuation_prices']['value'][0]['observed_at'] = runtime['observed_at']
    assert build_overview(source).portfolio.value.total_usdt == 120


def test_drawdown_equal_limit_matches_existing_enforcement_and_latch_remains_visible():
    source = row()
    source['status']['mm_drawdown']['global_drawdown_quote'] = '50'
    assert build_overview(source).risk_rules.value[0].status == 'ok'
    source['status']['mm_drawdown'].update(ready=False, reason_code='drawdown_global_limit', operator_action_required=True)
    assert build_overview(source).risk_rules.value[0].status == 'violated'


def test_inventory_directional_limits_do_not_invent_current_exposure():
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='perp', market='perpetual', pair='LIFE-USDT', asset='LIFE', inventory_limit=None,
        long_limit='100', short_limit='80', gross_limit='140')]
    rules = build_overview(source).risk_rules.value
    caps = [item for item in rules if item.rule_id.startswith('inventory_')]
    assert len(caps) == 3
    assert {item.upper_limit for item in caps} == {Decimal('100'), Decimal('80'), Decimal('140')}
    assert all(item.current is None and item.status == 'unknown' for item in caps)


@pytest.mark.asyncio
async def test_http_status_exposes_real_supplement_and_shares_fee_cache(monkeypatch):
    from test.hummingbot.cli.test_admin_configuration import api
    from test.hummingbot.cli.test_admin_quote_activity import quote_state
    from unittest.mock import Mock
    catalogue, source = await quote_state()
    sample = row()['status']
    source['status']['reporting'] = sample['reporting']
    runtime = sample['mm_overview']
    runtime.update(session_id=source['mm_session']['id'], generation=source['mm_session']['generation'], observed_at=1006)
    runtime['valuation_prices']['observed_at'] = 1006
    runtime['valuation_prices']['value'][0]['observed_at'] = 1006
    source['status']['mm_overview'] = runtime
    fees = Mock(return_value=dict(value=dict(total_usdt='0.125', by_market=None, conversion_basis='at_fill',
                                             history_complete=False, through_at=1007), observed_at=1007,
                                  stale=False, unavailable_reason='session_fee_history_incomplete'))
    monkeypatch.setattr('hummingbot.model.overview_fees.session_fees', fees)
    async with api(catalogue, source) as http:
        for _ in range(2):
            response = await http.get('/api/v1/admin/status')
            assert response.status == 200, await response.text()
            body = await response.json()
            assert body['overview']['portfolio']['value']['total_usdt'] == '120'
            assert body['overview']['session']['fees']['value']['total_usdt'] == '0.125'
            assert body['overview']['session']['fees']['value']['history_complete'] is False
            assert body['actions']['stop']['allowed'] is True
    assert fees.call_count == 1
    assert fees.call_args.kwargs['session_id'] == source['mm_session']['id']


@pytest.mark.asyncio
async def test_fee_database_failure_does_not_erase_portfolio_or_control_actions(monkeypatch):
    from test.hummingbot.cli.test_admin_configuration import api
    from test.hummingbot.cli.test_admin_quote_activity import quote_state
    from unittest.mock import Mock

    from sqlalchemy.exc import SQLAlchemyError
    catalogue, source = await quote_state()
    sample = row()['status']
    source['status']['reporting'] = sample['reporting']
    runtime = sample['mm_overview']
    runtime.update(session_id=source['mm_session']['id'], generation=source['mm_session']['generation'], observed_at=1006)
    runtime['valuation_prices']['observed_at'] = 1006
    runtime['valuation_prices']['value'][0]['observed_at'] = 1006
    source['status']['mm_overview'] = runtime
    monkeypatch.setattr('hummingbot.model.overview_fees.session_fees', Mock(side_effect=SQLAlchemyError('offline')))
    async with api(catalogue, source) as http:
        response = await http.get('/api/v1/admin/status')
        assert response.status == 200, await response.text()
        body = await response.json()
        assert body['overview']['portfolio']['value']['total_usdt'] == '120'
        assert body['overview']['session']['fees']['value'] is None
        assert body['overview']['session']['fees']['unavailable_reason'] == 'session_fees_unavailable'
        assert body['actions']['stop']['allowed'] is True


def test_negative_spot_outflow_never_offsets_current_open_order_reservations():
    source = row()
    source['status']['spot_outflow'] = dict(ready=True, stale=False, observed_at=1001,
                                            policy=dict(max_net_outflow_quote='50'), recorded_net_outflow_quote='-100', reserved_quote='30')
    rule = next(item for item in build_overview(source).risk_rules.value if item.rule_id == 'spot_outflow')
    assert rule.current == 30


def test_missing_inventory_policy_marks_otherwise_known_risks_incomplete():
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = None
    rules = build_overview(source).risk_rules
    assert rules.value[0].rule_id == 'drawdown_global'
    assert rules.unavailable_reason == 'risk_policy_observation_incomplete'


def test_quote_deviation_limit_comes_from_pinned_session_policy():
    source = row()
    source['mm_session']['catalogue'] = {'markets': {'spot': {'id': 'spot-controller',
                                                              'quote_guards': {'enabled': True, 'mode': 'strict', 'max_deviation_bps': '25'}}}}
    source['mm_session']['request'] = {'market_mode': 'spot'}
    source['status']['mm_markets'] = [{'market': 'spot', 'quote_guard': {
        'ready': True, 'deviation_bps': '12', 'evaluated_at': 1001}}]
    rule = next(item for item in build_overview(source).risk_rules.value if item.rule_id == 'quote_deviation')
    assert rule.upper_limit == 25 and rule.current == 12 and rule.unit == 'bps'
    assert rule.status == 'ok'


def test_partial_bootstrap_summary_keeps_independently_known_inventory_limits():
    source = row()
    source['mm_session']['catalogue'] = {'markets': {'spot': {'id': 'spot-controller',
                                                              'quote_guards': {'enabled': True, 'mode': 'bootstrap_then_market', 'max_deviation_bps': '25'}}}}
    source['mm_session']['request'] = {'market_mode': 'spot'}
    source['status']['mm_overview']['inventory_limits']['value'] = [dict(
        controller_id='spot-controller', market='spot', pair='LIFE-USDT', asset='LIFE', inventory_limit='100')]
    rules = build_overview(source).risk_rules
    assert any(item.rule_id == 'inventory_base' and item.upper_limit == 100 for item in rules.value)
    assert rules.unavailable_reason == 'risk_policy_observation_incomplete'
