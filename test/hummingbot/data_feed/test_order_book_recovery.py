import asyncio
from test.hummingbot.strategy_v2.controllers.test_mm_book_recovery import book
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.data_feed.market_data_provider import MarketDataProvider


def snapshot(pair, uid):
    return OrderBookMessage(OrderBookMessageType.SNAPSHOT, dict(
        trading_pair=pair, update_id=uid, bids=[[99, 10]], asks=[[101, 10]]), uid)


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), timeout=1)


@pytest_asyncio.fixture
async def recovery():
    clock = [990.0]  # Arm staleness only after each test configures its stimulus.
    books = {'BTC-USDT': book(990), 'ETH-USDT': book(990), 'AVAX-USDT': book(990)}
    source = SimpleNamespace(_order_book_snapshot=AsyncMock(side_effect=lambda trading_pair: snapshot(trading_pair, 1000)))
    stream = asyncio.Queue()
    connector = MagicMock()
    connector.name = 'okx'
    connector.trading_pairs = list(books)
    connector.order_book_tracker = SimpleNamespace(data_source=source, order_books=books,
                                                   _order_book_snapshot_stream=stream)
    provider = MarketDataProvider({'okx': connector})
    provider.time = lambda: clock[0]
    provider.acquire_book_recovery('okx', ['BTC-USDT'], 'first', 10)
    watcher = provider._book_recovery
    watcher.POLL_INTERVAL = .005
    watcher.clock = lambda: clock[0]
    try:
        yield SimpleNamespace(provider=provider, watcher=watcher, source=source, books=books, stream=stream, clock=clock,
                              connector=connector, start=lambda: clock.__setitem__(0, 1000.0))
    finally:
        provider.stop()
        await provider.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', [980, 1002, 0])
async def test_stale_future_or_missing_venue_stamp_cannot_become_fresh(recovery, uid):
    x = recovery
    x.source._order_book_snapshot.side_effect = lambda trading_pair: snapshot(trading_pair, uid)
    x.start()
    await until(lambda: x.watcher.entries['okx', 'BTC-USDT'].failures == 1)
    assert x.stream.empty() and x.books['BTC-USDT'].snapshot_uid == 990
    await asyncio.sleep(.03)
    assert x.source._order_book_snapshot.await_count == 1
    x.clock[0] += 2
    await until(lambda: x.source._order_book_snapshot.await_count == 2)


@pytest.mark.asyncio
async def test_queued_snapshot_is_single_flight_until_tracker_applies_it(recovery):
    x = recovery
    x.start()
    await until(lambda: x.stream.qsize() == 1)
    x.clock[0] += 100
    await asyncio.sleep(.03)
    assert x.source._order_book_snapshot.await_count == 1 and x.stream.qsize() == 1


@pytest.mark.asyncio
async def test_rest_same_uid_cannot_replace_book_and_is_rate_bounded(recovery):
    x = recovery
    x.source._order_book_snapshot.side_effect = lambda trading_pair: snapshot(trading_pair, 990)
    # Use a still-valid snapshot at the current uid; it makes no progress.
    x.clock[0] = 996
    await until(lambda: x.source._order_book_snapshot.await_count == 1)
    await asyncio.sleep(.03)
    assert x.stream.empty() and x.source._order_book_snapshot.await_count == 1


@pytest.mark.asyncio
async def test_ws_recovery_during_rest_read_wins_without_rollback(recovery):
    x = recovery
    started, release = asyncio.Event(), asyncio.Event()

    async def read(trading_pair):
        started.set()
        await release.wait()
        return snapshot(trading_pair, 1000)

    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(started.is_set)
    x.books['BTC-USDT'].apply_snapshot(snapshot('BTC-USDT', 1000).bids, snapshot('BTC-USDT', 1000).asks, 1000)
    release.set()
    await until(lambda: not x.watcher.jobs)
    assert x.stream.empty() and x.books['BTC-USDT'].snapshot_uid == 1000


@pytest.mark.asyncio
async def test_two_consumers_share_one_request_and_last_release_drains(recovery):
    x = recovery
    x.provider.acquire_book_recovery('okx', ['BTC-USDT'], 'second', 10)
    x.start()
    await until(lambda: x.stream.qsize() == 1)
    x.provider.release_book_recovery('first')
    assert 'okx' in {name for name, _ in x.watcher.entries}
    x.provider.release_book_recovery('second')
    await x.provider.wait_closed()
    assert not x.watcher.entries and not x.watcher.inflight and x.watcher.task is None


@pytest.mark.asyncio
async def test_stop_cancels_bounded_requests_and_does_not_publish(recovery):
    x = recovery
    started = asyncio.Event()

    async def read(trading_pair):
        started.set()
        await asyncio.Event().wait()

    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(started.is_set)
    x.provider.stop()
    await x.provider.wait_closed()
    assert x.stream.empty() and not x.watcher.entries and not x.watcher.inflight


