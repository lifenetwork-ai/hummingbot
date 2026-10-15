"""Process/Clock lifecycle with explicit offline adapters and no network/DB."""
import asyncio
import json
import os
import signal
import sys
from test.hummingbot.strategy_v2.life_liquidity.storage.headless_fixture import build
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import settings
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.headless import launch

FACTORY = 'test.hummingbot.strategy_v2.life_liquidity.storage.headless_fixture:build'


@pytest.mark.asyncio
async def test_headless_sighup_reloads_original_path_and_rejects_invalid_yaml_without_exit_or_activation(tmp_path):
    config, path = await config_file(tmp_path)
    command = [sys.executable, '-m', 'hummingbot.strategy_v2.life_liquidity.storage.headless', '--config', str(path),
               '--factory', FACTORY, '--tick-seconds', '.01', '--startup-timeout', '5', '--shutdown-timeout', '2',
               '--no-env-file', '--create-offline-binding']
    child = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                 env={**os.environ, 'LIFE_AUTO_MIGRATE': 'false'})
    try:
        async with asyncio.timeout(10):
            assert (await child.stdout.readline()).strip() == b'LIFE_SERVICE_STARTED_ACTIVATION_NOT_GRANTED'
            for body, reason in (('bad: [synthetic-private-data', 'CONFIG_LOAD_FAILED'),
                                 (config.model_dump_json(), 'CONFIG_UNCHANGED')):
                path.write_text(body)
                child.send_signal(signal.SIGHUP)
                line = await child.stdout.readline()
                assert json.loads(line) == {'configuration': reason, 'activation_granted': False}
                assert child.returncode is None
            child.send_signal(signal.SIGTERM)
            _, errors = await child.communicate()
            assert child.returncode == 0, errors.decode()
            assert b'synthetic-private-data' not in errors
    finally:
        if child.returncode is None:
            child.kill()
            await child.communicate()


async def config_file(tmp_path):
    config = await settings(tmp_path)
    config = config.model_copy(update={'controller': config.controller.model_copy(update={'safety_watchdog_interval_ms': 20})})
    path = tmp_path / 'service.yml'
    path.write_text(config.model_dump_json())
    return config, path


def migration_off():
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import load_config
    return load_config(environ={'LIFE_AUTO_MIGRATE': 'false'}, no_env_file=True)


@pytest.mark.asyncio
async def test_headless_awaits_migration_before_factory_import_and_never_constructs_after_failure(tmp_path):
    _, path = await config_file(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def migrate(_):
        entered.set()
        await release.wait()
        raise RuntimeError('synthetic migration failure')

    with patch('hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations.auto_migrate', migrate), \
            patch('hummingbot.strategy_v2.life_liquidity.storage.headless.load_factory') as factory:
        task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=asyncio.Event(),
                                          tick_seconds=.01, startup_timeout_s=2, shutdown_timeout_s=2,
                                          migration_config=migration_off(), create_offline_binding=True))
        await asyncio.wait_for(entered.wait(), 1)
        factory.assert_not_called()
        release.set()
        with pytest.raises(RuntimeError, match='synthetic migration failure'):
            await task
        factory.assert_not_called()


@pytest.mark.asyncio
async def test_actual_headless_clock_stays_disabled_when_disconnected_and_joins_on_stop(tmp_path):
    _, path = await config_file(tmp_path)
    stop, started = asyncio.Event(), asyncio.Event()
    services = []

    def capture(service):
        services.append(service)
        started.set()

    task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=stop, tick_seconds=.01,
                                      startup_timeout_s=3, shutdown_timeout_s=2, migration_config=migration_off(),
                                      create_offline_binding=True, on_started=capture))
    try:
        await asyncio.wait_for(started.wait(), 3)
        service = services[0]
        await asyncio.sleep(.05)
        assert not service.runner.ready_to_trade
        assert not service.controller.trading_permissions_ready()
        assert service.controller._control_loop_task is not None
        assert not service.controller.order_safety_watchdog_task.done()
        assert not service.store._wal.all_records()
        stop.set()
        await asyncio.wait_for(task, 3)
        assert service._revoked and service._close_task.done()
        assert service.controller._control_loop_task.done()
        assert service.controller.order_safety_watchdog_task.done()
        assert service.runner.listen_to_executor_actions_task.done()
        assert all(t.done() for t in service.connectors['okx'].__dict__['_headless_network_stops'])
    finally:
        stop.set()
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_headless_startup_kill_keeps_disabled_process_alive_until_stop(tmp_path):
    config, path = await config_file(tmp_path)
    config = config.model_copy(update={'controller': config.controller.model_copy(update={'manual_kill_switch': True})})
    path.write_text(config.model_dump_json())
    stop, started, services = asyncio.Event(), asyncio.Event(), []

    def capture(service):
        services.append(service)
        started.set()
    task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=stop, tick_seconds=.01,
                                      startup_timeout_s=3, shutdown_timeout_s=2, migration_config=migration_off(),
                                      create_offline_binding=True, on_started=capture))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await asyncio.sleep(.03)
        assert not task.done()
        service = services[0]
        assert service.configuration.state.kill_latched and service._revoked
        assert not service.runtime._local_ready() and not service.store._wal.all_records()
        assert service.controller._control_loop_task is not None
    finally:
        stop.set()
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_headless_network_restarts_do_not_retain_a_task_per_historical_stop(tmp_path):
    config, _ = await config_file(tmp_path)
    inputs = build(config)
    connector = inputs.connectors['okx']
    try:
        for _ in range(20):
            await connector.stop_network()
        assert not connector.__dict__['_headless_network_stops']
    finally:
        inputs.market_data_provider.stop()
        await connector.join_network_shutdown()


