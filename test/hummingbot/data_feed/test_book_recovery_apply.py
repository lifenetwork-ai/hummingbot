"""The final tracker consumer must not roll back a newer WS snapshot."""
import asyncio
from collections import deque
from test.hummingbot.data_feed.test_order_book_recovery import snapshot, until
from test.hummingbot.strategy_v2.controllers.test_mm_book_recovery import book
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.core.data_type.order_book_tracker import OrderBookTracker


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', [1000, 1002, 1004])
@pytest.mark.parametrize('recovery', [True, False])
async def test_ws_wins_even_when_recovery_snapshot_already_queued(uid, recovery):
    actual = book(1002)
    observed = SimpleNamespace(snapshot_uid=1002, last_diff_uid=1002,
                               restore_from_snapshot_and_diffs=MagicMock(wraps=actual.restore_from_snapshot_and_diffs))
    queue = asyncio.Queue()
    tracker = SimpleNamespace(_past_diffs_windows={'BTC-USDT': deque()}, _saved_message_queues={'BTC-USDT': deque()},
                              _tracking_message_queues={'BTC-USDT': queue}, _order_books={'BTC-USDT': observed})
    message = snapshot('BTC-USDT', uid)
    message.content['_mm_recovery'] = recovery
    queue.put_nowait(message)
    task = asyncio.create_task(OrderBookTracker._track_single_book(tracker, 'BTC-USDT'))
    try:
        await until(queue.empty)
        if recovery and uid <= 1002:
            assert actual.snapshot_uid == 1002
            observed.restore_from_snapshot_and_diffs.assert_not_called()
        else:
            assert actual.snapshot_uid == uid
            observed.restore_from_snapshot_and_diffs.assert_called_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
