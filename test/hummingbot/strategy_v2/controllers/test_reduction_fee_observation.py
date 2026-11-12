"""Account-specific fees are authenticated cached reads, never a static default."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.okx_reduction_fee import FeeObservation, read_reduction_fee


def response(**updates):
    row = dict(instType='SWAP', ts='1000000', feeGroup=[dict(groupId='2', maker='-.0002', taker='-.0005')])
    row.update(updates)
    return {'code': '0', 'data': [row]}


def test_fee_group_and_explicit_legacy_usdt_rate_decode_without_rebate_credit():
    observed = FeeObservation.decode(response(), wall_now=1000, monotonic_now=50)
    assert observed.current(50).rate == Decimal('.0002')
    assert observed.current(79).flat_quote == 0
    assert FeeObservation.decode(response(feeGroup=[dict(groupId='2', maker='.0002')]),
                                 wall_now=1000, monotonic_now=50).current(50).rate == 0
    legacy = response(makerU='-.0003')
    del legacy['data'][0]['feeGroup']
    assert FeeObservation.decode(legacy, wall_now=1000, monotonic_now=50).current(50).rate == Decimal('.0003')


@pytest.mark.parametrize('mutation', [
    {'code': '50013'}, {'data': []}, {'data': [response()['data'][0]] * 2},
    {'row': {'instType': 'SPOT'}}, {'row': {'ts': None}}, {'row': {'ts': '900000'}},
    {'row': {'ts': '1002000'}}, {'row': {'feeGroup': []}}, {'row': {'feeGroup': {}}},
    {'row': {'feeGroup': [dict(groupId='2', maker='NaN')]}},
    {'row': {'feeGroup': [dict(groupId='2', maker='Infinity')]}},
    {'row': {'feeGroup': [dict(groupId='2', maker='-1')]}},
    {'row': {'feeGroup': [dict(groupId='2', maker=None)]}},
    {'row': {'feeGroup': [dict(maker='-.0002')]}},
    {'row': {'feeGroup': [dict(groupId='2', maker='-.0002'), dict(groupId='3', maker='-.0001')]}},
])
def test_unknown_ambiguous_failed_or_stale_fee_response_is_not_zero(mutation):
    value = deepcopy(response())
    if 'row' in mutation:
        value['data'][0].update(mutation['row'])
    else:
        value.update(mutation)
    with pytest.raises(ValueError, match='reduction_fee_unavailable'):
        FeeObservation.decode(value, wall_now=1000, monotonic_now=50)


@pytest.mark.parametrize('now', [49, 80.001, float('inf'), float('nan'), True])
def test_expired_or_invalid_local_clock_cannot_extend_fee_authority(now):
    observed = FeeObservation.decode(response(), wall_now=1000, monotonic_now=50)
    with pytest.raises(ValueError, match='reduction_fee_unavailable'):
        observed.current(now)


@pytest.mark.asyncio
async def test_fee_read_uses_native_auth_and_exact_instrument_family():
    connector = SimpleNamespace(name='okx_perpetual', exchange_symbol_associated_to_pair=AsyncMock(return_value='BTC-USDT-SWAP'),
                                _api_get=AsyncMock(return_value=response()), trading_rules={'BTC-USDT': SimpleNamespace(
                                    buy_order_collateral_token='USDT', sell_order_collateral_token='USDT')})
    observed = await read_reduction_fee(connector, 'BTC-USDT', wall_now=1000, monotonic_now=50)
    assert observed.current(50).rate == Decimal('.0002')
    connector._api_get.assert_awaited_once_with(path_url='/api/v5/account/trade-fee',
                                                params={'instType': 'SWAP', 'instFamily': 'BTC-USDT'},
                                                is_auth_required=True, trading_pair='BTC-USDT')
    connector.trading_rules['BTC-USDT'].buy_order_collateral_token = 'BTC'
    with pytest.raises(ValueError, match='reduction_fee_unavailable'):
        await read_reduction_fee(connector, 'BTC-USDT', wall_now=1000, monotonic_now=50)
    assert connector._api_get.await_count == 1
