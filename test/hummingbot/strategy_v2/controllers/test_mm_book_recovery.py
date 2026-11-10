"""Quiet-pair recovery uses the real controller/provider lifecycle, no venue."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase


def book(stamp):
    item = OrderBook()
    item.apply_snapshot([OrderBookRow(99, 10, stamp)], [OrderBookRow(101, 10, stamp)], stamp)
    return item


@pytest.mark.asyncio
async def test_quiet_eth_recovers_without_requesting_healthy_btc():
    books = {'BTC-USDT': book(1000), 'ETH-USDT': book(990)}
    snapshot = OrderBookMessage(OrderBookMessageType.SNAPSHOT, dict(
        trading_pair='ETH-USDT', update_id=1000, bids=[[9, 10]], asks=[[11, 10]]), 1000)
    source = SimpleNamespace(_order_book_snapshot=AsyncMock(return_value=snapshot))
    stream = asyncio.Queue()
    connector = MagicMock()
    connector.name = 'okx'
    connector.trading_pairs = list(books)
    connector.order_book_tracker = SimpleNamespace(data_source=source, order_books=books,
                                                   _order_book_snapshot_stream=stream)
    connector.get_order_book.side_effect = books.__getitem__
    provider = MarketDataProvider({'okx': connector})
    provider.time = lambda: 1000
    config = LifeMMConfig(id='quiet-book', connector_name='okx', trading_pair='BTC-USDT',
                          scenario='follow_market', references=[{'pair': 'ETH-USDT', 'weight': '1'}],
                          min_price=Decimal('1'), max_price=Decimal('1000'), max_data_age_seconds=10,
                          max_base_inventory=Decimal('100'))
    controller = LifeMMController(config, provider, asyncio.Queue())
    try:
        with patch.object(ControllerBase, 'on_start', new_callable=AsyncMock):
            await controller.on_start()
        await asyncio.sleep(.05)
        source._order_book_snapshot.assert_awaited_once_with(trading_pair='ETH-USDT')
        message = stream.get_nowait()
        assert message.update_id == 1000 and message.trading_pair == 'ETH-USDT'
        assert books['ETH-USDT'].snapshot_uid == 990  # Queuing is not synthetic freshness.
        books['ETH-USDT'].apply_snapshot(message.bids, message.asks, message.update_id)
        assert books['ETH-USDT'].snapshot_uid == 1000
    finally:
        await controller.on_stop()
        provider.stop()
        await provider.wait_closed()
