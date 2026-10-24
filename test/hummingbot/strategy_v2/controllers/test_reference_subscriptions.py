from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.data_feed.market_data_provider import MarketDataProvider


@pytest.mark.asyncio
async def test_shared_reference_is_removed_only_after_its_last_consumer():
    connector = MagicMock(trading_pairs=["LIFE-USDT"])
    provider = MarketDataProvider({"okx": connector})
    provider.initialize_order_book = AsyncMock(return_value=True)
    provider.remove_order_book = AsyncMock(return_value=True)
    first, second = object(), object()
    await provider.acquire_order_books("okx", ["BTC-USDT"], first)
    await provider.acquire_order_books("okx", ["BTC-USDT"], second)
    assert provider.initialize_order_book.await_count == 1
    await provider.release_order_books("okx", ["BTC-USDT"], first)
    provider.remove_order_book.assert_not_awaited()
    await provider.release_order_books("okx", ["BTC-USDT"], second)
    provider.remove_order_book.assert_awaited_once_with("okx", "BTC-USDT")


@pytest.mark.asyncio
async def test_trading_target_and_preexisting_subscriptions_are_retained():
    connector = MagicMock(trading_pairs=["LIFE-USDT", "BTC-USDT"])
    provider = MarketDataProvider({"okx": connector})
    provider.initialize_order_book = AsyncMock(return_value=True)
    provider.remove_order_book = AsyncMock(return_value=True)
    consumer = object()
    await provider.acquire_order_books("okx", ["LIFE-USDT", "BTC-USDT"], consumer)
    await provider.release_order_books("okx", ["LIFE-USDT", "BTC-USDT"], consumer)
    provider.remove_order_book.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_subscription_is_retryable_and_repeated_release_is_idempotent():
    provider = MarketDataProvider({"okx": MagicMock(trading_pairs=["LIFE-USDT"])})
    provider.initialize_order_book = AsyncMock(side_effect=[False, True])
    provider.remove_order_book = AsyncMock(return_value=True)
    consumer = object()
    assert await provider.acquire_order_books("okx", ["BTC-USDT"], consumer) == {"BTC-USDT": False}
    assert await provider.acquire_order_books("okx", ["BTC-USDT"], consumer) == {"BTC-USDT": True}
    await provider.release_order_books("okx", ["BTC-USDT"], consumer)
    await provider.release_order_books("okx", ["BTC-USDT"], consumer)
    provider.remove_order_book.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_trading_subscription_reports_initialization_timeout():
    provider = MarketDataProvider({})
    provider.get_connector_with_fallback = MagicMock(return_value=MagicMock())
    provider._ensure_non_trading_connector_started = AsyncMock(return_value=True)
    provider._wait_for_order_book_initialized = AsyncMock(return_value=False)
    assert await provider.initialize_order_book("okx", "BTC-USDT") is False
