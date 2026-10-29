"""Shipping role separation must retain startup, pool and shutdown contracts."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hummingbot.client.config.runtime_config import RuntimeSettings


@pytest.mark.parametrize('port,private', [(8080, 8081), (8090, 8091), (65535, 65534)])
@pytest.mark.parametrize('total', [5, 12, 50])
@pytest.mark.parametrize('migrate', [False, True])
def test_roles_share_configured_pool_ceiling_and_do_not_expose_engine(port, private, total, migrate):
    from hummingbot.cli.service_processes import ProcessLayout
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       port=port, database_pool_size=total, enable_auto_migration=migrate)
    layout = ProcessLayout(settings)
    assert layout.api.port == port and layout.engine.port == private
    assert layout.api.database_pool_size == 2
    assert layout.engine.database_pool_size == total - 2
    assert layout.engine.database_pool_size + layout.api.database_pool_size == total
    assert layout.engine.enable_auto_migration is migrate and layout.api.enable_auto_migration is False


def test_api_child_has_no_connector_credentials_or_dotenv_reload_path():
    from hummingbot.cli.service_processes import child_environment
    source = dict(DATABASE_URL='postgresql://operator:database-secret@db/test',
                  ADMIN_API_PASSWORD='admin-secret', HBOT_OKX_DEMO_API_KEY='exchange-secret',
                  HBOT_BINANCE_API_KEY='another-exchange-secret', HBOT_PASSWORD='wallet-secret',
                  ENV_FILE='/private/env', HBOT_ENV_FILE='/other/private/env',
                  HBOT_OFFLINE_HTTP_ORIGIN='http://exchange:8080', OKX_TRADING_ENV='demo')
    api = child_environment('api', source)
    assert api['DATABASE_URL'] == source['DATABASE_URL'] and api['ADMIN_API_PASSWORD'] == 'admin-secret'
    assert api['HBOT_OFFLINE_HTTP_ORIGIN'] == 'http://exchange:8080'
    assert not {'ENV_FILE', 'HBOT_ENV_FILE', 'HBOT_OKX_DEMO_API_KEY', 'HBOT_BINANCE_API_KEY', 'HBOT_PASSWORD'} & api.keys()
    assert source['HBOT_OKX_DEMO_API_KEY'] == 'exchange-secret'
    engine = child_environment('engine', source)
    assert engine['HBOT_OKX_DEMO_API_KEY'] == 'exchange-secret' and engine['ADMIN_API_ENABLED'] == 'false'
    assert 'ENV_FILE' not in engine and 'HBOT_ENV_FILE' not in engine
    with pytest.raises(ValueError):
        child_environment('unknown', source)


def test_default_entrypoint_supervises_and_child_roles_do_not_reload_dotenv(monkeypatch):
    from hummingbot.cli import service
    calls = []
    monkeypatch.setattr('hummingbot.client.config.runtime_config.load_environment', lambda: calls.append('dotenv'))
    monkeypatch.setattr(service, 'serve', lambda: calls.append('supervisor'))
    monkeypatch.setattr(service, 'serve_role', lambda role: calls.append(role))
    service.main([])
    service.main(['--role', 'api'])
    service.main(['--role', 'engine'])
    assert calls == ['dotenv', 'supervisor', 'api', 'engine']


def test_probe_uses_startup_until_bootstrapped_then_independent_liveness():
    from hummingbot.cli.service_processes import probe_path
    child = object()
    owner = SimpleNamespace(children={'api': child})
    watchdog = SimpleNamespace(supervisor=owner, observations={})
    assert probe_path(watchdog, 'api') == '/startup'
    watchdog.observations['api'] = dict(child=child, healthy_once=True)
    assert probe_path(watchdog, 'api') == '/live'
    owner.children['api'] = object()
    assert probe_path(watchdog, 'api') == '/startup'


@pytest.mark.asyncio
async def test_api_liveness_does_not_depend_on_engine_or_database():
    import asyncio

    from hummingbot.cli.admin_service import AdminService
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'})
    host = AdminService(settings, engine_port=8081, stop=asyncio.Event())
    assert (await host.health(SimpleNamespace(path='/live'))).status == 200
    assert (await host.health(SimpleNamespace(path='/startup'))).status == 503
    assert (await host.health(SimpleNamespace(path='/ready'))).status == 503
    host.stop.set()
    assert (await host.health(SimpleNamespace(path='/live'))).status == 503


@pytest.mark.asyncio
async def test_api_startup_and_trading_readiness_proxy_engine_independently():
    import asyncio
    from unittest.mock import AsyncMock

    from hummingbot.cli.admin_service import AdminService
    host = AdminService(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                        engine_port=8081, stop=asyncio.Event())
    host.bootstrapped = True
    host.engine_health = AsyncMock(side_effect=[(200, {'state': 'halted'}), (503, {'state': 'halted'})])
    assert (await host.health(SimpleNamespace(path='/startup'))).status == 200
    assert (await host.health(SimpleNamespace(path='/ready'))).status == 503
    assert (await host.health(SimpleNamespace(path='/live'))).status == 200
    assert host.service_status()['persisted_state_available'] is False


def test_fatal_bootstrap_and_transient_database_failure_are_distinct():
    from sqlalchemy.exc import OperationalError

    from hummingbot.cli.service_processes import failure_exit_code
    from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError
    assert failure_exit_code(PostgreSQLBootstrapError('schema drift')) == 78
    assert failure_exit_code(PostgreSQLBootstrapError('read temporarily unavailable', retryable=True)) == 1
    assert failure_exit_code(OperationalError('hidden', {}, ConnectionError())) == 1


def test_parent_stop_during_popen_never_launches_api(monkeypatch):
    import signal
    from test.hummingbot.cli.test_process_supervision import Child

    from hummingbot.cli import service_processes
    launched = []
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused/test')
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    monkeypatch.setenv('ADMIN_API_ENABLED', 'false')
    monkeypatch.setattr('hummingbot.logger.service_logging.configure_service_logging', lambda *a, **k: None)

    def launch(command, *, env):
        role = command[-1]
        child = Child(role)
        launched.append(child)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return child

    monkeypatch.setattr(service_processes.subprocess, 'Popen', launch)
    assert service_processes.run_supervisor() == 0
    assert [child.role for child in launched] == ['engine']
    assert launched[0].waited


@pytest.mark.parametrize('sqlstate,retryable', [('08006', True), ('53300', True), ('57014', True),
                                                ('42501', False), ('42P01', False), ('28P01', False)])
def test_bootstrap_wrap_preserves_retry_classification_without_driver_text(sqlstate, retryable):
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError, bootstrap_postgres
    original = ConnectionError('private driver credential text')
    original.pgcode = sqlstate
    engine = MagicMock()
    engine.connect.side_effect = OperationalError('hidden query', {}, original)
    with pytest.raises(PostgreSQLBootstrapError) as raised:
        bootstrap_postgres(engine, auto_migrate=False)
    assert raised.value.retryable is retryable
    assert 'private driver' not in str(raised.value)


@pytest.mark.asyncio
async def test_api_initialization_stop_never_connects_or_acquires_ownership(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from hummingbot.cli.admin_service import AdminService
    monkeypatch.setenv('ADMIN_API_ENABLED', 'false')
    connect = MagicMock(side_effect=AssertionError('API must not initialize DB after stop'))
    monkeypatch.setattr('hummingbot.model.db_migration.postgres.connection.create_postgres_engine', connect)
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}), port=0)
    host = AdminService(settings, engine_port=8081, stop=asyncio.Event())
    host.engine_health = AsyncMock(return_value=(503, {}))
    task = asyncio.create_task(host.run())
    try:
        async with asyncio.timeout(5):
            while not host.engine_health.await_count:
                await asyncio.sleep(.01)
        host.stop.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        host.stop.set()
        await asyncio.gather(task, return_exceptions=True)
    assert host.closed and host.store is None
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_api_bootstrap_only_validates_and_never_ensures_bot_state(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from hummingbot.cli.admin_service import AdminService
    monkeypatch.setenv('ADMIN_API_ENABLED', 'false')
    engine, ensure = MagicMock(), MagicMock()
    monkeypatch.setattr('hummingbot.model.db_migration.postgres.connection.create_postgres_engine', lambda settings: engine)
    monkeypatch.setattr('hummingbot.model.bot_state_store.BotStateStore.ensure', ensure)
    stop, loop = asyncio.Event(), asyncio.get_running_loop()

    def validate(value, *, auto_migrate):
        assert value is engine and auto_migrate is False
        loop.call_soon_threadsafe(stop.set)

    monkeypatch.setattr('hummingbot.model.db_migration.postgres.runner.bootstrap_postgres', validate)
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}), port=0)
    host = AdminService(settings, engine_port=8081, stop=stop)
    host.engine_health = AsyncMock(return_value=(200, {}))
    await asyncio.wait_for(host.run(), timeout=5)
    assert host.closed and host.store is not None
    ensure.assert_not_called()
    engine.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_failed_http_cleanup_cannot_be_marked_clean(monkeypatch):
    import asyncio

    from hummingbot.cli import admin_service
    monkeypatch.setenv('ADMIN_API_ENABLED', 'false')

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.close()

        async def close(self):
            raise OSError('HTTP cleanup failed')

    monkeypatch.setattr(admin_service, 'ClientSession', lambda **kwargs: Client())
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}), port=0)
    stop = asyncio.Event()
    stop.set()
    host = admin_service.AdminService(settings, engine_port=8081, stop=stop)
    with pytest.raises(OSError, match='HTTP cleanup failed'):
        await host.run()
    assert host.closed is False
