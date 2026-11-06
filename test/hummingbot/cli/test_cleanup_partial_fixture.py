"""Prove the partial-close stimulus before using it as native-image evidence."""
import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['live', 'canceled'])
async def test_first_close_partially_fills_once_and_later_close_uses_only_verified_remainder(outcome):
    venue = exchange()
    venue.positions[('LIFE-USDT-SWAP', 'long')] = dict(instId='LIFE-USDT-SWAP', posSide='long',
                                                       pos='10', avgPx='.1', mgnMode='cross')
    assert (await venue.control({'cleanup_market_closes': True, 'cleanup_partial': outcome})).status == 200
    terms = dict(instId='LIFE-USDT-SWAP', side='sell', posSide='long', tdMode='cross', ordType='market')
    result = json.loads((await venue.handle(signed('/api/v5/trade/order', method='POST',
                                                   data={**terms, 'clOrdId': 'partial', 'sz': '10'}))).text)
    assert result['data'][0]['sCode'] == '0'
    assert venue.fills[0]['fillSz'] == '4.0' and len(venue.fills) == 1
    assert venue.positions['LIFE-USDT-SWAP', 'long']['pos'] == '6.0'
    assert venue.orders['partial']['state'] == ('canceled' if outcome == 'canceled' else 'partially_filled')
    assert venue.cleanup_partial_event['clOrdId'] == 'partial' and venue.cleanup_partial_event['ws_dropped']
    if outcome == 'live':
        assert (await venue.control({'fill': dict(clOrdId='partial', amount='6', fee='.000001'), 'duplicate': True})).status == 200
    else:
        result = json.loads((await venue.handle(signed('/api/v5/trade/order', method='POST',
                                                       data={**terms, 'clOrdId': 'residual', 'sz': '6'}))).text)
        assert result['data'][0]['sCode'] == '0'
    assert venue.positions['LIFE-USDT-SWAP', 'long']['pos'] == '0.0' and len(venue.fills) == 2
    assert not venue.unknown


@pytest.mark.asyncio
async def test_invalid_partial_mode_cannot_change_the_fixture_policy():
    venue = exchange()
    assert (await venue.control({'cleanup_partial': 'pretend-flat'})).status == 400
    assert venue.cleanup_partial == 'none'
