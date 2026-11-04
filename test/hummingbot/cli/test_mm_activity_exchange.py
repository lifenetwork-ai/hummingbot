import asyncio
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
async def test_terminal_before_ack_fault_bounds_admissions_until_release():
    venue = exchange()
    await venue.control({'hold_ack': True, 'cancel_before_ack': True, 'cancel_before_ack_limit': 2})
    tasks = []
    try:
        for identity in ('one', 'two', 'three'):
            tasks.append(asyncio.create_task(venue.handle(request('/api/v5/trade/order', dict(
                clOrdId=identity, instId='LIFE-USDT', side='buy', sz='1', px='.1')))))
            await asyncio.sleep(0)
        # Replacement creates must not turn one bounded race into a stream of
        # cancellations while the caller verifies durable terminal observations.
        assert len(venue.accepted) == 2
        assert all(row['state'] == 'canceled' for row in venue.orders.values())
        assert not any(task.done() for task in tasks)
    finally:
        await venue.control({'hold_ack': False, 'cancel_before_ack': False})
        replies = await asyncio.gather(*tasks)
    assert all(json.loads(reply.text)['code'] == '0' for reply in replies)
    assert len(venue.accepted) == 3
    assert venue.orders['three']['state'] == 'live'


@pytest.mark.asyncio
async def test_unbounded_lost_ack_fault_still_holds_all_accepted_orders():
    venue = exchange()
    await venue.control({'hold_ack': True, 'cancel_before_ack': True})
    tasks = []
    try:
        for identity in ('one', 'two', 'three'):
            tasks.append(asyncio.create_task(venue.handle(request('/api/v5/trade/order', dict(
                clOrdId=identity, instId='LIFE-USDT', side='buy', sz='1', px='.1')))))
            await asyncio.sleep(0)
        assert len(venue.accepted) == 3 and not any(task.done() for task in tasks)
    finally:
        await venue.control({'hold_ack': False, 'cancel_before_ack': False})
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_bounded_fault_rechecks_expiry_before_accepting_a_waiting_replacement(monkeypatch):
    monkeypatch.setattr('test.e2e.offline.exchange.millis', lambda: '100')
    venue = exchange()
    await venue.control({'hold_ack': True, 'cancel_before_ack': True, 'cancel_before_ack_limit': 1})
    tasks = []
    try:
        for identity in ('accepted', 'replacement'):
            message = request('/api/v5/trade/order', dict(clOrdId=identity, instId='LIFE-USDT', side='buy', sz='1', px='.1'))
            message.headers['expTime'] = '150'
            tasks.append(asyncio.create_task(venue.handle(message)))
            await asyncio.sleep(0)
        assert len(venue.accepted) == 1 and not any(task.done() for task in tasks)
        monkeypatch.setattr('test.e2e.offline.exchange.millis', lambda: '200')
    finally:
        await venue.control({'hold_ack': False, 'cancel_before_ack': False})
        accepted, expired = await asyncio.gather(*tasks)
    assert json.loads(accepted.text)['code'] == '0'
    assert json.loads(expired.text)['code'] != '0'
    assert 'replacement' not in venue.orders and len(venue.accepted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [None, False, True, -1, 0, 201, 1.5, '2'])
async def test_invalid_terminal_fault_bound_does_not_arm_the_hold(limit):
    venue = exchange()
    result = await venue.control({'hold_ack': True, 'cancel_before_ack': True, 'cancel_before_ack_limit': limit})
    assert result.status == 400
    assert not venue.hold_ack and not venue.cancel_before_ack and venue.release.is_set()


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
