import asyncio
from types import SimpleNamespace

import pytest

from hummingbot.data_feed.market_data_provider import MarketDataProvider


@pytest.mark.asyncio
async def test_public_connector_shutdown_is_retained_and_awaited():
    provider = MarketDataProvider({})
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def close():
        entered.set()
        await release.wait()
        finished.set()

    provider._non_trading_connectors = {"okx": SimpleNamespace(stop_network=close)}
    provider.stop()
    try:
        await asyncio.wait_for(entered.wait(), .5)
        waiting = asyncio.create_task(provider.wait_closed())
        await asyncio.sleep(0)
        assert not waiting.done()
        assert not finished.is_set()
    finally:
        release.set()
    await asyncio.wait_for(waiting, .5)
    assert finished.is_set()
    assert not provider._stopping_tasks