@pytest.mark.asyncio
async def test_headless_subprocess_sigterm_closes_worker_and_restarts_cold_without_prompt(tmp_path):
    _, path = await config_file(tmp_path)
    command = [sys.executable, '-m', 'hummingbot.strategy_v2.life_liquidity.storage.headless', '--config', str(path),
               '--factory', FACTORY, '--tick-seconds', '.01', '--startup-timeout', '5', '--shutdown-timeout', '2',
               '--no-env-file']
    for bootstrap in (True, False):
        child = await asyncio.create_subprocess_exec(*command, *(['--create-offline-binding'] if bootstrap else []),
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                     env={**os.environ, 'LIFE_AUTO_MIGRATE': 'false'})
        try:
            async with asyncio.timeout(8):
                line = await child.stdout.readline()
                if not line:
                    _, errors = await child.communicate()
                    pytest.fail(errors.decode())
                assert line.strip() == b'LIFE_SERVICE_STARTED_ACTIVATION_NOT_GRANTED'
                child.send_signal(signal.SIGTERM)
                _, errors = await child.communicate()
                assert child.returncode == 0, errors.decode()
        finally:
            if child.returncode is None:
                child.kill()
                await child.communicate()
    assert (tmp_path / 'state' / 'binding.json').is_file()


@pytest.mark.asyncio
async def test_headless_requires_explicit_independent_watchdog_before_source_factory(tmp_path):
    config = await settings(tmp_path)
    path = tmp_path / 'service.yml'
    path.write_text(config.model_dump_json())
    with patch('hummingbot.strategy_v2.life_liquidity.storage.headless.load_factory') as factory:
        with pytest.raises(StorageError, match='HEADLESS_SAFETY_WATCHDOG_REQUIRED'):
            await launch(config_path=path, factory_spec=FACTORY, stop=asyncio.Event(), tick_seconds=.01,
                         startup_timeout_s=2, shutdown_timeout_s=2, migration_config=migration_off())
        factory.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_headless_host_still_joins_owned_loops_and_releases_after_cleanup(tmp_path):
    _, path = await config_file(tmp_path)
    started, services = asyncio.Event(), []

    def capture(service):
        services.append(service)
        started.set()

    task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=asyncio.Event(), tick_seconds=.01,
                                      startup_timeout_s=3, shutdown_timeout_s=2, migration_config=migration_off(),
                                      create_offline_binding=True, on_started=capture))
    await asyncio.wait_for(started.wait(), 3)
    await asyncio.sleep(.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    service = services[0]
    assert service._revoked and service._close_task.done()
    assert service.controller._control_loop_task.done()
    assert service.controller.order_safety_watchdog_task.done()
    assert not service.connectors['okx'].__dict__['_headless_network_stops']


@pytest.mark.asyncio
async def test_headless_shutdown_timeout_does_not_release_still_running_file_worker(tmp_path):
    import threading
    from pathlib import Path

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
    _, path = await config_file(tmp_path)
    stop, started, services = asyncio.Event(), asyncio.Event(), []
    entered, release = threading.Event(), threading.Event()

    def capture(service):
        services.append(service)
        started.set()

    def block():
        entered.set()
        assert release.wait(5)

    task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=stop, tick_seconds=.01,
                                      startup_timeout_s=3, shutdown_timeout_s=.05, migration_config=migration_off(),
                                      create_offline_binding=True, on_started=capture))
    await asyncio.wait_for(started.wait(), 3)
    service = services[0]
    worker = asyncio.create_task(service.store._call(block))
    while not entered.is_set():
        await asyncio.sleep(.001)
    competitor = AccountRiskPoolLock(service.store.binding.account_uid, Path(service.config.account_lock_root))
    try:
        stop.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, 2)
        assert service._revoked and not service.runtime._local_ready()
        assert service._close_task is not None and not service._close_task.done()
        with pytest.raises(AccountLockUnavailable, match='ACCOUNT_LOCK_HELD_ELSEWHERE'):
            competitor.acquire()
    finally:
        release.set()
        await worker
        await service.close()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        competitor.acquire()
        competitor.release()


@pytest.mark.asyncio
async def test_connector_shutdown_failure_is_retained_after_task_completion(tmp_path):
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    config, _ = await config_file(tmp_path)
    inputs = build(config)

    async def fail(_):
        raise OSError('synthetic connector shutdown failure')

    with patch.object(OkxExchange, 'stop_network', fail):
        with pytest.raises(OSError, match='synthetic connector shutdown failure'):
            await inputs.connectors['okx'].stop_network()
        with pytest.raises(OSError, match='synthetic connector shutdown failure'):
            await inputs.connectors['okx'].join_network_shutdown()
    inputs.market_data_provider.stop()


@pytest.mark.asyncio
async def test_headless_joins_native_network_check_cleanup_before_clock_drops_its_reference(tmp_path):
    _, path = await config_file(tmp_path)
    stop, entered, stopping, release = (asyncio.Event() for _ in range(4))

    def factory(config):
        inputs = build(config)

        async def check_loop():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopping.set()
                await release.wait()

        inputs.connectors['okx']._check_network_loop = check_loop
        return inputs

    with patch('hummingbot.strategy_v2.life_liquidity.storage.headless.load_factory', return_value=factory):
        task = asyncio.create_task(launch(config_path=path, factory_spec=FACTORY, stop=stop, tick_seconds=.01,
                                          startup_timeout_s=3, shutdown_timeout_s=2, migration_config=migration_off(),
                                          create_offline_binding=True))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            stop.set()
            await asyncio.wait_for(stopping.wait(), 1)
            await asyncio.sleep(.05)
            assert not task.done()
        finally:
            release.set()
            stop.set()
            await asyncio.wait_for(task, 3)
