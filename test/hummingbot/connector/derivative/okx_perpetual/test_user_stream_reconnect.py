"""A failed reconnect must not retire the socket from an earlier iteration."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_user_stream_data_source import (
    OkxPerpetualUserStreamDataSource,
)


@pytest.mark.asyncio
async def test_stream_survives_close_then_repeated_handshake_failures_and_delivers_again():
    source = OkxPerpetualUserStreamDataSource(MagicMock(), MagicMock())
    first, second = MagicMock(), MagicMock()
    first.ping, second.ping = AsyncMock(), AsyncMock()
    source._get_connected_websocket_assistant = AsyncMock(side_effect=[
        first, OSError('handshake 502'), OSError('handshake 502'), second])
    source._subscribe_to_channels = AsyncMock()
    source._sleep = AsyncMock()
    source._on_user_stream_interruption = AsyncMock()
    delivered = asyncio.Event()
    output = asyncio.Queue()

    async def messages(websocket_assistant, queue):
        if websocket_assistant is first:
            raise ConnectionError('close 1006')
        await queue.put({'order': 'after-reconnect'})
        delivered.set()
        await asyncio.Event().wait()

    source._process_websocket_messages = messages
    task = asyncio.create_task(source._listen_for_user_stream_on_url('offline', output))
    waiter = asyncio.create_task(delivered.wait())
    try:
        done, _ = await asyncio.wait([task, waiter], timeout=1,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()  # Surface the exact failure instead of a timeout.
        assert delivered.is_set()
        assert await output.get() == {'order': 'after-reconnect'}
        assert source._ws_assistants == [second]
        assert [call.args for call in source._sleep.await_args_list] == [(5.0,)] * 3
    finally:
        task.cancel()
        waiter.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)
        assert source._ws_assistants == []
    calls = [call.args[0] for call in source._on_user_stream_interruption.await_args_list]
    assert calls == [first, None, None, second]
