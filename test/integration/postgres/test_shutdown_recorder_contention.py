"""Stop and native fill processing while a recorder transaction holds ownership."""
import asyncio
import threading
from test.integration.postgres.test_order_recorder_liveness import tracked_fill as tracked_fill_fixture
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture, reconciliation_core, send
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.trade_fill import TradeFill

owned = owned_fixture
tracked_fill = tracked_fill_fixture


@pytest.mark.parametrize('outcome', ['drained', 'deadline', 'superseded'])
def test_stop_stays_responsive_while_recorder_holds_owner_row(owned, tracked_fill, outcome):
    tracker, order, trade = tracked_fill
    core = reconciliation_core(owned)

    async def shutdown(**kwargs):
        assert not owned.owner.allow_new_orders
        assert owned.store.read()['recovery_state']['shutdown_incomplete']
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)

    async def exercise():
        loop = asyncio.get_running_loop()
        shutdown_blocked = asyncio.Event()
        fill_started, release_fill = threading.Event(), threading.Event()
        app = web.Application()
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]

        def observe_shutdown_lock(error):
            context = error.execution_context
            if (context is not None and context.execution_options.get('ownership_row_writer') == 'owner_recovery'
                    and getattr(error.original_exception, 'pgcode', None) == '55P03'):
                loop.call_soon_threadsafe(shutdown_blocked.set)

        def pause_fill(connection, cursor, statement, parameters, context, executemany):
            if context.isinsert and context.compiled.statement.table.name == TradeFill.__tablename__:
                fill_started.set()
                assert release_fill.wait(3), 'Probe did not finish while the fill write was paused.'

        def probe_during_fill():
            assert fill_started.wait(2), 'Native fill did not reach the recorder write.'
            return probe_health(port, '/live')

        tasks = []
        event.listen(owned.engine, 'handle_error', observe_shutdown_lock)
        event.listen(owned.engine, 'before_cursor_execute', pause_fill)
        try:
            with owned.engine.begin() as blocker:
                params = {'bot': owned.store.bot_id}
                blocker.execute(text('SELECT bot_id FROM bot_state WHERE bot_id = :bot FOR SHARE'), params)
                stop_task = asyncio.create_task(owned.owner.shutdown_core(core, timeout=.6 if outcome == 'deadline' else 4))
                tasks.append(stop_task)
                await asyncio.wait_for(shutdown_blocked.wait(), 3)
                assert not owned.owner.allow_new_orders
                assert owned.owner.connection is not None
                rest, transport = assistant()
                with pytest.raises(SubmissionBlocked):
                    await send(rest)
                transport.call.assert_not_awaited()

                probe = loop.run_in_executor(None, probe_during_fill)
                fill_task = asyncio.create_task(tracker.apply_trade_update(trade))
                tasks.extend((probe, fill_task))
                healthy, detail = await probe
                assert healthy, detail
                assert not fill_task.done()
                assert order.executed_amount_base == 0
                core.shutdown.assert_not_awaited()
                release_fill.set()
                await fill_task
                assert order.executed_amount_base == trade.fill_base_amount

                if outcome == 'deadline':
                    assert not await stop_task
                    assert owned.owner.connection is not None
                else:
                    assert not stop_task.done()
                    if outcome == 'superseded':
                        blocker.execute(text('UPDATE bot_state SET control_version = control_version + 1 WHERE bot_id = :bot'),
                                        params)
            assert await stop_task is (outcome == 'drained')
        finally:
            release_fill.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            event.remove(owned.engine, 'before_cursor_execute', pause_fill)
            event.remove(owned.engine, 'handle_error', observe_shutdown_lock)
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
