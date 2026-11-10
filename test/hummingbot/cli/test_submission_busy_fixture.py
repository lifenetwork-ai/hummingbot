import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed

import pytest


@pytest.mark.asyncio
async def test_busy_fault_is_http429_without_venue_acceptance_and_can_be_disabled():
    venue = exchange()
    assert (await venue.control({'submission_busy': 'true'})).status == 400
    assert not venue.submission_busy
    await venue.control({'submission_busy': True})
    terms = dict(instId='LIFE-USDT', clOrdId='busy', side='sell', tdMode='cash', ordType='post_only', sz='1', px='.101')
    response = await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))
    assert response.status == 429 and json.loads(response.text)['code'] == '50013'
    assert not venue.orders and not venue.accepted and not venue.fills
    request = signed('/api/v5/trade/order')
    request.query = {'instId': 'LIFE-USDT', 'clOrdId': 'busy'}
    absent = await venue.handle(request)
    assert json.loads(absent.text)['code'] == '51603'
    assert venue.requests[-2]['injected_submission_busy']
    await venue.control({'submission_busy': False})
    assert (await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))).status == 200
    assert len(venue.accepted) == 1
