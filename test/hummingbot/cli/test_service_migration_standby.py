import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings


@pytest.mark.asyncio
async def test_deferred_migration_keeps_startup_healthy_and_blocks_trading_until_handoff():
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test', 'ENABLE_AUTO_MIGRATION': 'true'})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    host.engine = MagicMock()
    host.owner = MagicMock()
    host.owner.connection = None
    host.owner.try_acquire.return_value = False
    host.bootstrapped = True
    host.start_strategy = AsyncMock()
    with patch('hummingbot.model.db_migration.postgres.runner.bootstrap_postgres', side_effect=[False, True]) as migrate:
        await host.observe_once()
        assert host.phase == 'standby'
        assert host.service_status()['reason_code'] == 'migration_pending'
        host.owner.try_acquire.assert_not_called()
        assert (await host.health(SimpleNamespace(path='/startup'))).status == 200
        assert (await host.health(SimpleNamespace(path='/ready'))).status == 503
        await host.observe_once()
        host.owner.try_acquire.assert_called_once()
        await host.observe_once()
        assert migrate.call_count == 2  # Completed bootstrap never enters the quote hot path.
    host.start_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_migration_health_wait_is_explicit_bounded_and_cleared_after_wait():
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test', 'ENABLE_AUTO_MIGRATION': 'true'})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    host.engine = MagicMock()
    host.owner = MagicMock(connection=None)
    host.bootstrapped = True
    started, release = threading.Event(), threading.Event()

    def migrate(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return False

    with patch('hummingbot.model.db_migration.postgres.runner.bootstrap_postgres', side_effect=migrate):
        task = asyncio.create_task(host.observe_once())
        try:
            assert await asyncio.to_thread(started.wait, 1)
            host.observer_progress_at = time.monotonic() - 6
            assert host.phase == 'migrating'
            assert host.service_status()['reason_code'] == 'migration_in_progress'
            assert time.monotonic() < host.observer_wait_until <= time.monotonic() + 180
            assert (await host.health(SimpleNamespace(path='/live'))).status == 200
            assert (await host.health(SimpleNamespace(path='/ready'))).status == 503
            host.owner.try_acquire.assert_not_called()
        finally:
            release.set()
            await task
    assert host.observer_wait_until is None
    host.observer_progress_at = time.monotonic() - 6
    assert (await host.health(SimpleNamespace(path='/live'))).status == 503


@pytest.mark.asyncio
async def test_standby_poll_does_not_repeat_full_bootstrap_while_old_owner_is_active():
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test', 'ENABLE_AUTO_MIGRATION': 'true'})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    host.engine = MagicMock()
    host.owner = MagicMock(connection=None)
    with patch('hummingbot.model.db_migration.postgres.runner.migration_owner_available', return_value=False, create=True), \
            patch('hummingbot.model.db_migration.postgres.runner.bootstrap_postgres', return_value=False) as migrate:
        for _ in range(3):
            await host.observe_once()
        assert host.phase == 'standby'
        migrate.assert_not_called()
        host.owner.try_acquire.assert_not_called()
