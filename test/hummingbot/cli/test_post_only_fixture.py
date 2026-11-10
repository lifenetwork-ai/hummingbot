import asyncio
import json
from decimal import Decimal
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed
from test.hummingbot.strategy_v2.controllers.test_quote_guard import book, order
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.utils.quote_guard import observe_market


@pytest.mark.asyncio
@pytest.mark.parametrize('side,price,canceled', [('buy', '.101', True), ('sell', '.099', True), ('buy', '.099', False), ('sell', '.101', False)])
async def test_passive_fixture_cancels_crossing_order_with_successful_ack_and_no_fill(side, price, canceled):
    venue = exchange()
    await venue.control({'enforce_post_only': True})
    terms = dict(instId='LIFE-USDT', clOrdId='entry', side=side, tdMode='cash', ordType='post_only', sz='1', px=price)
    reply = json.loads((await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))).text)
    assert reply['code'] == '0' and reply['data'][0]['sCode'] == '0'
    order = venue.orders['entry']
    assert order['state'] == ('canceled' if canceled else 'live')
    assert order.get('cancelSource') == ('31' if canceled else None)
    assert not venue.fills and order['accFillSz'] == '0'


@pytest.mark.parametrize('closed_count', [2, 4])
def test_thin_maker_l1_is_not_healthy_liquidity_after_conservative_terminal_filter(closed_count):
    cached = {}
    for side, price in ((TradeType.BUY, '.099'), (TradeType.SELL, '.101')):
        for index in range(closed_count):
            item = order(identity=f'{side.name}-{index}', side=side, price=price, amount='333', state=OrderState.CANCELED)
            cached[item.client_order_id] = item
    connector = SimpleNamespace(name='okx', in_flight_orders={},
                                _order_tracker=SimpleNamespace(cached_orders=cached, lost_orders={}))

    def sample(deep):
        bids = [(.099, 1000)] + ([(.098999, 1000)] if deep else [])
        asks = [(.101, 1000)] + ([(.101001, 1000)] if deep else [])
        return observe_market(connector, book(bids, asks), 'LIFE-USDT', now=1000,
                              max_age_seconds=10, max_levels=50, max_orders=200)

    shallow, deep = sample(False), sample(True)
    assert min(shallow.external_bid_quote, shallow.external_ask_quote) < Decimal('50')
    assert deep.market_mid == Decimal('.100')
    assert min(deep.external_bid_quote, deep.external_ask_quote) >= Decimal('50')
    assert deep.market_spread_bps <= Decimal('250')


@pytest.mark.asyncio
async def test_post_only_cancel_also_preserves_the_existing_hold_ack_fault():
    venue = exchange()
    await venue.control({'enforce_post_only': True, 'hold_ack': True})
    terms = dict(instId='LIFE-USDT', clOrdId='held', side='sell', tdMode='cash',
                 ordType='post_only', sz='1', px='.099')
    task = asyncio.create_task(venue.handle(signed('/api/v5/trade/order', method='POST', data=terms)))
    try:
        for _ in range(20):
            if 'held' in venue.orders:
                break
            await asyncio.sleep(0)
        assert venue.orders['held']['cancelSource'] == '31'
        assert not task.done()
        venue.release.set()
        assert json.loads((await asyncio.wait_for(task, 1)).text)['data'][0]['sCode'] == '0'
    finally:
        venue.release.set()
        await asyncio.gather(task, return_exceptions=True)
