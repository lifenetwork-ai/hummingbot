"""Engine-only opt-in lifecycle drains before removing its SQL hooks."""
import asyncio
import threading
from dataclasses import replace
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web
from sqlalchemy import create_engine

from hummingbot.cli import service
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, RuntimeSettings
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.sql_connection_manager import SQLConnectionManager


@pytest.mark.parametrize('value,enabled', [(None, False), ('true', True), ('false', False), (' TRUE ', True)])
def test_diagnostics_requires_explicit_valid_boolean(value, enabled):
    env = {'DATABASE_URL': 'postgresql://unused/test'}
    if value is not None:
        env['ENGINE_DIAGNOSTICS_ENABLED'] = value
    assert RuntimeSettings.from_environment(env).engine_diagnostics_enabled is enabled
    env['ENGINE_DIAGNOSTICS_ENABLED'] = 'private-invalid-flag'
    with pytest.raises(RuntimeConfigurationError) as caught:
        RuntimeSettings.from_environment(env)
    assert 'private-invalid-flag' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('failure', [False, True])
async def test_service_lifecycle_owns_trace_task_thread_and_hooks(monkeypatch, unused_tcp_port, enabled, failure):
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    # Logging has separate integration coverage; do not replace pytest/global
    # handlers, warning capture or legacy NETWORK severity in a lifecycle test.
    monkeypatch.setattr('hummingbot.logger.service_logging.configure_service_logging', lambda *a, **kw: None)
    made = []

    def tracer():
        item = EngineDiagnostics(emit=lambda _: None)
        made.append(item)
        return item

    async def bootstrap(host):
        host.engine = create_engine('sqlite://')
        host.bootstrapped = True

    async def observe(host):
        if enabled:
            assert host.runtime_diagnostics is made[0]
            assert made[0].hooks and made[0].worker.is_alive()
        if failure:
            raise RuntimeError('private-engine-input')
        host.stop.set()

    monkeypatch.setattr(service, 'EngineDiagnostics', tracer)
    monkeypatch.setattr(service.ServiceHost, 'bootstrap', bootstrap)
    monkeypatch.setattr(service.ServiceHost, 'observe_once', observe)
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       engine_diagnostics_enabled=enabled, port=unused_tcp_port)
    code = await service.run_service(settings=settings, bind_host='127.0.0.1')
    assert code == int(failure)
    assert len(made) == int(enabled)
    for item in made:
        assert item._closed and not item.hooks
        assert item.heartbeat_task.done() and not item.worker.is_alive()
    assert not any(t.get_name() == 'engine-diagnostic-heartbeat' for t in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_diagnostic_constructor_failure_does_not_stop_an_otherwise_healthy_engine(monkeypatch, unused_tcp_port):
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    monkeypatch.setattr('hummingbot.logger.service_logging.configure_service_logging', lambda *a, **kw: None)

    def unavailable():
        raise MemoryError('private-telemetry-allocation')

    async def bootstrap(host):
        host.engine = create_engine('sqlite://')

    async def observe(host):
        assert host.runtime_diagnostics is None
        host.stop.set()

    monkeypatch.setattr(service, 'EngineDiagnostics', unavailable)
    monkeypatch.setattr(service.ServiceHost, 'bootstrap', bootstrap)
    monkeypatch.setattr(service.ServiceHost, 'observe_once', observe)
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       engine_diagnostics_enabled=True, port=unused_tcp_port)
    assert await service.run_service(settings=settings, bind_host='127.0.0.1') == 0


@pytest.mark.asyncio
async def test_engine_prestarts_workers_before_first_concurrent_io(monkeypatch, unused_tcp_port):
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    monkeypatch.setattr('hummingbot.logger.service_logging.configure_service_logging', lambda *a, **kw: None)
    loop_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    starts = []
    original_start = threading.Thread.start
    bind = web.TCPSite.start
    release = threading.Event()
    release_start = threading.Event()
    starting, listening = asyncio.Event(), asyncio.Event()

    def record_start(thread):
        if thread.name.startswith('asyncio_'):
            starts.append(threading.get_ident())
            if thread.name == 'asyncio_1':
                loop.call_soon_threadsafe(starting.set)
                assert release_start.wait(5)
        return original_start(thread)

    async def bind_listener(site):
        await bind(site)
        listening.set()

    async def bootstrap(host):
        host.bootstrapped = True

    async def observe(host):
        prepared = len(starts)
        jobs = [asyncio.create_task(asyncio.to_thread(release.wait, 2)) for _ in range(4)]
        try:
            await asyncio.sleep(0)
            assert len(starts) == prepared and prepared >= 4
            assert starts.count(loop_thread) == 1
        finally:
            release.set()
            await asyncio.gather(*jobs)
        host.stop.set()

    monkeypatch.setattr(threading.Thread, 'start', record_start)
    monkeypatch.setattr(web.TCPSite, 'start', bind_listener)
    monkeypatch.setattr(service.ServiceHost, 'bootstrap', bootstrap)
    monkeypatch.setattr(service.ServiceHost, 'observe_once', observe)
    settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       port=unused_tcp_port)
    running = asyncio.create_task(service.run_service(settings=settings, bind_host='127.0.0.1'))
    try:
        async with asyncio.timeout(3):
            await asyncio.gather(starting.wait(), listening.wait())
            async with ClientSession() as http:
                async with http.get(f'http://127.0.0.1:{unused_tcp_port}/live') as response:
                    assert response.status == 200
                    assert not release_start.is_set()
    finally:
        release_start.set()
        code = await asyncio.wait_for(running, 3)
    assert code == 0


@pytest.mark.asyncio
async def test_schema_bootstrap_yields_to_loop_and_drains_before_cancelled_shutdown(monkeypatch):
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'})
    host = service.ServiceHost(settings, asyncio.Event(), None)
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    engine = Mock()
    calls = []

    def initialize(config, *, allow_migration_standby):
        calls.append(threading.get_ident())
        assert config is settings and allow_migration_standby
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return engine

    ensure = Mock()
    monkeypatch.setattr(SQLConnectionManager, 'initialize_postgres', initialize)
    monkeypatch.setattr(BotStateStore, 'ensure', ensure)
    monkeypatch.setattr(BotStateStore, 'read', lambda self: None)
    startup = asyncio.create_task(host.bootstrap())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert calls and calls[0] != loop_thread
        startup.cancel()
        await asyncio.sleep(0)
        assert not startup.done()
        assert host.engine is None and not host.bootstrapped
    finally:
        release.set()
        result, = await asyncio.gather(startup, return_exceptions=True)
    assert isinstance(result, asyncio.CancelledError)
    assert host.engine is engine and not host.bootstrapped
    ensure.assert_called_once()
