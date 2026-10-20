"""Native observation service CLI. Migrations precede source-factory import.

Uses the explicit operational PG environment settings and an existing imported
account/owner. No file financial service or implicit account seed is installed.
"""
import argparse
import asyncio
import inspect
import json
import signal
import sys
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.headless import (
    FileServiceHost,
    _close_owned_service,
    _seconds,
    load_factory,
)
from hummingbot.strategy_v2.life_liquidity.storage.headless_connectors import (
    HeadlessOkxExchange,
    HeadlessOkxPerpetualDerivative,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import load_config
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import auto_migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime_config import load_runtime_config
from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService, PgServiceLimits
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource


class PgServiceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    account_id: UUID
    database_lineage: UUID
    controller: LifeLiquidityConfig
    owner: OwnerToken
    sources: tuple[EventSource, ...]
    limits: PgServiceLimits
    native_trading: bool = False

    @classmethod
    async def read_yaml(cls, path):
        import yaml

        def read():
            try:
                with Path(path).open('rb') as stream:
                    raw = stream.read(131073)
                if not raw or len(raw) > 131072:
                    raise ValueError()
                return cls.model_validate_json(json.dumps(yaml.safe_load(raw)))
            except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
                raise StorageError('PG_SERVICE_CONFIG_INVALID') from None
        return await asyncio.to_thread(read)


@dataclass(frozen=True)
class PgHeadlessInputs:
    connectors: dict
    market_data_provider: object
    clock: Callable
    independent_fill_value: Callable
    independent_cashflow_value: Callable

    def __post_init__(self):
        if (type(self.connectors) is not dict or set(self.connectors) != {'okx', 'okx_perpetual'}
                or not isinstance(self.connectors['okx'], HeadlessOkxExchange)
                or not isinstance(self.connectors['okx_perpetual'], HeadlessOkxPerpetualDerivative)
                or getattr(self.market_data_provider, 'connectors', None) != self.connectors
                or any(not callable(v) for v in (self.clock, self.independent_fill_value, self.independent_cashflow_value))):
            raise StorageError('PG_HEADLESS_INPUTS_INVALID')
        object.__setattr__(self, 'connectors', dict(self.connectors))


class PgServiceHost(FileServiceHost):
    """Reuse the repository process/Clock host; financial service stays native."""


async def launch(*, config_path, factory_spec, stop, tick_seconds, startup_timeout_s, shutdown_timeout_s,
                 migration_config=None, runtime_config=None, env_file=None, on_started=None):
    _seconds(tick_seconds)
    _seconds(startup_timeout_s)
    _seconds(shutdown_timeout_s)
    if not isinstance(stop, asyncio.Event):
        raise StorageError('PG_HEADLESS_STOP_INVALID')
    inputs = service = host = None
    starting = []
    source_stack = AsyncExitStack()
    # Exactly the same awaited numbered migration contract as the File host.
    await auto_migrate(migration_config)
    try:
        async with asyncio.timeout(startup_timeout_s):
            config = await PgServiceConfig.read_yaml(config_path)
            runtime_config = runtime_config or load_runtime_config(binding=config.controller.storage_binding,
                                                                   account_id=config.account_id,
                                                                   database_lineage=config.database_lineage,
                                                                   env_file=env_file)
            if type(runtime_config) is not PgConfig or runtime_config.binding != config.controller.storage_binding:
                raise StorageError('PG_HEADLESS_RUNTIME_MISMATCH')
            if runtime_config.account_id != config.account_id or runtime_config.database_lineage != config.database_lineage:
                raise StorageError('PG_HEADLESS_RUNTIME_MISMATCH')
            if stop.is_set():
                return
            inputs = load_factory(factory_spec)(config)
            if inspect.isawaitable(inputs):
                inputs = await inputs
            if isinstance(inputs, AbstractAsyncContextManager):
                inputs = await source_stack.enter_async_context(inputs)
            if type(inputs) is not PgHeadlessInputs:
                raise StorageError('PG_HEADLESS_FACTORY_RESULT_INVALID')
            service = await PgLiquidityService.open(
                config.controller, runtime_config=runtime_config, owner=config.owner, sources=config.sources,
                limits=config.limits, connectors=inputs.connectors, market_data_provider=inputs.market_data_provider,
                clock=inputs.clock, independent_fill_value=inputs.independent_fill_value,
                independent_cashflow_value=inputs.independent_cashflow_value, native_trading=config.native_trading,
                _startup_owner=starting)
            host = PgServiceHost(service, inputs, tick_seconds=tick_seconds, shutdown_timeout_s=shutdown_timeout_s,
                                 source_stack=source_stack)
        if on_started is not None:
            on_started(service)
        await host.run(stop)
    finally:
        if host is not None:
            try:
                await host.close()
            finally:
                await host._stop_networks()
        else:
            async def cleanup():
                try:
                    original = service if service is not None else starting[0] if starting else None
                    if original is not None:
                        await _close_owned_service(original)
                finally:
                    try:
                        if type(inputs) is PgHeadlessInputs:
                            for connector in inputs.connectors.values():
                                connector.stop_network()
                            await asyncio.gather(*(c.join_network_shutdown() for c in inputs.connectors.values()))
                    finally:
                        await source_stack.aclose()
            task = asyncio.create_task(cleanup())
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            async with asyncio.timeout(shutdown_timeout_s):
                await asyncio.shield(task)


async def _cli(args):
    stop, loop, registered = asyncio.Event(), asyncio.get_running_loop(), []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
            registered.append(sig)
        await launch(config_path=args.config, factory_spec=args.source_factory, stop=stop,
                     tick_seconds=args.tick_seconds, startup_timeout_s=args.startup_timeout,
                     shutdown_timeout_s=args.shutdown_timeout, env_file=args.env_file,
                     migration_config=load_config(env_file=args.env_file))
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--source-factory', required=True)
    parser.add_argument('--env-file')
    parser.add_argument('--tick-seconds', type=float, required=True)
    parser.add_argument('--startup-timeout', type=float, required=True)
    parser.add_argument('--shutdown-timeout', type=float, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_cli(args))
    except (Exception, KeyboardInterrupt):
        # A trusted factory or driver exception may include source credentials.
        # The process exit is explicit; never serialize the exception payload.
        print('PostgreSQL service startup/shutdown failed; configuration or native reconciliation is required.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
