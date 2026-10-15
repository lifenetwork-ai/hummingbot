"""Explicit file-recorder assembly; no SQLite initialization on the event loop."""
import math
from pathlib import Path

from hummingbot.client.config.client_config_map import ClientConfigMap, DBSqliteMode, MarketDataCollectionConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.recorder import FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_application import FileRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
from hummingbot.strategy_v2.life_liquidity.storage.runtime import FileControllerRuntime


def _open_sql(store, database_path):
    store.assert_worker_thread()
    path = Path(database_path)
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise StorageError('RECORDER_DATABASE_PATH_INVALID')
    if 'recorder_sql' in store._worker_finalizers:
        raise StorageError('RECORDER_SQL_ALREADY_INSTALLED')
    # This factory supports explicit SQLite only, never db_mode/environment
    # fallback to a credential-bearing or unsupported PostgreSQL connection.
    client = ClientConfigAdapter(ClientConfigMap(db_mode=DBSqliteMode()))
    sql = SQLConnectionManager(client, SQLConnectionType.TRADE_FILLS, db_path=str(path))
    store._register_worker_finalizer('recorder_sql', sql.engine.dispose)
    return sql


async def open_file_recorder(runtime, *, database_path, config_file_path, strategy_name, connectors,
                             max_pending, shutdown_timeout_s):
    """Assemble reporting before runner construction; never issue an activation.

    Call reporting.close(), then store.close() on shutdown. The owning store
    drains admitted work and disposes this SQL engine before account handoff,
    including when bootstrap/close awaiters are cancelled or writes are unknown.
    Connector tracking states remain diagnostics requiring qualified recovery.
    """
    if (not isinstance(runtime, FileControllerRuntime) or type(database_path) is not str
            or runtime._recorder_application is not None or runtime._recorder_startup_pending
            or type(max_pending) is not int or not 0 < max_pending <= runtime.store.limits.max_pending
            or type(shutdown_timeout_s) not in (int, float) or not math.isfinite(shutdown_timeout_s)
            or shutdown_timeout_s <= 0):
        raise StorageError('RECORDER_FACTORY_BINDING_INVALID')
    connectors = tuple(connectors)  # Detach a one-shot iterable before the first await.
    for connector in connectors:
        EventSource(runtime.controller.config.id, config_file_path, strategy_name, connector.name)
    runtime._recorder_startup_pending = True
    reporting = None
    try:
        sql = await runtime.store._call(_open_sql, runtime.store, database_path)
        recorder = MarketsRecorder(sql, list(connectors), config_file_path, strategy_name,
                                   MarketDataCollectionConfigMap(market_data_collection_enabled=False),
                                   defer_history_restore=True)
        repository = await runtime.store._call(FileRecorderRepository, runtime.store, recorder, runtime.controller.config.id)
        if runtime._swap_application is not None:
            await runtime.store._call(repository.bind_swap_repository, runtime._swap_application.repository)
        reporting = await FileRecorderApplication.install(repository, runtime, max_pending=max_pending,
                                                          shutdown_timeout_s=shutdown_timeout_s)
        await reporting.install_event_sources(connectors)
        runtime._assert_config()
        if runtime.store._closed or runtime._revoked or runtime.controller._order_safety_stopped:
            raise StorageError('RECORDER_STARTUP_REVOKED')
        return reporting
    except BaseException:
        runtime.revoke()
        if reporting is not None:
            await reporting.close()
        raise
    finally:
        runtime._recorder_startup_pending = False
