"""Explicit file-service process host. Source factories are trusted local code.

python -m hummingbot.strategy_v2.life_liquidity.storage.headless --help
No activation, financial bootstrap or exchange credentials are inferred here.
"""
import argparse
import asyncio
import importlib
import inspect
import json
import math
import signal
import sys
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from uuid import uuid4

from hummingbot.core.clock import Clock, ClockMode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.headless_inputs import HeadlessInputs
from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService


def _seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise StorageError('HEADLESS_TIME_BUDGET_INVALID')
    return value


class FileServiceHost:
    def __init__(self, service, inputs, *, tick_seconds, shutdown_timeout_s, source_stack=None):
        if service.connectors != inputs.connectors or service.controller.market_data_provider is not inputs.market_data_provider:
            raise StorageError('HEADLESS_SERVICE_INPUTS_MISMATCH')
        self.service, self.inputs = service, inputs
        self.tick_seconds, self.shutdown_timeout_s = _seconds(tick_seconds), _seconds(shutdown_timeout_s)
        self._clock_task = self._stop_waiter = self._close_task = None
        self._reload_task = None
        self._network_checks = set()
        self._source_stack = source_stack
        self._ran = False

    async def run(self, stop, *, reload_event=None, config_path=None, on_reload_result=None):
        if self._ran or not isinstance(stop, asyncio.Event):
            raise StorageError('HEADLESS_RUN_ALREADY_STARTED_OR_STOP_INVALID')
        self._ran = True
        clock = Clock(ClockMode.REALTIME, tick_size=self.tick_seconds)
        for connector in self.inputs.connectors.values():
            clock.add_iterator(connector)
        clock.add_iterator(self.service.runner)
        try:
            with clock:
                try:
                    if not stop.is_set():
                        self._clock_task = asyncio.create_task(clock.run())
                        self._stop_waiter = asyncio.create_task(stop.wait())
                        if reload_event is not None:
                            self._reload_task = asyncio.create_task(self._reload(reload_event, config_path, on_reload_result))
                        tasks = tuple(t for t in (self._clock_task, self._stop_waiter, self._reload_task) if t is not None)
                        done, _ = await asyncio.wait(tasks,
                                                     return_when=asyncio.FIRST_COMPLETED)
                        if self._clock_task in done:
                            await self._clock_task
                            raise StorageError('HEADLESS_CLOCK_STOPPED_UNEXPECTEDLY')
                        if self._reload_task in done:
                            await self._reload_task
                            raise StorageError('HEADLESS_RELOADER_STOPPED_UNEXPECTEDLY')
                finally:
                    self.service.revoke()  # Before waiting for any persistence/network cleanup.
                    for task in (self._clock_task, self._stop_waiter, self._reload_task):
                        if task is not None:
                            task.cancel()
                    await asyncio.gather(*(t for t in (self._clock_task, self._stop_waiter, self._reload_task) if t is not None),
                                         return_exceptions=True)
                    # Keep native market/event registrations until the service
                    # has recorded executor uncertainty and closed its worker.
                    try:
                        await self.close()
                    finally:
                        # Native c_stop clears check_network_task. Join its
                        # cancellation cleanup before Clock discards that handle.
                        await self._stop_networks()
        finally:
            # Native Clock exit schedules each tracked stop_network awaitable.
            # Startup-before-clock errors still get an explicit network stop.
            await self._stop_networks()

    async def _reload(self, event, config_path, callback):
        if not isinstance(event, asyncio.Event) or config_path is None:
            raise StorageError('HEADLESS_RELOAD_CONFIG_REQUIRED')
        while True:
            await event.wait()
            event.clear()  # Repeated signals coalesce to one additional reload.
            try:
                receipt = await self.service.reload_yaml(config_path, operation_id='sighup:' + uuid4().hex)
                code = receipt.reason_code
            except Exception:
                code = 'CONFIG_RELOAD_FAILED'  # No raw YAML, source exception or credentials.
            if callback is not None:
                callback(code)

    async def close(self):
        self.service.revoke()
        self._cancel_network_checks()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_service_and_sources())
        async with asyncio.timeout(self.shutdown_timeout_s):
            # A timeout/cancel cannot release a still-running file worker or
            # certify terminal exchange state. The owning close task remains.
            await asyncio.shield(self._close_task)

    async def _close_service_and_sources(self):
        try:
            await self.service.close()
        finally:
            if self._source_stack is not None:
                # Retain factory-owned clients until worker/ACK/reporting cleanup
                # has finished. A caller timeout does not cancel this owned task.
                await self._source_stack.aclose()

    def _cancel_network_checks(self):
        for connector in self.inputs.connectors.values():
            check = connector.check_network_task
            if check is not None:
                self._network_checks.add(check)
                if not check.cancelling():
                    check.cancel()

    async def _stop_networks(self):
        self._cancel_network_checks()
        for connector in self.inputs.connectors.values():
            # Idempotent native stop also covers construction/Clock-start failure.
            connector.stop_network()
        async with asyncio.timeout(self.shutdown_timeout_s):
            await asyncio.gather(*self._network_checks, return_exceptions=True)
            await asyncio.gather(*(c.join_network_shutdown() for c in self.inputs.connectors.values()))


