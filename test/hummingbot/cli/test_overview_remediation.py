"""Wire-level regressions from the PR #2 contract review."""
from decimal import Decimal
from test.hummingbot.cli.test_admin_overview import row
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hummingbot.cli.admin_overview import build_overview
from hummingbot.cli.admin_overview_models import OverviewInventoryLimit, OverviewSessionFees


@pytest.mark.parametrize('market', ['spot', 'perpetual'])
@pytest.mark.parametrize('asset', ['BTC', 'LIFE', 'ETH'])
def test_inventory_and_risk_units_follow_the_actual_pair(market, asset):
    source = row()
    item = dict(controller_id='controller', market=market, pair=f'{asset}-USDT', asset=asset,
                inventory_limit='0.05' if market == 'spot' else None,
                long_limit='0.02' if market == 'perpetual' else None,
                short_limit='0.03' if market == 'perpetual' else None,
                gross_limit='0.05' if market == 'perpetual' else None)
    source['status']['mm_overview']['inventory_limits']['value'] = [item]
    result = build_overview(source)
    assert result.inventory_limits.value is not None
    assert result.inventory_limits.value[0].asset == asset
    risks = [rule for rule in result.risk_rules.value if rule.rule_id.startswith('inventory_')]
    assert len(risks) == (1 if market == 'spot' else 3)
    assert all(rule.unit == asset for rule in risks)
    assert {rule.upper_limit for rule in risks} == (
        {Decimal('0.05')} if market == 'spot' else {Decimal('0.02'), Decimal('0.03'), Decimal('0.05')})


def test_mismatched_inventory_asset_is_rejected_in_both_projections():
    item = dict(controller_id='controller', market='spot', pair='BTC-USDT', asset='LIFE', inventory_limit='1')
    with pytest.raises(ValidationError):
        OverviewInventoryLimit.model_validate(item)
    source = row()
    source['status']['mm_overview']['inventory_limits']['value'] = [item]
    result = build_overview(source)
    assert result.inventory_limits.value is None
    assert not any(rule.rule_id.startswith('inventory_') for rule in result.risk_rules.value)
    assert result.risk_rules.unavailable_reason == 'risk_policy_observation_incomplete'


@pytest.mark.parametrize('amount', ['0.00000001', '-0.00000001', '0E-8', '1E+20', '120.123456789012345678'])
def test_fees_use_plain_exact_decimal_strings(amount):
    value = OverviewSessionFees(total_usdt=amount, by_market=None, conversion_basis='at_fill',
                                history_complete=False, through_at=1001).model_dump(mode='json')
    assert value['total_usdt'] == format(Decimal(amount), 'f')


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['0.00000001', '-0.00000001', '0E-8', '1E+20', '120.123456789012345678'])
async def test_real_status_response_keeps_small_fee_usable_by_fe(monkeypatch, amount):
    from test.hummingbot.cli.test_admin_configuration import api
    from test.hummingbot.cli.test_admin_quote_activity import quote_state

    catalogue, source = await quote_state()
    sample = row()['status']
    source['status']['reporting'] = sample['reporting']
    runtime = sample['mm_overview']
    runtime.update(session_id=source['mm_session']['id'], generation=source['mm_session']['generation'], observed_at=1006)
    source['status']['mm_overview'] = runtime
    monkeypatch.setattr('hummingbot.model.overview_fees.session_fees', Mock(return_value=dict(
        value=dict(total_usdt=amount, by_market=None, conversion_basis='at_fill',
                   history_complete=False, through_at=1007), observed_at=1007, stale=False,
        unavailable_reason='session_fee_history_incomplete')))
    async with api(catalogue, source) as http:
        response = await http.get('/api/v1/admin/status')
        assert response.status == 200
        body = await response.json()
        assert body['overview']['session']['fees']['value']['total_usdt'] == format(Decimal(amount), 'f')
        assert body['actions']['stop']['allowed'] is True