@pytest.mark.asyncio
async def test_slow_pairs_have_bounded_parallelism_and_cannot_starve_third(recovery):
    x = recovery
    release = asyncio.Event()
    calls = []

    async def read(trading_pair):
        calls.append(trading_pair)
        await release.wait()
        return snapshot(trading_pair, 1000)

    x.source._order_book_snapshot.side_effect = read
    x.provider.acquire_book_recovery('okx', ['ETH-USDT', 'AVAX-USDT'], 'first', 10)
    x.start()
    await until(lambda: len(calls) == 2)
    await asyncio.sleep(.02)
    assert len(calls) == len(x.watcher.inflight) == 2
    release.set()
    await until(lambda: len(calls) == 3)
    await until(lambda: x.stream.qsize() == 3)


@pytest.mark.asyncio
async def test_release_and_reacquire_rejects_old_inflight_snapshot(recovery):
    x = recovery
    started, release = asyncio.Event(), asyncio.Event()

    async def read(trading_pair):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return snapshot(trading_pair, 1000)

    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(started.is_set)
    old = x.watcher.entries['okx', 'BTC-USDT']
    x.provider.release_book_recovery('first')
    x.provider.acquire_book_recovery('okx', ['BTC-USDT'], 'new', 10)
    # Keep the successor healthy so only the retired request can finish.
    fresh = snapshot('BTC-USDT', 1000)
    x.books['BTC-USDT'].apply_snapshot(fresh.bids, fresh.asks, 1000)
    release.set()
    await x.provider.wait_closed()
    assert x.watcher.entries['okx', 'BTC-USDT'] is not old and x.stream.empty()


@pytest.mark.asyncio
async def test_stricter_consumer_added_during_request_rejects_old_snapshot(recovery):
    x = recovery
    started, release = asyncio.Event(), asyncio.Event()

    async def read(trading_pair):
        started.set()
        await release.wait()
        return snapshot(trading_pair, 996)

    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(started.is_set)
    x.provider.acquire_book_recovery('okx', ['BTC-USDT'], 'strict', 2)
    release.set()
    await until(lambda: x.watcher.entries['okx', 'BTC-USDT'].failures == 1)
    assert x.stream.empty()


@pytest.mark.asyncio
async def test_timeout_releases_slot_and_keeps_backoff(recovery):
    x = recovery
    x.watcher.REQUEST_TIMEOUT = .02

    async def read(trading_pair):
        await asyncio.Event().wait()
    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(lambda: x.watcher.entries['okx', 'BTC-USDT'].failures == 1)
    await until(lambda: not x.watcher.inflight)
    assert x.stream.empty() and x.source._order_book_snapshot.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['pair', 'type', 'tracker'])
async def test_response_cannot_publish_into_wrong_book_or_replaced_tracker(recovery, invalid):
    x = recovery
    started, release = asyncio.Event(), asyncio.Event()

    async def read(trading_pair):
        started.set()
        await release.wait()
        result = snapshot('ETH-USDT' if invalid == 'pair' else trading_pair, 1000)
        if invalid == 'type':
            result = OrderBookMessage(OrderBookMessageType.DIFF, result.content, 1000)
        return result

    x.source._order_book_snapshot.side_effect = read
    x.start()
    await until(started.is_set)
    if invalid == 'tracker':
        x.connector.order_book_tracker = SimpleNamespace(data_source=x.source, order_books=x.books,
                                                         _order_book_snapshot_stream=asyncio.Queue())
    release.set()
    await until(lambda: not x.watcher.jobs)
    assert x.stream.empty() and x.connector.order_book_tracker._order_book_snapshot_stream.empty()


@pytest.mark.asyncio
async def test_applied_snapshot_then_ws_updates_avoid_more_rest_and_release_has_no_retention(recovery):
    x = recovery
    x.start()
    await until(lambda: x.stream.qsize() == 1)
    fresh = x.stream.get_nowait()
    x.books['BTC-USDT'].apply_snapshot(fresh.bids, fresh.asks, fresh.update_id)
    x.clock[0] = 1005
    newer = snapshot('BTC-USDT', 1005)
    x.books['BTC-USDT'].apply_diffs(newer.bids, newer.asks, newer.update_id)
    await until(lambda: not x.watcher.entries['okx', 'BTC-USDT'].pending_uid)
    await asyncio.sleep(.02)
    assert x.source._order_book_snapshot.await_count == 1
    for _ in range(25):
        x.provider.release_book_recovery('first')
        await x.provider.wait_closed()
        assert not x.watcher.inflight and not x.watcher.jobs and not x.watcher.entries
        x.provider.acquire_book_recovery('okx', ['BTC-USDT'], 'first', 10)


@pytest.mark.asyncio
async def test_perpetual_snapshot_keeps_native_millisecond_uid():
    provider = MarketDataProvider({})
    provider.time = lambda: 1000
    source = SimpleNamespace(_order_book_snapshot=AsyncMock(return_value=snapshot('BTC-USDT', 1000000)))
    stream = asyncio.Queue()
    tracker = SimpleNamespace(data_source=source, order_books={'BTC-USDT': book(990000)},
                              _order_book_snapshot_stream=stream)
    connector = SimpleNamespace(order_book_tracker=tracker)
    provider.get_connector_with_fallback = lambda name: connector
    try:
        provider.acquire_book_recovery('okx_perpetual', ['BTC-USDT'], 'owner', 10)
        await until(lambda: stream.qsize() == 1)
        message = stream.get_nowait()
        assert message.update_id == 1000000
        assert tracker.order_books['BTC-USDT'].snapshot_uid == 990000
    finally:
        provider.stop()
        await provider.wait_closed()
