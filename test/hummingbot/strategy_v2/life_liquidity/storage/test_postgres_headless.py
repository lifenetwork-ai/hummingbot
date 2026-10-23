"""Native CLI configuration/migration/source lifetime; no network or real DB."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service import limits
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres import headless
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource


def config():
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', UUID(int=1), 1, 'a' * 64, 'b' * 64)
    at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    controller = LifeLiquidityConfig(id='main', storage_backend='postgres', storage_binding=binding)
    controller = controller.model_copy(update={'strategy': type(controller.strategy).model_validate(dict(
        controller.strategy.model_dump(mode='json'),
        perpetual=dict(enabled=True, position_mode='HEDGE', margin_mode='cross', leverage=2)))})
    return headless.PgServiceConfig(
        account_id=UUID(int=5), database_lineage=UUID(int=6), controller=controller,
        owner=OwnerToken(UUID(int=2), UUID(int=3), 1, UUID(int=4), at, at + timedelta(hours=1)),
        sources=tuple(EventSource('main', 'life.yml', 'strategy_v2', m) for m in ('okx', 'okx_perpetual')),
        limits=limits())


@pytest.mark.asyncio
async def test_native_service_config_yaml_roundtrip_and_invalid_input_bound(tmp_path):
    configured = config()
    path = tmp_path / 'service.yml'
    path.write_text(configured.model_dump_json())  # JSON is a YAML subset.
    assert (await headless.PgServiceConfig.read_yaml(path)).model_dump_json() == configured.model_dump_json()
    for bad in ('{}', 'unknown: true', '!!python/object:object {}', 'x' * 131073):
        path.write_text(bad)
        with pytest.raises(StorageError, match='PG_SERVICE_CONFIG_INVALID'):
            await headless.PgServiceConfig.read_yaml(path)


@pytest.mark.asyncio
async def test_managed_process_yaml_contains_only_public_lifecycle_configuration(tmp_path):
    payload = json.loads(config().model_dump_json())
    payload['managed_process'] = dict(claim_operation_id='original-claim', heartbeat_interval_ms=250)
    path = tmp_path / 'managed.yml'
    path.write_text(json.dumps(payload))
    selected = await headless.PgServiceConfig.read_yaml(path)
    assert selected.managed_process.claim_operation_id == 'original-claim'
    assert json.loads(selected.model_dump_json()) == payload
    for body in (dict(payload['managed_process'], heartbeat_interval_ms=0),
                 dict(payload['managed_process'], heartbeat_interval_ms=True),
                 dict(payload['managed_process'], capability='synthetic-secret'),
                 dict(payload['managed_process'], claim_operation_id='invalid operation')):
        path.write_text(json.dumps(dict(payload, managed_process=body)))
        with pytest.raises(StorageError, match='PG_SERVICE_CONFIG_INVALID'):
            await headless.PgServiceConfig.read_yaml(path)


@pytest.mark.asyncio
@pytest.mark.parametrize('native,provisional', [(False, False), (True, False), (True, True)])
async def test_native_service_protocol_selection_is_explicit_and_roundtrips(tmp_path, native, provisional):
    payload = json.loads(config().model_dump_json())
    payload.update(native_trading=native, provisional_quotes=provisional)
    path = tmp_path / 'selected.yml'
    path.write_text(json.dumps(payload))
    selected = await headless.PgServiceConfig.read_yaml(path)
    assert selected.native_trading is native and selected.provisional_quotes is provisional
    assert json.loads(selected.model_dump_json()) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize('native,provisional', [(False, True), ('true', True), (True, 'true'), (True, 1)])
async def test_native_service_rejects_invalid_or_observation_only_provisional_selection(tmp_path, native, provisional):
    payload = json.loads(config().model_dump_json())
    payload.update(native_trading=native, provisional_quotes=provisional)
    path = tmp_path / 'selected.yml'
    path.write_text(json.dumps(payload))
    with pytest.raises(StorageError, match='PG_SERVICE_CONFIG_INVALID'):
        await headless.PgServiceConfig.read_yaml(path)


def test_native_cli_does_not_print_factory_or_driver_error_payload(monkeypatch, capsys):
    import sys

    async def failed(args):
        raise RuntimeError('synthetic-source-secret')
    monkeypatch.setattr(headless, '_cli', failed)
    monkeypatch.setattr(sys, 'argv', ['headless', '--config', 'unused.yml', '--source-factory', 'trusted:factory',
                                      '--tick-seconds', '.1', '--startup-timeout', '2', '--shutdown-timeout', '2'])
    assert headless.main() == 1
    captured = capsys.readouterr()
    assert 'synthetic-source-secret' not in captured.err + captured.out
    assert 'startup/shutdown failed' in captured.err


@pytest.mark.asyncio
async def test_repository_host_holds_factory_clients_after_service_close_timeout_until_owned_close_finishes():
    from contextlib import AsyncExitStack

    from hummingbot.strategy_v2.life_liquidity.storage.headless import FileServiceHost
    entered, release, events = asyncio.Event(), asyncio.Event(), []

    @asynccontextmanager
    async def factory():
        try:
            yield
        finally:
            events.append('factory-close')

    async def physical():
        entered.set()
        await release.wait()
        events.append('physical-close')
    owned = asyncio.create_task(physical())

    async def close():
        async with asyncio.timeout(.01):
            await asyncio.shield(owned)
    provider = object()
    service = SimpleNamespace(connectors={}, controller=SimpleNamespace(market_data_provider=provider),
                              close=close, revoke=lambda: None, _close_task=owned)
    inputs = SimpleNamespace(connectors={}, market_data_provider=provider)
    stack = AsyncExitStack()
    await stack.enter_async_context(factory())
    host = FileServiceHost(service, inputs, tick_seconds=.1, shutdown_timeout_s=.02, source_stack=stack)
    try:
        await entered.wait()
        with pytest.raises(TimeoutError):
            await host.close()
        assert not owned.done() and 'factory-close' not in events
    finally:
        release.set()
        await owned
        if host._close_task is not None:
            await asyncio.gather(host._close_task, return_exceptions=True)
        await stack.aclose()
    assert events == ['physical-close', 'factory-close']


@pytest.mark.asyncio
@pytest.mark.parametrize('slow_network', [False, True])
async def test_host_close_timeout_retains_both_sdk_networks_until_physical_cleanup(slow_network):
    from contextlib import AsyncExitStack

    from hummingbot.strategy_v2.life_liquidity.storage.headless import FileServiceHost
    from hummingbot.strategy_v2.life_liquidity.storage.headless_connectors import JoinedNetworkShutdown
    release, events = asyncio.Event(), []
    network_entered, network_release = asyncio.Event(), asyncio.Event()
    if not slow_network:
        network_release.set()

    class Network:
        check_network_task = None

        async def stop_network(self):
            network_entered.set()
            await network_release.wait()
            events.append('network-close')

    class SDK(JoinedNetworkShutdown, Network):
        pass

    async def physical():
        await release.wait()
        events.append('physical-close')
    owned = asyncio.create_task(physical())

    async def close():
        async with asyncio.timeout(.01):
            await asyncio.shield(owned)

    @asynccontextmanager
    async def factory():
        try:
            yield
        finally:
            events.append('factory-close')
    connectors = {m: SDK() for m in ('okx', 'okx_perpetual')}
    provider = object()
    inputs = SimpleNamespace(connectors=connectors, market_data_provider=provider)
    service = SimpleNamespace(connectors=connectors, controller=SimpleNamespace(market_data_provider=provider),
                              close=close, revoke=lambda: None, _close_task=owned)
    stack = AsyncExitStack()
    await stack.enter_async_context(factory())
    host = FileServiceHost(service, inputs, tick_seconds=.1, shutdown_timeout_s=.02, source_stack=stack)
    try:
        with pytest.raises(TimeoutError):
            await host.close()
        with pytest.raises(TimeoutError):
            await host._stop_networks()
        assert not events and not owned.done()
        # Host deadlines cannot cancel the original network-stop tasks either.
        for sdk in connectors.values():
            assert sdk._headless_network_stops
            assert all(not t.done() for t in sdk._headless_network_stops)
    finally:
        release.set()
        await owned
        try:
            if slow_network:
                await asyncio.wait_for(network_entered.wait(), 1)
                await asyncio.sleep(.03)  # Cross the host's own wait budget.
                assert 'factory-close' not in events and not host._close_task.done()
        finally:
            network_release.set()
            await asyncio.gather(host._close_task, return_exceptions=True)
            await asyncio.gather(*(sdk.join_network_shutdown() for sdk in connectors.values()))
            await stack.aclose()
    assert events[0] == 'physical-close'
    assert events.count('factory-close') == 1 and events.count('network-close') == 2
    assert events[-1] == 'factory-close'
    with pytest.raises(TimeoutError):
        await host.close()  # Observing completion never erases the original error.


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['host', 'open'])
async def test_native_prehost_failure_retains_factory_and_network_until_owned_close(tmp_path, monkeypatch, failure):
    configured = config()
    path = tmp_path / 'service.yml'
    path.write_text(configured.model_dump_json())
    release, closed, events = asyncio.Event(), asyncio.Event(), []
    runtime = SimpleNamespace(binding=configured.controller.storage_binding, account_id=configured.account_id,
                              database_lineage=configured.database_lineage)
    monkeypatch.setattr(headless, 'PgConfig', SimpleNamespace)
    monkeypatch.setattr(headless, 'auto_migrate', AsyncMock())

    class Inputs:
        def __init__(self):
            self.register_process = None
            self.generation_access_factory = None
            self.integrity_monitor = None
            self.connectors = {m: SimpleNamespace(stop_network=lambda: events.append('network-close'),
                                                  join_network_shutdown=AsyncMock()) for m in ('okx', 'okx_perpetual')}
            self.market_data_provider = object()
            self.clock = self.independent_fill_value = self.independent_cashflow_value = lambda *args: None
    monkeypatch.setattr(headless, 'PgHeadlessInputs', Inputs)

    @asynccontextmanager
    async def factory(value):
        try:
            yield Inputs()
        finally:
            events.append('factory-close')
            closed.set()
    monkeypatch.setattr(headless, 'load_factory', lambda spec: factory)

    async def physical():
        await release.wait()
        events.append('physical-close')
    owned = asyncio.create_task(physical())

    class Service:
        _close_task = owned

        async def close(self):
            async with asyncio.timeout(.01):
                await asyncio.shield(owned)
    service = Service()

    async def open_service(*args, **kwargs):
        if failure == 'open':
            registration = kwargs.get('_startup_owner')
            if registration is not None:
                registration.append(service)
            raise StorageError('SYNTHETIC_PARTIAL_OPEN_FAILURE')
        return service
    monkeypatch.setattr(headless.PgLiquidityService, 'open', open_service)

    def invalid_host(*args, **kwargs):
        raise StorageError('SYNTHETIC_HOST_CONSTRUCTION_FAILURE')
    monkeypatch.setattr(headless, 'PgServiceHost', invalid_host)
    try:
        with pytest.raises(TimeoutError):
            await headless.launch(config_path=path, factory_spec='trusted:factory', stop=asyncio.Event(),
                                  tick_seconds=.1, startup_timeout_s=1, shutdown_timeout_s=.02,
                                  runtime_config=runtime, migration_config=object())
        assert not events and not owned.done()
    finally:
        release.set()
        await owned
        await asyncio.wait_for(closed.wait(), 1)
    assert events[0] == 'physical-close'
    assert events.count('network-close') == 2 and events.count('factory-close') == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'migration', 'config', 'open', 'run', 'factory_result', 'control'])
async def test_native_launcher_awaits_migrations_and_joins_owned_sources_after_service(tmp_path, monkeypatch, failure):
    configured = config()
    if failure is None:
        payload = json.loads(configured.model_dump_json())
        configured = headless.PgServiceConfig.model_validate_json(json.dumps(dict(
            payload, native_trading=True, provisional_quotes=True,
            managed_process=dict(claim_operation_id='original-claim', heartbeat_interval_ms=250))))
    events, stop = [], asyncio.Event()
    path = tmp_path / 'service.yml'
    path.write_text('{}' if failure == 'config' else configured.model_dump_json())
    runtime = SimpleNamespace(binding=configured.controller.storage_binding, account_id=configured.account_id,
                              database_lineage=configured.database_lineage)
    # This host contract test isolates physical DB/Clock qualification, which
    # lives in test_postgres_service. No runtime admission is mocked there.
    monkeypatch.setattr(headless, 'PgConfig', SimpleNamespace)

    async def migrate(_):
        events.append('migrate')
        if failure == 'migration':
            raise RuntimeError('synthetic migration failure')
    monkeypatch.setattr(headless, 'auto_migrate', migrate)

    class Inputs:
        def __init__(self):
            self.register_process = AsyncMock()
            self.generation_access_factory = AsyncMock()
            self.integrity_monitor = None
            self.connectors = {'okx': SimpleNamespace(stop_network=lambda: events.append('network-stop'),
                                                      join_network_shutdown=AsyncMock())}
            self.market_data_provider = object()
            self.clock = self.independent_fill_value = self.independent_cashflow_value = lambda *args: None
    monkeypatch.setattr(headless, 'PgHeadlessInputs', Inputs)

    @asynccontextmanager
    async def factory(value):
        assert value.model_dump_json() == configured.model_dump_json()
        events.append('enter')
        try:
            yield object() if failure == 'factory_result' else Inputs()
        finally:
            events.append('factory-close')

    def load(spec):
        events.append('import-factory')
        return factory
    monkeypatch.setattr(headless, 'load_factory', load)

    class Service:
        async def close(self):
            events.append('service-close')

    async def open_service(*args, **kwargs):
        events.append('open')
        assert kwargs['native_trading'] is configured.native_trading
        assert kwargs['provisional_quotes'] is configured.provisional_quotes
        assert kwargs['managed_process'] == configured.managed_process
        assert callable(kwargs['register_process'])
        assert callable(kwargs['generation_access_factory'])
        if failure == 'open':
            raise RuntimeError('synthetic open failure')
        return Service()
    monkeypatch.setattr(headless.PgLiquidityService, 'open', open_service)

    class Host:
        def __init__(self, service, inputs, *, source_stack, **kwargs):
            self.service, self.stack = service, source_stack

        async def run(self, stopped):
            events.append('run')
            if failure == 'run':
                raise RuntimeError('synthetic run failure')

        async def close(self):
            await self.service.close()
            await self.stack.aclose()

        async def _stop_networks(self):
            events.append('network-stop')
    monkeypatch.setattr(headless, 'PgServiceHost', Host)

    async def explicit_control(service):
        events.append('explicit-control')
        await asyncio.sleep(0)
        if failure == 'control':
            raise RuntimeError('synthetic explicit control failure')
        events.append('control-ready')
    invoke = headless.launch(config_path=path, factory_spec='trusted:factory', stop=stop,
                             tick_seconds=.1, startup_timeout_s=2, shutdown_timeout_s=2,
                             runtime_config=runtime, migration_config=object(), on_started=explicit_control)
    if failure is None:
        await invoke
    else:
        with pytest.raises((StorageError, RuntimeError)):
            await invoke
    assert events[0] == 'migrate'
    if failure in ('migration', 'config'):
        assert events == ['migrate']
    else:
        assert events.index('import-factory') < events.index('enter') < events.index('factory-close')
        if failure in (None, 'run'):
            assert events.index('explicit-control') < events.index('control-ready') < events.index('run')
            assert events.index('service-close') < events.index('factory-close')
