"""Independently prove delayed pending views without delaying cancel truth."""
import json
from copy import deepcopy
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('instrument', ['LIFE-USDT', 'LIFE-USDT-SWAP'])
async def test_visibility_fault_armed_on_an_empty_book_covers_later_native_cancellations(instrument):
    venue = exchange()
    assert not venue.orders
    assert (await venue.control({'cancel_pending_visibility_reads': 2})).status == 200
    terms = dict(instId=instrument, clOrdId='created-after-arming', side='buy',
                 posSide='long', tdMode='cross', ordType='post_only', sz='1', px='.099')
    created = json.loads((await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))).text)
    assert created['data'][0]['sCode'] == '0'
    await venue.handle(signed('/api/v5/trade/cancel-order', method='POST',
                              data={'clOrdId': terms['clOrdId'], 'instId': instrument}))
    terminal = deepcopy(venue.orders[terms['clOrdId']])
    assert terminal['state'] == 'canceled' and terminal['accFillSz'] == '0'
    observations = []
    for _ in range(3):
        pending = json.loads((await venue.handle(signed('/api/v5/trade/orders-pending'))).text)
        observations.append([row['clOrdId'] for row in pending['data']])
        request = signed('/api/v5/trade/order')
        request.query = {'clOrdId': terms['clOrdId']}
        status = json.loads((await venue.handle(request)).text)
        assert status['data'] == [terminal]
        request = signed('/api/v5/trade/fills')
        request.query = {'ordId': terminal['ordId']}
        assert json.loads((await venue.handle(request)).text)['data'] == []
    assert observations == [[terms['clOrdId']], [terms['clOrdId']], []]
    assert venue.orders[terms['clOrdId']] == terminal
    assert venue.event(terminal)['data'][0]['state'] == 'canceled'
    assert len(venue.accepted) == 1 and not venue.fills and not venue.unknown