def load_factory(spec):
    if type(spec) is not str or spec.count(':') != 1:
        raise StorageError('HEADLESS_FACTORY_INVALID')
    module, name = spec.split(':')
    if not module or not name.isidentifier() or name.startswith('_'):
        raise StorageError('HEADLESS_FACTORY_INVALID')
    factory = getattr(importlib.import_module(module), name)
    if not callable(factory):
        raise StorageError('HEADLESS_FACTORY_INVALID')
    return factory


async def launch(*, config_path, factory_spec, stop, tick_seconds, startup_timeout_s, shutdown_timeout_s,
                 migration_config=None, create_offline_binding=False, on_started=None, reload_event=None, on_reload_result=None):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import auto_migrate
    _seconds(tick_seconds)
    _seconds(startup_timeout_s)
    _seconds(shutdown_timeout_s)
    inputs = service = host = None
    source_stack = AsyncExitStack()
    # This await precedes even importing factory code or constructing connectors.
    await auto_migrate(migration_config)
    try:
        async with asyncio.timeout(startup_timeout_s):
            config = await FileLiquidityService.read_yaml(config_path)
            if config.controller.safety_watchdog_interval_ms is None:
                raise StorageError('HEADLESS_SAFETY_WATCHDOG_REQUIRED')
            if stop.is_set():
                return
            factory = load_factory(factory_spec)
            inputs = factory(config)
            if inspect.isawaitable(inputs):
                inputs = await inputs
            if isinstance(inputs, AbstractAsyncContextManager):
                inputs = await source_stack.enter_async_context(inputs)
            if type(inputs) is not HeadlessInputs:
                raise StorageError('HEADLESS_FACTORY_RESULT_INVALID')
            service = await FileLiquidityService.open(config, connectors=inputs.connectors, sources=inputs.sources,
                                                      market_data_provider=inputs.market_data_provider,
                                                      create_binding=create_offline_binding)
            host = FileServiceHost(service, inputs, tick_seconds=tick_seconds, shutdown_timeout_s=shutdown_timeout_s,
                                   source_stack=source_stack)
        if on_started is not None:
            on_started(service)
        await host.run(stop, reload_event=reload_event, config_path=config_path, on_reload_result=on_reload_result)
    finally:
        if host is not None:
            try:
                await host.close()
            finally:
                await host._stop_networks()
        else:
            async def cleanup_startup():
                try:
                    if service is not None:
                        await service.close()
                finally:
                    try:
                        if type(inputs) is HeadlessInputs:
                            for connector in inputs.connectors.values():
                                connector.stop_network()
                            await asyncio.gather(*(c.join_network_shutdown() for c in inputs.connectors.values()))
                    finally:
                        await source_stack.aclose()
            cleanup = asyncio.create_task(cleanup_startup())
            async with asyncio.timeout(shutdown_timeout_s):
                await asyncio.shield(cleanup)


async def _cli(args):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import load_config
    stop, reload_event, loop = asyncio.Event(), asyncio.Event(), asyncio.get_running_loop()
    handled = []
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
            handled.append(sig)
        loop.add_signal_handler(signal.SIGHUP, reload_event.set)
        handled.append(signal.SIGHUP)
        await launch(config_path=args.config, factory_spec=args.factory, stop=stop, tick_seconds=args.tick_seconds,
                     startup_timeout_s=args.startup_timeout, shutdown_timeout_s=args.shutdown_timeout,
                     migration_config=load_config(env_file=args.env_file, no_env_file=args.no_env_file),
                     create_offline_binding=args.create_offline_binding,
                     on_started=lambda _: print('LIFE_SERVICE_STARTED_ACTIVATION_NOT_GRANTED', flush=True),
                     reload_event=reload_event,
                     on_reload_result=lambda code: print(json.dumps({'configuration': code, 'activation_granted': False}), flush=True))
    finally:
        for sig in handled:
            loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Absolute service YAML path')
    parser.add_argument('--factory', required=True, help='Trusted local module:factory for qualified source/connector assembly')
    parser.add_argument('--tick-seconds', type=float, required=True)
    parser.add_argument('--startup-timeout', type=float, required=True)
    parser.add_argument('--shutdown-timeout', type=float, required=True)
    parser.add_argument('--create-offline-binding', action='store_true', help='OFFLINE_SIMULATION only; no live bootstrap')
    env = parser.add_mutually_exclusive_group()
    env.add_argument('--env-file', type=Path)
    env.add_argument('--no-env-file', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(_cli(args))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        # Source/driver exceptions may contain credentials; do not print them.
        print(json.dumps({'error': type(error).__name__, 'code': error.code if isinstance(error, StorageError)
                          else 'HEADLESS_START_OR_SHUTDOWN_FAILED'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
