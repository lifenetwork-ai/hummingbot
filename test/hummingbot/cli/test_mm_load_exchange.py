import json
from test.e2e.offline.exchange import Exchange
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["canceled", "filled"])
async def test_fill_candidate_canceled_after_ledger_read_cannot_reopen_terminal_order(state):
    fixtures = dict(order_event={"arg": {"channel": "orders"}, "data": [{}]}, balances={"data": [{"details": []}]})
    with patch("test.e2e.offline.exchange.Path.read_text", return_value=json.dumps(fixtures)):
        exchange = Exchange()
    order = dict(instId="LIFE-USDT", clOrdId="one", ordId="1", state=state,
                 accFillSz="0", sz="1", side="buy", px="0.1")
    exchange.orders["one"] = order
    response = await exchange.control({"fill": {"clOrdId": "one", "amount": "0.001", "fee": "0.000001"}})
    assert response.status == 409
    assert order["state"] == state and order["accFillSz"] == "0"
    assert exchange.fills == []


@pytest.mark.asyncio
async def test_perpetual_fill_snapshot_is_not_changed_by_a_later_cancel_during_publication():
    fixtures = dict(order_event={"arg": {"channel": "orders"}, "data": [{}]}, balances={"data": [{"details": []}]})
    with patch("test.e2e.offline.exchange.Path.read_text", return_value=json.dumps(fixtures)):
        exchange = Exchange()
    order = dict(instId="LIFE-USDT-SWAP", clOrdId="one", ordId="1", state="live", posSide="long",
                 accFillSz="0", sz="1", side="buy", px="0.1")
    exchange.orders["one"] = order
    events = []

    async def publish(message):
        events.append(message)
        if message["arg"]["channel"] == "positions":
            order["state"] = "canceled"
    exchange.broadcast = publish
    response = await exchange.control({"fill": {"clOrdId": "one", "amount": "0.1", "fee": "0.000001"}})
    assert response.status == 200
    fill_event = next(event for event in events if event["arg"]["channel"] == "orders")
    assert fill_event["data"][0]["state"] == "partially_filled"
    assert order["state"] == "canceled"


@pytest.mark.asyncio
async def test_failed_burst_measurement_always_restores_the_steady_feed(monkeypatch):
    import sys
    from test.e2e.offline import load_trace
    from unittest.mock import AsyncMock
    with patch.dict(sys.modules, {'load_trace': load_trace}):
        from test.e2e.offline.load_client import measure_market_burst
    controls = []
    ledgers = iter([dict(market_update_count=0, observed_at_monotonic=0),
                    dict(market_update_count=4990, observed_at_monotonic=10)])

    async def exchange(path, payload=None):
        if path == '/control':
            controls.append(payload['target_updates_per_second'])
            return {}
        return next(ledgers)
    monkeypatch.setattr('test.e2e.offline.load_client.asyncio.sleep', AsyncMock())
    with pytest.raises(AssertionError, match='Burst feed'):
        await measure_market_burst(exchange, 100)
    assert controls == [500, 100]


@pytest.mark.asyncio
async def test_emitter_counts_only_delivered_book_frames_under_burst_load():
    import asyncio
    fixtures = dict(balances={'data': [{'details': []}]})
    with patch('test.e2e.offline.exchange.Path.read_text', return_value=json.dumps(fixtures)):
        exchange = Exchange()
    messages = []

    class Socket:
        closed = False

        async def send_json(self, message):
            messages.append(message)
    socket = Socket()
    exchange.socket_args[socket] = [dict(channel='books', instId='LIFE-USDT'), dict(channel='candle1s', instId='LIFE-USDT')]
    exchange.target_updates_per_second = 500
    task = asyncio.create_task(exchange.market_updates())
    try:
        await asyncio.sleep(.1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert exchange.market_update_count > 0
    assert exchange.market_update_count == sum(row['arg']['channel'] == 'books' for row in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('closing_channel', ['books', 'candle1s'])
async def test_closed_transport_during_market_send_does_not_kill_other_subscribers(closing_channel):
    import asyncio

    from aiohttp import ClientConnectionResetError
    with patch('test.e2e.offline.exchange.Path.read_text', return_value=json.dumps({})):
        exchange = Exchange()
    delivered = asyncio.Event()
    messages = []

    class ClosingSocket:
        closed = False  # The transport closes after the initial closed check.

        async def send_json(self, message):
            raise ClientConnectionResetError('Cannot write to closing transport')

    class HealthySocket:
        closed = False

        async def send_json(self, message):
            messages.append(message)
            delivered.set()

    closing, healthy = ClosingSocket(), HealthySocket()
    exchange.socket_args[closing] = [dict(channel=closing_channel, instId='LIFE-USDT')]
    exchange.socket_args[healthy] = [dict(channel='books', instId='LIFE-USDT')]
    exchange.sockets[closing] = {closing_channel}
    exchange.target_updates_per_second = 500
    task = asyncio.create_task(exchange.market_updates())
    waiting = asyncio.create_task(delivered.wait())
    try:
        done, _ = await asyncio.wait((task, waiting), timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert waiting in done and not task.done(), 'A closed client killed the simulator market feed'
        assert closing not in exchange.socket_args and closing not in exchange.sockets
        assert exchange.market_update_count == len(messages) > 0
    finally:
        task.cancel()
        waiting.cancel()
        await asyncio.gather(task, waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_hot_load_reads_do_not_serialize_the_growing_exchange_ledger():
    from types import SimpleNamespace
    with patch('test.e2e.offline.exchange.Path.read_text', return_value=json.dumps({})):
        exchange = Exchange()
    exchange.orders = {str(i): dict(clOrdId=str(i), state='canceled') for i in range(1000)}
    exchange.orders['live'] = dict(clOrdId='live', state='live')
    exchange.orders['partial'] = dict(clOrdId='partial', state='partially_filled')
    exchange.fills = [{'tradeId': str(i)} for i in range(1000)]
    exchange.requests = [{'path': '/large-history'}] * 1000
    exchange.market_update_count = 1234
    metrics = json.loads((await exchange.handle(SimpleNamespace(path='/market-metrics', method='GET'))).text)
    working = json.loads((await exchange.handle(SimpleNamespace(path='/working-orders', method='GET'))).text)
    assert set(metrics) == {'market_update_count', 'observed_at_monotonic'}
    assert metrics['market_update_count'] == 1234 and metrics['observed_at_monotonic'] > 0
    assert set(working) == {'orders'}
    assert {row['clOrdId'] for row in working['orders']} == {'live', 'partial'}
    assert len(exchange.orders) == 1002 and len(exchange.fills) == 1000
