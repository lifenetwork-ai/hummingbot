"""Engine-only opt-in lifecycle drains before removing its SQL hooks."""
import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import create_engine

from hummingbot.cli import service
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, RuntimeSettings
from hummingbot.core.engine_diagnostics import EngineDiagnostics


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
