"""Established-connection response loss, without firewall privileges or real DSNs."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace


@asynccontextmanager
async def response_loss_proxy(url):
    state = SimpleNamespace(drop_responses=False)
    writers, tasks = [], []

    async def forward(reader, writer, downstream=False):
        try:
            while data := await reader.read(65536):
                if not (downstream and state.drop_responses):
                    writer.write(data)
                    await writer.drain()
        finally:
            writer.close()

    async def connect(reader, writer):
        remote_reader, remote_writer = await asyncio.open_connection(url.host, url.port)
        writers.extend([writer, remote_writer])
        pair = [asyncio.create_task(forward(reader, remote_writer)),
                asyncio.create_task(forward(remote_reader, writer, True))]
        tasks.extend(pair)
        await asyncio.gather(*pair, return_exceptions=True)

    server = await asyncio.start_server(connect, '127.0.0.1', 0)
    state.url = url.set(host='127.0.0.1', port=server.sockets[0].getsockname()[1])
    try:
        yield state
    finally:
        server.close()
        for writer in writers:
            writer.close()
        for task in tasks:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
        await asyncio.wait_for(server.wait_closed(), 3)
