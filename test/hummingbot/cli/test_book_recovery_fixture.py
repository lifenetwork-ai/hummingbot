"""Quiet WS and stale REST are independent stimuli, not fabricated freshness."""
import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed

import pytest


@pytest.mark.asyncio
async def test_quiet_ws_keeps_rest_fresh_but_explicit_rest_faults_are_real():
    venue = exchange()
    request = signed('/api/v5/market/books')
    request.query = {'instId': 'ETH-USDT'}
    await venue.control({'stale_instruments': ['ETH-USDT']})
    fresh = json.loads((await venue.handle(request)).text)['data'][0]
    await venue.control({'rest_stale_instruments': ['ETH-USDT']})
    old = json.loads((await venue.handle(request)).text)['data'][0]
    assert 59000 <= int(fresh['ts']) - int(old['ts']) <= 61000
    assert old['asks'] == fresh['asks'] and old['bids'] == fresh['bids']
    request.query = {'instId': 'BTC-USDT'}
    healthy = json.loads((await venue.handle(request)).text)['data'][0]
    assert abs(int(healthy['ts']) - int(fresh['ts'])) < 1000
    await venue.control({'rest_failed_instruments': ['ETH-USDT']})
    request.query = {'instId': 'ETH-USDT'}
    assert (await venue.handle(request)).status == 503
    await venue.control({'rest_failed_instruments': [], 'rest_stale_instruments': []})
    assert (await venue.handle(request)).status == 200
