"""Recorder bootstrap/cleanup use the same physically owned file worker."""
import asyncio
import threading
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_application import setup

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.recorder_factory import open_file_recorder


@pytest.mark.asyncio
async def test_real_sql_bootstrap_runs_on_worker_and_deferred_history_never_calls_legacy_reads(tmp_path, monkeypatch):
    app, store, _, _, _, _ = await setup(tmp_path)
    main = threading.get_ident()
    constructed = []
    original = SQLConnectionManager.__init__

    def create(manager, *args, **kwargs):
        assert threading.get_ident() != main
        constructed.append(threading.get_ident())
        original(manager, *args, **kwargs)
    monkeypatch.setattr(SQLConnectionManager, '__init__', create)
    monkeypatch.setattr(MarketsRecorder, 'get_trades_for_config', lambda *args: (_ for _ in ()).throw(AssertionError('legacy SQL')))
    monkeypatch.setattr(MarketsRecorder, 'get_orders_for_config_and_market', lambda *args: (_ for _ in ()).throw(AssertionError('legacy SQL')))
    reporting = None
    try:
        reporting = await open_file_recorder(app.runtime, database_path=str(tmp_path / 'recorder.sqlite'),
                                             config_file_path='life-test', strategy_name='v2',
                                             connectors=iter((app.connector,)), max_pending=2, shutdown_timeout_s=.5)
        assert len(constructed) == 1 and reporting.ready
        assert reporting.repository._recorder._repository_event_sink is not None
        with pytest.raises(ValueError, match='RECORDER_QUALIFIED_TRACKER_RECOVERY_REQUIRED'):
            reporting.repository._recorder.restore_market_states('life-test', app.connector)
        assert await reporting.repository.read(app.binding) == ()
        assert store._worker_finalizers.keys() == {'recorder_sql'}
    finally:
        if reporting is not None:
            await reporting.close()
        await app.close()
        await store.close()
    assert store._worker_finalizers == {}


@pytest.mark.asyncio
async def test_cancelled_sql_bootstrap_is_physically_drained_and_disposed_before_account_handoff(tmp_path, monkeypatch):
    app, store, _, _, _, _ = await setup(tmp_path)
    entered, release, disposed = threading.Event(), threading.Event(), threading.Event()
    original = SQLConnectionManager.__init__

    def blocked(manager, *args, **kwargs):
        original(manager, *args, **kwargs)
        original_dispose = manager.engine.dispose

        def dispose(*args, **kwargs):
            store.assert_worker_thread()
            disposed.set()
            return original_dispose(*args, **kwargs)
        manager.engine.dispose = dispose
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(SQLConnectionManager, '__init__', blocked)
    task = asyncio.create_task(open_file_recorder(app.runtime, database_path=str(tmp_path / 'recorder.sqlite'),
                                                  config_file_path='life-test', strategy_name='v2',
                                                  connectors=(app.connector,), max_pending=2, shutdown_timeout_s=.5))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not disposed.is_set() and not closing.done()
        release.set()
        await closing
        assert disposed.is_set() and store._worker_finalizers == {}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_relative_database_path_is_rejected_before_native_sql_creation(tmp_path, monkeypatch):
    app, store, _, _, _, _ = await setup(tmp_path)
    monkeypatch.setattr(SQLConnectionManager, '__init__', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unexpected SQL')))
    try:
        with pytest.raises(StorageError, match='RECORDER_DATABASE_PATH_INVALID'):
            await open_file_recorder(app.runtime, database_path='relative.sqlite', config_file_path='life-test', strategy_name='v2',
                                     connectors=(app.connector,), max_pending=2, shutdown_timeout_s=.5)
    finally:
        await app.close()
        await store.close()
