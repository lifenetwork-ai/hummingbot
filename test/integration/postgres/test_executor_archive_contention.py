import asyncio
import threading
from test.integration.postgres.test_automatic_recovery import executor_info, orchestrator
from test.integration.postgres.test_ownership import owned as owned_fixture
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from sqlalchemy import event, text

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType

owned = owned_fixture


@pytest.mark.parametrize('locked_seconds', [.45, 1.25])
def test_executor_archive_defers_contended_fence_without_publishing(owned, locked_seconds):
    runtime = orchestrator()
    info = executor_info('probe-executor')
    info.close_type = CloseType.EARLY_STOP
    executor = SimpleNamespace(config=info.config, executor_info=info, is_active=False)
    runtime.active_executors['maker'] = [executor]
    action = StoreExecutorAction(controller_id='maker', executor_id=info.id)
    locked, fence = threading.Event(), threading.Event()
    traces, fences = [], []

    def lock_row():
        with owned.engine.begin() as connection:
            connection.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            locked.set()
            assert fence.wait(2)
            threading.Event().wait(locked_seconds)

    def detect_fence(connection, cursor, statement, parameters, context, executemany):
        if 'FROM bot_state' in statement and 'FOR SHARE' in statement:
            fences.append(statement)
            fence.set()

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        server = web.AppRunner(app)
        await server.setup()
        await web.TCPSite(server, '127.0.0.1', 0).start()
        port = server.addresses[0][1]
        loop = asyncio.get_running_loop()
        diagnostics = EngineDiagnostics(emit=traces.append)
        await diagnostics.start()
        diagnostics.attach_engine(owned.engine)

        def probe():
            assert fence.wait(2)
            return probe_health(port, '/live')

        blocker = loop.run_in_executor(None, lock_row)
        try:
            assert await asyncio.to_thread(locked.wait, 2)
            checking = loop.run_in_executor(None, probe)
            with diagnostics.span('strategy.tick'):
                runtime.execute_action(action)
            first = await checking
            assert first[0], first[1]
            print(f"Archive probe under a {locked_seconds}s ownership lock: {first[1]}")
            assert runtime.active_executors['maker'] == [executor]
            assert owned.manager.persistence_healthy
            with owned.engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM executors WHERE id='probe-executor'")) == 0
            await blocker
            # The production clock retries the retained action on the next tick.
            await asyncio.sleep(.15)
            with diagnostics.span('strategy.tick'):
                runtime.execute_action(action)
            assert (await asyncio.to_thread(probe_health, port, '/live'))[0]
            assert diagnostics.snapshot()['operations']['executor.archive']['completed'] == 2
        finally:
            await blocker
            await diagnostics.close()
            await server.cleanup()

    event.listen(owned.engine, 'before_cursor_execute', detect_fence)
    try:
        with patch.object(runtime, '_update_cached_performance', wraps=runtime._update_cached_performance) as update:
            owned.loop.run_until_complete(exercise())
            update.assert_called_once_with('maker', info)
    finally:
        event.remove(owned.engine, 'before_cursor_execute', detect_fence)
    assert all('NOWAIT' in statement for statement in fences)
    assert not runtime.active_executors['maker']
    with owned.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM executors WHERE id='probe-executor'")) == 1


def test_executor_archive_commit_failure_retains_executor_and_latches_persistence(owned):
    runtime = orchestrator()
    info = executor_info('rejected-archive')
    info.close_type = CloseType.EARLY_STOP
    executor = SimpleNamespace(config=info.config, executor_info=info, is_active=False)
    runtime.active_executors['maker'] = [executor]
    with owned.engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION reject_archive_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'deferred lock failure' USING ERRCODE = '55P03';
            END $$;
            CREATE CONSTRAINT TRIGGER reject_archive_commit
                AFTER INSERT ON executors DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION reject_archive_commit();
        """))
    action = StoreExecutorAction(controller_id='maker', executor_id=info.id)
    with patch.object(runtime, '_update_cached_performance') as update:
        runtime.execute_action(action)
        update.assert_not_called()
    assert runtime.active_executors['maker'] == [executor]
    assert not owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM executors WHERE id='rejected-archive'")) == 0
