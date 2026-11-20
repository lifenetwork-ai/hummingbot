"""Stop and native fill processing while a recorder transaction holds ownership."""
import asyncio
import threading
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture, reconciliation_core, send
from test.integration.postgres.test_recorder import create, fill
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.trade_fill import TradeFill

owned = owned_fixture


@pytest.mark.parametrize('outcome', ['drained', 'deadline', 'superseded'])
def test_stop_stays_responsive_while_recorder_holds_owner_row(owned, outcome):
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, create())
    core = reconciliation_core(owned)

    async def shutdown(**kwargs):
        assert not owned.owner.allow_new_orders
        assert owned.store.read()['recovery_state']['shutdown_incomplete']
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)

    async def exercise():
        locked, stopping, release = threading.Event(), threading.Event(), threading.Event()
        app = web.Application()
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]

        def hold_recorder_fence():
            with owned.engine.begin() as connection:
                connection.execute(text('SELECT bot_id FROM bot_state WHERE bot_id = :bot FOR SHARE'),
                                   {'bot': owned.store.bot_id})
                locked.set()
                assert stopping.wait(2)
                release.wait(.65)
                if outcome == 'superseded':
                    connection.execute(text('UPDATE bot_state SET control_version = control_version + 1 WHERE bot_id = :bot'),
                                       {'bot': owned.store.bot_id})

        def probe_during_stop():
            assert stopping.wait(2)
            return probe_health(port, '/live')

        async def stop():
            stopping.set()
            return await owned.owner.shutdown_core(core, timeout=.1 if outcome == 'deadline' else 2)

        loop = asyncio.get_running_loop()
        holder = loop.run_in_executor(None, hold_recorder_fence)
        try:
            assert await asyncio.to_thread(locked.wait, 2)
            probe = loop.run_in_executor(None, probe_during_stop)
            stop_task = asyncio.create_task(stop())
            await asyncio.sleep(0)
            assert not owned.owner.allow_new_orders
            assert owned.owner.connection is not None
            rest, transport = assistant()
            with pytest.raises(SubmissionBlocked):
                await send(rest)
            transport.call.assert_not_awaited()
            owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill())
            assert (await probe)[0]
            assert not core.shutdown.await_count
            if outcome != 'deadline':
                assert not stop_task.done()
                release.set()
                await holder
                assert await stop_task is (outcome == 'drained')
            else:
                assert not await stop_task
                assert not holder.done()
                assert owned.owner.connection is not None
        finally:
            release.set()
            await holder
            if 'stop_task' in locals():
                await asyncio.gather(stop_task, return_exceptions=True)
            await runner.cleanup()

    owned.loop.run_until_complete(exercise())
    assert core.shutdown.await_count == int(outcome == 'drained')
    recovery = owned.store.read()['recovery_state']
    assert not recovery.get('shutdown_incomplete')
    with owned.manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1


def test_shutdown_does_not_retry_a_lock_error_reported_at_commit(owned):
    core = reconciliation_core(owned)
    core.shutdown = AsyncMock(return_value=True)
    with owned.engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION fail_shutdown_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'deferred lock error' USING ERRCODE = '55P03';
            END
            $$
        """))
        connection.execute(text("""
            CREATE CONSTRAINT TRIGGER fail_shutdown_commit
            AFTER UPDATE ON bot_state DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (NEW.recovery_state->>'shutdown_incomplete' = 'true')
            EXECUTE FUNCTION fail_shutdown_commit()
        """))

    with pytest.raises(OperationalError) as failure:
        owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=1))

    assert failure.value.orig.pgcode == '55P03'
    assert not owned.owner.allow_new_orders
    core.shutdown.assert_not_awaited()
    assert not owned.store.read()['recovery_state'].get('shutdown_incomplete')
