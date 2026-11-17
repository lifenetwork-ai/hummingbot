"""Contract-only gates: no reporting collectors or trading behavior are changed."""


def test_overview_is_nullable_additive_status_contract():
    from hummingbot.cli.swagger import build_spec

    schemas = build_spec()['components']['schemas']
    status = schemas['StatusResponseOutput']
    assert 'overview' not in status.get('required', [])
    field = status['properties']['overview']
    assert {'type': 'null'} in field['anyOf']
    assert {'$ref': '#/components/schemas/OverviewSupplementOutput'} in field['anyOf']
    assert field['default'] is None


def test_overview_covers_all_requested_groups_and_serializes_money_as_strings():
    from hummingbot.cli.swagger import build_spec

    schemas = build_spec()['components']['schemas']
    assert set(schemas['OverviewSupplementOutput']['properties']) >= {
        'account_scope', 'session_id', 'session_started_at', 'session', 'portfolio',
        'risk_rules', 'markets', 'balances', 'inventory_limits', 'references',
    }
    assert set(schemas['OverviewSessionOutput']['properties']) == {
        'capital_allocated', 'fees', 'delta', 'benchmark_hold',
    }
    assert set(schemas['OverviewMarketDataOutput']['properties']) >= {
        'depth', 'coverage', 'slippage_estimate', 'post_fill_recovery',
    }
    assert schemas['OverviewPortfolioOutput']['properties']['total_usdt']['type'] == 'string'
    assert schemas['OverviewCapitalAllocatedOutput']['properties']['life']['type'] == 'string'


def test_standalone_contract_serialization_and_unavailable_values():
    from decimal import Decimal

    from hummingbot.cli.admin_overview_models import OverviewCapitalAllocated, OverviewObservation

    value = OverviewObservation[OverviewCapitalAllocated](
        value={'usdt': Decimal('120000.00'), 'life': '2400000', 'source': 'session_configuration'},
        observed_at='2026-11-13T10:00:00+07:00', stale=False, unavailable_reason=None,
    ).model_dump(mode='json')
    assert value['value']['usdt'] == '120000.00'
    assert value['observed_at'] == '2026-11-13T03:00:00Z'
    missing = OverviewObservation[OverviewCapitalAllocated](
        value=None, observed_at=None, stale=True, unavailable_reason='not_implemented',
    ).model_dump(mode='json')
    assert missing['value'] is None
    assert missing['stale'] is True


def test_standalone_contract_rejects_invalid_amounts_fields_and_naive_time():
    import pytest
    from pydantic import ValidationError

    from hummingbot.cli.admin_overview_models import OverviewCapitalAllocated, OverviewObservation

    for amount in ['NaN', 'Infinity', '1,000', '12 USDT']:
        with pytest.raises(ValidationError):
            OverviewCapitalAllocated(usdt=amount, life='0', source='session_configuration')
    with pytest.raises(ValidationError):
        OverviewCapitalAllocated(usdt='0', life='0', source='session_configuration', invented=True)
    with pytest.raises(ValidationError):
        OverviewObservation[OverviewCapitalAllocated](
            value=None, observed_at='2026-11-13T10:00:00', stale=True, unavailable_reason='unknown',
        )


def test_standalone_contract_serialization_schema_covers_all_groups():
    from hummingbot.cli.admin_overview_models import OverviewSupplement

    schema = OverviewSupplement.model_json_schema(mode='serialization')
    assert set(schema['properties']) >= {
        'session', 'portfolio', 'risk_rules', 'markets', 'balances', 'inventory_limits', 'references',
    }
    assert schema['$defs']['OverviewPortfolio']['properties']['total_usdt']['type'] == 'string'
    fraction = schema['$defs']['OverviewRollingCoverage']['properties']['rolling_30m_fraction']['anyOf'][0]
    assert fraction['minimum'] == 0
    assert fraction['maximum'] == 1
