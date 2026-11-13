"""Dropped terminal delivery must not rewrite venue truth, fills or WS liveness."""
import asyncio
import copy
import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest


async def admitted(venue, identity):
    terms = dict(instId='LIFE-USDT', clOrdId=identity, side='buy', tdMode='cash',
                 ordType='post_only', sz='1', px='.099')
    response = await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))
    assert json.loads(response.text)['data'][0]['sCode'] == '0'
    return venue.orders[identity]


def read(path, **query):
    request = signed(path + '?' + urlencode(query))
    request.path, request.query = path, query
    return request


@pytest.mark.asyncio
async def test_selected_terminal_delivery_is_lost_but_ack_and_rest_keep_exact_truth():
    venue = exchange()
    first, second = await admitted(venue, 'first'), await admitted(venue, 'second')
    socket = Mock(closed=False, send_json=AsyncMock())
    venue.sockets[socket] = {'orders', 'account'}
    await venue.control({'suppress_terminal_ws_for': ['first']})
    response = await venue.handle(signed('/api/v5/trade/cancel-order', method='POST', data={'clOrdId': 'first'}))
    assert json.loads(response.text)['data'][0]['sCode'] == '0'
    assert first['state'] == 'canceled'
    assert not socket.send_json.await_count
    reply = await venue.handle(read('/api/v5/trade/order', clOrdId='first'))
    assert json.loads(reply.text)['data'] == [first]
    mixed = {'arg': {'channel': 'orders'}, 'data': [copy.deepcopy(first), copy.deepcopy(second)]}
    original = copy.deepcopy(mixed)
    await venue.broadcast(mixed)
    assert mixed == original
    assert socket.send_json.await_args.args[0]['data'] == [second]
    assert venue.suppressed_terminal_events and not venue.fills


@pytest.mark.asyncio
async def test_withheld_fill_view_releases_the_same_original_trade_and_fee():
    venue = exchange()
    order = await admitted(venue, 'filled')
    await venue.control({'suppress_terminal_ws_for': ['filled'], 'withhold_fills_for': ['filled']})
    await venue.control({'fill': dict(clOrdId='filled', amount='1', fee='.001'), 'duplicate': True})
    original = copy.deepcopy(venue.fills)
    assert order['state'] == 'filled' and len(original) == 1
    request = read('/api/v5/trade/fills', ordId=order['ordId'])
    assert json.loads((await venue.handle(request)).text)['data'] == []
    assert venue.withheld_fill_reads == {'filled': 1}
    assert venue.fills == original
    await venue.control({'withhold_fills_for': []})
    assert json.loads((await venue.handle(request)).text)['data'] == original
    assert venue.fills == original


@pytest.mark.asyncio
async def test_private_feed_stays_alive_without_replaying_suppressed_order_state():
    venue = exchange()
    await admitted(venue, 'first')
    received = asyncio.Event()

    async def frame(message):
        if message['arg']['channel'] == 'account':
            received.set()

    socket = Mock(closed=False, send_json=AsyncMock(side_effect=frame))
    venue.sockets[socket] = {'account', 'orders'}
    venue.socket_args[socket] = []
    await venue.control({'suppress_terminal_ws_for': ['first'], 'private_keepalive': True})
    task = asyncio.create_task(venue.market_updates())
    try:
        await asyncio.wait_for(received.wait(), 1)
        assert venue.private_keepalive_messages >= 1 and not socket.closed
        assert all(call.args[0]['arg']['channel'] == 'account' for call in socket.send_json.await_args_list)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['suppress_terminal_ws_for', 'withhold_fills_for'])
@pytest.mark.parametrize('value', ['first', None, ['missing'], ['first'] * 9])
async def test_terminal_fault_controls_are_bounded_and_known_id_only(field, value):
    venue = exchange()
    await admitted(venue, 'first')
    response = await venue.control({field: value})
    assert response.status == 400
