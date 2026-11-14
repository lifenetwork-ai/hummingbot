"""Managed startup streams real PostgreSQL history on a worker-owned session."""
import asyncio
import threading
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_performance import seed_executors
from test.integration.postgres.test_recorder import recording as _recording
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text

from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from scripts.v2_with_controllers import V2WithControllers

recording = _recording
owned = _owned


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('runtime', ['recording', 'owned'])
def test_history_worker_closes_stream_and_preserves_native_totals(request, pg_engine, cancel, runtime):
    context = request.getfixturevalue(runtime)
    recorder = context[0] if runtime == 'recording' else context.recorder
    checkedout = pg_engine.pool.checkedout()
    physical = None if runtime == 'recording' else context.owner.connection
    if physical is not None:
        context.owner.check_owner()
    seed_executors(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE executors SET controller_id = 'selected-' || (timestamp::int % 2) WHERE timestamp <= 2000"))
    config = SimpleNamespace(mm_drawdown_scope={'selected-0': object(), 'selected-1': object()})
    reference = ExecutorOrchestrator(SimpleNamespace(config=config, controllers={}, markets={}))
    entered, release = threading.Event(), threading.Event()
    history_threads, completed_waits, constructed = [], [], []
    main = threading.get_ident()

    def fetching(connection, cursor, statement, *args):
        if 'FROM executors' in statement:
            assert 'executors.config' not in statement and 'executors.custom_info' not in statement
            history_threads.append(threading.get_ident())
            entered.set()
            completed_waits.append(release.wait(1))

    class Strategy:
        def __init__(self, connectors, config, *, startup_performance):
            constructed.append(threading.get_ident())
            self.config, self.controllers, self.markets = config, {}, {}
            self.executor_orchestrator = ExecutorOrchestrator(self, startup_performance=startup_performance)

    async def run():
        task = asyncio.create_task(V2WithControllers.create_with_async_history.__func__(Strategy, {}, config))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            if cancel:
                task.cancel()
                await asyncio.sleep(.01)
                task.cancel()
                await asyncio.sleep(.01)
                assert not task.done() and pg_engine.pool.checkedout() == checkedout + 1
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.executor_orchestrator.cached_performance == reference.cached_performance
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    event.listen(pg_engine, 'after_cursor_execute', fetching)
    try:
        asyncio.run(run())
    finally:
        event.remove(pg_engine, 'after_cursor_execute', fetching)
    assert completed_waits == [True]
    assert len(history_threads) == 1 and history_threads[0] != main
    assert constructed == ([] if cancel else [main])
    assert pg_engine.pool.checkedout() == checkedout
    assert recorder.sql_manager.persistence_healthy
    if physical is not None:
        assert context.owner.connection is physical
        context.owner.check_owner()
