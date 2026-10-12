"""The shared bootstrap must await migration before constructing any application."""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.client import runner
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import MigrationError


def patches(stack, gate):
    security = MagicMock()
    security.wait_til_decryption_done = AsyncMock()
    stack.enter_context(patch('hummingbot.client.config.security.Security', security))
    stack.enter_context(patch('hummingbot.init_logging'))
    stack.enter_context(patch('hummingbot.client.config.config_helpers.create_yml_files_legacy', new=AsyncMock()))
    stack.enter_context(patch('hummingbot.client.config.config_helpers.read_system_configs_from_yml', new=AsyncMock()))
    stack.enter_context(patch.object(runner.AllConnectorSettings, 'initialize_paper_trade_settings'))
    stack.enter_context(patch.object(runner, 'auto_migrate', new=AsyncMock(side_effect=gate)))
    app = stack.enter_context(patch.object(runner.HummingbotApplication, 'main_application'))
    return security, app


@pytest.mark.asyncio
async def test_bootstrap_waits_before_login_and_application_creation():
    gate = asyncio.Event()

    async def migrate():
        await gate.wait()

    with ExitStack() as stack:
        security, app = patches(stack, migrate)
        task = asyncio.create_task(runner.bootstrap_application(MagicMock(), MagicMock(), headless=True))
        await asyncio.sleep(0)
        security.login.assert_not_called()
        app.assert_not_called()
        gate.set()
        assert await task is app.return_value
        app.assert_called_once()


@pytest.mark.asyncio
async def test_failed_migration_blocks_application_and_login():
    async def fail():
        raise MigrationError('synthetic migration failure')

    with ExitStack() as stack:
        security, app = patches(stack, fail)
        with pytest.raises(MigrationError):
            await runner.bootstrap_application(MagicMock(), MagicMock())
        security.login.assert_not_called()
        app.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_migration_cannot_later_create_application():
    async def wait():
        await asyncio.Event().wait()

    with ExitStack() as stack:
        security, app = patches(stack, wait)
        task = asyncio.create_task(runner.bootstrap_application(MagicMock(), MagicMock()))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        security.login.assert_not_called()
        app.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_interactive_launcher_awaits_gate_before_construction():
    import importlib
    import sys

    with patch.dict(sys.modules, {'path_util': MagicMock()}):
        launcher = importlib.import_module('bin.hummingbot')
    gate = asyncio.Event()

    async def migrate():
        await gate.wait()

    with patch.object(launcher, 'auto_migrate', new=AsyncMock(side_effect=migrate)), \
            patch.object(launcher.HummingbotApplication, 'main_application') as app, \
            patch.object(launcher.Security, 'wait_til_decryption_done', new=AsyncMock()), \
            patch.object(launcher, 'create_yml_files_legacy', new=AsyncMock()), \
            patch.object(launcher, 'init_logging'), \
            patch.object(launcher.AllConnectorSettings, 'initialize_paper_trade_settings'), \
            patch.object(launcher, 'safe_gather', new=AsyncMock()):
        app.return_value.run = AsyncMock()
        task = asyncio.create_task(launcher.main_async(MagicMock(debug_console=False)))
        await asyncio.sleep(0)
        app.assert_not_called()
        gate.set()
        await task
        app.assert_called_once()
        # safe_gather is mocked; close the coroutine that the launcher gave it.
        launcher.safe_gather.call_args.args[0].close()
