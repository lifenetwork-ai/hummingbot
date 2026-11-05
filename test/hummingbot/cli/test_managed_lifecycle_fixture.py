"""Validate failure stimuli before relying on their image-level acceptance results."""
import asyncio
import base64
import hashlib
import hmac
import json
from test.e2e.offline.exchange import Exchange
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def exchange():
    return Exchange(fixtures=dict(order_event={'arg': {'channel': 'orders'}, 'data': [{}]},
                                  balances={'data': [{'details': []}]}))


def signed(path, *, method='GET', data=None, generation='a'):
    suffix = '' if generation == 'a' else '-b'
    raw = json.dumps(data) if data is not None else ''
    signature = base64.b64encode(hmac.new(('offline-secret' + suffix).encode(),
                                          ('1' + method + path + raw).encode(), hashlib.sha256).digest()).decode()
    return SimpleNamespace(path=path, path_qs=path, method=method, query={},
                           headers={'OK-ACCESS-KEY': 'offline-key' + suffix, 'OK-ACCESS-TIMESTAMP': '1',
                                    'OK-ACCESS-PASSPHRASE': 'offline-pass' + suffix, 'OK-ACCESS-SIGN': signature,
                                    'expTime': '9999999999999'}, text=AsyncMock(return_value=raw))


@pytest.mark.asyncio
async def test_rotated_key_authenticates_its_own_signature_and_expired_key_cannot_mutate():
    venue = exchange()
    await venue.control({'credential_generations': ['a', 'b']})
    for generation in ('a', 'b'):
        response = await venue.handle(signed('/api/v5/account/config', generation=generation))
        assert json.loads(response.text)['code'] == '0'
        assert venue.requests[-1]['credential_generation'] == generation
    await venue.control({'credential_generations': ['b']})
    response = await venue.handle(signed('/api/v5/trade/order', method='POST', data={'clOrdId': 'must-not-send'}))
    assert json.loads(response.text)['code'] == '50119'
    assert venue.requests[-1]['authorized'] is False
    assert not venue.accepted and not venue.unknown
    request = signed('/api/v5/account/config', generation='b')
    request.headers['OK-ACCESS-SIGN'] = signed('/api/v5/account/config').headers['OK-ACCESS-SIGN']
    assert json.loads((await venue.handle(request)).text)['code'] == '50113'


@pytest.mark.asyncio
async def test_rotated_websocket_records_generation_and_rejects_revoked_login():
    venue = exchange()
    await venue.control({'credential_generations': ['b']})
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', venue.handle)
    async with TestClient(TestServer(app)) as client:
        for generation in ('a', 'b'):
            async with client.ws_connect('/ws/private') as socket:
                suffix = '' if generation == 'a' else '-b'
                signature = base64.b64encode(hmac.new(('offline-secret' + suffix).encode(),
                                                      b'1GET/users/self/verify', hashlib.sha256).digest()).decode()
                await socket.send_json({'op': 'login', 'args': [dict(apiKey='offline-key' + suffix,
                                                                     passphrase='offline-pass' + suffix,
                                                                     timestamp='1', sign=signature)]})
                if generation == 'a':
                    assert (await socket.receive_json())['code'] == '50119'
                else:
                    assert (await socket.receive_json())['code'] == '0'
                    await socket.send_json({'op': 'subscribe', 'args': [{'channel': 'positions'}]})
                    await socket.receive_json()
                    assert venue.subscriptions[-1]['credential_generation'] == 'b'
    assert not venue.unknown


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['accepted', 'verified_fill'])
async def test_cleanup_barrier_preserves_independent_venue_truth_until_explicit_release(boundary):
    venue = exchange()
    venue.positions[('LIFE-USDT-SWAP', 'long')] = dict(instId='LIFE-USDT-SWAP', posSide='long',
                                                       pos='10', avgPx='.1', mgnMode='cross')
    await venue.control({'cleanup_market_closes': True, 'cleanup_barrier': boundary})
    placement = asyncio.create_task(venue.handle(signed('/api/v5/trade/order', method='POST', data=dict(
        instId='LIFE-USDT-SWAP', clOrdId='close', side='sell', posSide='long', tdMode='cross', sz='10', ordType='market'))))
    try:
        await asyncio.sleep(0)
        assert len(venue.accepted) == 1
        if boundary == 'accepted':
            assert not placement.done() and not venue.fills
            assert venue.positions['LIFE-USDT-SWAP', 'long']['pos'] == '10'
            assert venue.cleanup_barrier_hits[-1]['stage'] == 'accepted'
            await venue.control({'cleanup_barrier': 'none'})
            assert json.loads((await placement).text)['code'] == '0'
        else:
            await placement
            assert len(venue.fills) == 1 and venue.positions['LIFE-USDT-SWAP', 'long']['pos'] == '0'
            readback = asyncio.create_task(venue.handle(signed('/api/v5/account/positions')))
            try:
                await asyncio.sleep(0)
                assert not readback.done() and venue.cleanup_barrier_hits[-1]['stage'] == 'verified_fill'
                await venue.control({'cleanup_barrier': 'none'})
                assert json.loads((await readback).text)['code'] == '0'
            finally:
                readback.cancel()
                await asyncio.gather(readback, return_exceptions=True)
        assert len(venue.fills) == 1 and venue.fills[0]['fillSz'] == '10'
        assert not venue.unknown
    finally:
        placement.cancel()
        await asyncio.gather(placement, return_exceptions=True)
