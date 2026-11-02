import base64
import hashlib
import hmac
import json
from test.e2e.offline.exchange import Exchange
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def request(path, payload=None):
    raw = json.dumps(payload) if payload is not None else ''
    method = 'POST' if payload is not None else 'GET'
    signature = base64.b64encode(hmac.new(b'offline-secret', ('1' + method + path + raw).encode(), hashlib.sha256).digest()).decode()
    return SimpleNamespace(path=path, path_qs=path, method=method, query={},
                           headers={'OK-ACCESS-KEY': 'offline-key', 'OK-ACCESS-TIMESTAMP': '1',
                                    'OK-ACCESS-SIGN': signature, 'OK-ACCESS-PASSPHRASE': 'offline-pass', 'expTime': '9999999999999'},
                           text=AsyncMock(return_value=raw))


def exchange():
    return Exchange(fixtures=dict(order_event={'arg': {'channel': 'orders'}, 'data': [{}]},
                                  balances={'data': [{'details': []}]}))


@pytest.mark.asyncio
async def test_activity_is_opt_in_and_cannot_claim_prior_orders_have_known_monotonic_age():
    venue = exchange()
    assert (await venue.handle(request('/quote-activity'))).status == 404
    await venue.handle(request('/api/v5/trade/order', dict(clOrdId='one', instId='LIFE-USDT', side='buy', sz='1', px='.1')))
    assert (await venue.control({'mm_activity': True})).status == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['cancel', 'fill', 'cancel_before_ack'])
async def test_real_simulator_routes_track_live_partial_and_terminal_quotes(terminal):
    venue = exchange()
    assert (await venue.control({'mm_activity': True})).status == 200
    if terminal == 'cancel_before_ack':
        venue.cancel_before_ack = True
    await venue.handle(request('/api/v5/trade/order', dict(clOrdId='one', instId='LIFE-USDT', side='buy', sz='1', px='.1')))
    if terminal != 'cancel_before_ack':
        initial = json.loads((await venue.handle(request('/quote-activity'))).text)
        assert initial['tracking_complete'] and initial['orders'][0]['order_id'] == 'one'
        await venue.control({'fill': dict(clOrdId='one', amount='.1', fee='0')})
        assert len(json.loads((await venue.handle(request('/quote-activity'))).text)['orders']) == 1
        if terminal == 'cancel':
            await venue.handle(request('/api/v5/trade/cancel-order', dict(clOrdId='one')))
        else:
            await venue.control({'fill': dict(clOrdId='one', amount='.9', fee='0')})
    snapshot = json.loads((await venue.handle(request('/quote-activity'))).text)
    assert snapshot['orders'] == [] and len(snapshot['terminals']) == 1
    assert snapshot['terminals'][0]['state'] == ('filled' if terminal == 'fill' else 'canceled')
    if terminal == 'cancel':
        assert snapshot['terminals'][0]['request_to_terminal_ms'] >= 0
        await venue.handle(request('/api/v5/trade/cancel-order', dict(clOrdId='one')))
        assert len(json.loads((await venue.handle(request('/quote-activity'))).text)['terminals']) == 1
