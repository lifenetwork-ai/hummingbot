"""A real delayed SELECT must not prevent the unchanged engine HTTP probe."""
import asyncio
import threading
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import managed_native as _managed_native
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from aiohttp import web
from sqlalchemy import select, text

from hummingbot.cli.service import ServiceHost
from hummingbot.core import automatic_recovery as module
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.model.order import Order
from hummingbot.model.position import Position

managed_native, owned = _managed_native, _owned


@pytest.mark.parametrize('blocking_control', [False, True])
def test_delayed_terminal_select_keeps_native_probe_and_loop_progress(managed_native, monkeypatch, blocking_control):
    context, exchange, recorder, _ = managed_native
    order = exchange.in_flight_orders['auto-order']
    raw = dict(clOrdId=order.client_order_id, ordId='venue-auto', instId='BTC-USDT', side='buy',
               ordType='post_only', px='100', sz='.1', accFillSz='0', state='canceled', uTime='1700000001000')
    update = OrderUpdate(order.trading_pair, 1700000001, OrderState.CANCELED, order.client_order_id,
                         'venue-auto', {'venue_order': raw})
    original = module.durable_fills
    reading = threading.Event()
    threads, backends, progress = [], [], []
    main = threading.get_ident()
    identity = context.owner.owner_uuid, context.owner.generation, context.owner.backend_pid

    def delayed(connection, *args, **kwargs):
        threads.append(threading.get_ident())
        backends.append(connection.execute(text('SELECT pg_backend_pid()')).scalar_one())
        reading.set()
        connection.execute(text('SELECT pg_sleep(.4)'))
        return original(connection, *args, **kwargs)

    monkeypatch.setattr(module, 'durable_fills', delayed)

    async def run():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', bootstrapped=True, running=True,
                               observer_progress_at=None, observer_wait_until=None)
        app = web.Application()

        async def health(request):
            return await ServiceHost.health(host, request)

        app.router.add_get('/live', health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        def probe():
            assert reading.wait(3), 'The actual SQL read was not reached'
            try:
                with urlopen(f'http://127.0.0.1:{port}/live', timeout=.3) as response:
                    return response.status == 200
            except (TimeoutError, URLError):
                return False

        async def heartbeat():
            while True:
                await asyncio.sleep(.02)
                if reading.is_set():
                    progress.append(True)

        heartbeat_task = asyncio.create_task(heartbeat())
        probe_task = asyncio.create_task(asyncio.to_thread(probe))
        await asyncio.sleep(.01)
        try:
            if blocking_control:
                with context.engine.connect() as connection:
                    assert delayed(connection, order.client_order_id, [], missing_ok=True)
            else:
                proved = await context.owner.automatic_recovery.terminal_update(exchange, order, update)
                assert proved == update
            healthy = await probe_task
            assert healthy is (not blocking_control)
            if not blocking_control:
                assert len(progress) >= 2
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await asyncio.gather(probe_task, return_exceptions=True)
            await runner.cleanup()

    context.loop.run_until_complete(run())
    assert identity == (context.owner.owner_uuid, context.owner.generation, context.owner.backend_pid)
    context.owner.check_owner()
    assert bool(threads) and all((thread == main) is blocking_control for thread in threads)
    assert context.owner.backend_pid not in backends
    assert not context.owner.terminal_tasks and context.manager.persistence_healthy
    with context.engine.connect() as connection:
        assert connection.execute(select(Order.creation_pending).where(Order.id == order.client_order_id)).scalar_one()
        assert not connection.execute(select(Position.id)).first()
    assert order.executed_amount_base == Decimal(0)
