"""Foreground lifecycle/probe tests, with owned cleanup surviving cancellation."""
import asyncio
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.worker import WorkerConfig, WorkerSupervisor


def config(**changes):
    return WorkerConfig(config_path='/tmp/synthetic-service.yml', source_factory='trusted:factory',
                        tick_seconds=.1, startup_timeout_s=2, shutdown_timeout_s=.2,
                        status_host='127.0.0.1', status_port=0, telemetry_interval_s=.05, **changes)


@pytest.mark.asyncio
async def test_unarmed_worker_is_live_startup_waits_for_binding_and_status_cannot_arm(monkeypatch):
    entered, ready, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    service = SimpleNamespace(revoke=lambda: finished.set(), runtime=SimpleNamespace(healthy=False), _trading_ready=lambda: False)

    async def launch(**kwargs):
        entered.set()
        await ready.wait()
        await kwargs['on_started'](service)
        await kwargs['stop'].wait()
        assert finished.is_set()
    supervisor = WorkerSupervisor(config(), launch=launch)
    task = asyncio.create_task(supervisor.run())
    await entered.wait()
    port = supervisor.server.sockets[0].getsockname()[1]

    async def get(path):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(('GET ' + path + ' HTTP/1.1\r\nHost: local\r\n\r\n').encode())
        await writer.drain()
        result = await reader.read()
        writer.close()
        await writer.wait_closed()
        return result
    try:
        assert b'200 OK' in await get('/live')
        assert b'503 Service Unavailable' in await get('/startup')
        ready.set()
        for _ in range(100):
            if supervisor.started:
                break
            await asyncio.sleep(.001)
        assert b'200 OK' in await get('/startup')
        assert b'"local_trading_ready":false' in await get('/status')
        assert b'404 Not Found' in await get('/activate')
        assert not supervisor.stop.is_set()
        supervisor.request_stop()
        assert finished.is_set()  # synchronous admission close, before any await
        await task
    finally:
        supervisor.request_stop()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_global_shutdown_budget_retains_original_cleanup_without_certifying_release():
    entered, release, physical_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def launch(**kwargs):
        await kwargs['on_started'](SimpleNamespace(revoke=lambda: None, runtime=SimpleNamespace(healthy=True), _trading_ready=lambda: False))
        entered.set()
        await kwargs['stop'].wait()
        await release.wait()
        physical_done.set()
    supervisor = WorkerSupervisor(config(), launch=launch)
    task = asyncio.create_task(supervisor.run())
    await entered.wait()
    supervisor.request_stop()
    try:
        with pytest.raises(TimeoutError):
            await task
        assert not physical_done.is_set()
        assert not supervisor.service_task.done()
        assert not supervisor.service_task.cancelled()
    finally:
        release.set()
        await supervisor.service_task
    assert physical_done.is_set()


def test_worker_environment_requires_explicit_managed_configuration_and_redacts_secrets():
    with pytest.raises(ValueError):
        WorkerConfig.from_environment({})
    env = dict(LIFE_ENV_FILE='', LIFE_SERVICE_CONFIG='/run/config/service.yml', LIFE_SOURCE_FACTORY='trusted:factory',
               LIFE_TICK_SECONDS='.1', LIFE_STARTUP_TIMEOUT_SECONDS='30', LIFE_SHUTDOWN_TIMEOUT_SECONDS='8',
               LIFE_STATUS_HOST='0.0.0.0', LIFE_STATUS_PORT='8080', LIFE_TELEMETRY_INTERVAL_SECONDS='5')
    assert WorkerConfig.from_environment(env).shutdown_timeout_s == 8
    for changes in (dict(LIFE_SHUTDOWN_TIMEOUT_SECONDS='10'), dict(LIFE_TICK_SECONDS='NaN'), dict(LIFE_ENV_FILE='/tmp/secret'),
                    dict(LIFE_SOURCE_FACTORY='bad factory'), dict(LIFE_STATUS_HOST='external.example.com')):
        with pytest.raises(ValueError) as error:
            WorkerConfig.from_environment(dict(env, **changes))
        assert '/tmp/secret' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected', [('clean', 0), ('blocked', 2), ('failed', 1), ('startup_blocked', 2), ('late_cleanup', 2)])
async def test_actual_foreground_process_signals_redaction_and_physical_deadline(mode, expected):
    import json
    import os
    import signal
    import sys
    from time import monotonic
    env = {key: os.environ[key] for key in ('PATH', 'PYTHONPATH') if key in os.environ}
    env.update(LIFE_AUTO_MIGRATE='false', LIFE_ENV_FILE='', LIFE_SERVICE_CONFIG='/tmp/synthetic-not-read.yml',
               LIFE_SOURCE_FACTORY='trusted:unused', LIFE_TICK_SECONDS='.1', LIFE_STARTUP_TIMEOUT_SECONDS='.4',
               LIFE_SHUTDOWN_TIMEOUT_SECONDS='.3', LIFE_STATUS_HOST='127.0.0.1', LIFE_STATUS_PORT='0',
               LIFE_TELEMETRY_INTERVAL_SECONDS='.05')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.worker_process', mode,
        env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    lines = []
    try:
        if mode in ('clean', 'blocked', 'late_cleanup'):
            async with asyncio.timeout(4):
                while True:
                    line = await process.stdout.readline()
                    assert line, 'worker exited before STARTED'
                    lines.append(line)
                    if json.loads(line)['event'] == 'STARTED':
                        break
            began = monotonic()
            process.send_signal(signal.SIGTERM)
            if mode == 'blocked':
                await asyncio.sleep(.1)
                process.send_signal(signal.SIGTERM)  # Cannot renew the first deadline.
        else:
            began = monotonic()
        stdout, stderr = await asyncio.wait_for(process.communicate(), 4)
        assert process.returncode == expected
        assert monotonic() - began < 2
        output = b''.join(lines) + stdout
        assert b'synthetic-do-not-disclose-credential' not in output + stderr
        events = [json.loads(line)['event'] for line in output.splitlines()]
        if mode in ('clean', 'blocked', 'late_cleanup'):
            assert events.index('FIXTURE_REVOKED') < events.index('FIXTURE_CLEANUP_STARTED')
        assert ('FIXTURE_CLEANUP_DONE' in events) is (mode == 'clean')
        assert ('STOPPED' in events) is (mode == 'clean')
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


def test_unavailable_readiness_does_not_disclose_source_exceptions(capsys):
    def failed():
        raise RuntimeError('synthetic-status-credential')
    supervisor = WorkerSupervisor(config())
    supervisor.service = SimpleNamespace(runtime=SimpleNamespace(healthy=True), _trading_ready=failed)
    state = supervisor.status()
    assert state['reason_code'] == 'STATUS_UNAVAILABLE'
    assert not state['local_trading_ready'] and not state['database_healthy']
    assert 'synthetic-status-credential' not in str(state) + str(capsys.readouterr())


def test_status_exports_only_bounded_local_database_metrics_without_borrowing_a_connection():
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_diagnostics import runtime
    store, _ = runtime()
    store._pools.clear()
    supervisor = WorkerSupervisor(config())
    supervisor.service = SimpleNamespace(runtime=store, _trading_ready=lambda: False)
    state = supervisor.status()
    assert state['database_metrics']['completed_total'] == 0
    assert state['database_metrics']['elapsed_p95_ms'] is None
    assert not state['database_metrics']['trading_permission']
    assert not state['local_trading_ready'] and state['database_healthy']
    assert 'private-test-password' not in str(state)
    state['database_metrics']['outcomes_total']['error'] = 99
    assert supervisor.status()['database_metrics']['outcomes_total']['error'] == 0
