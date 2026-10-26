import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, RuntimeSettings


def service_host():
    settings = RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"})
    return ServiceHost(settings, asyncio.Event(), MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["v1-strategy", "controller", "mm-session"])
async def test_stopped_selection_does_not_require_previous_revision_yaml_or_credentials(kind):
    host = service_host()
    await host.validate_selection({
        "desired_state": "stopped", "config_type": kind, "config_file_path": "removed.yml",
    })


@pytest.mark.asyncio
async def test_running_selection_still_requires_deployed_yaml():
    with pytest.raises(RuntimeConfigurationError, match="deployed config"):
        await service_host().validate_selection({
            "desired_state": "running", "config_type": "controller", "config_file_path": "removed.yml",
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("await_point", ["cleanup", "validation"])
@pytest.mark.parametrize("replacement", ["stop", "switch"])
async def test_superseded_start_during_cleanup_or_validation_never_initializes_old_strategy(
        monkeypatch, await_point, replacement):
    from hummingbot.core import order_submission_guard

    monkeypatch.setattr(order_submission_guard, "install_guard", lambda owner: None)
    host = service_host()
    row = {
        "control_version": 1, "desired_state": "running", "config_type": "controller",
        "config_file_path": "old.yml", "mm_session": None, "recovery_state": {},
    }
    current = dict(row)
    host.owner = MagicMock()
    host.owner.try_acquire.return_value = True
    host.owner.check_owner.side_effect = lambda: dict(current)
    host.hb = SimpleNamespace(trading_core=SimpleNamespace())
    host.snapshot = MagicMock(return_value={})
    host.poll_reconciliation = AsyncMock()
    host.poll_funding = MagicMock()
    host.stop_core = AsyncMock(return_value=True)
    host.validate_selection = AsyncMock()
    host.start_strategy = AsyncMock(return_value=True)

    async def supersede(*args):
        current.update(control_version=2, desired_state="stopped" if replacement == "stop" else "running",
                       config_file_path="old.yml" if replacement == "stop" else "new.yml")
        return True

    if await_point == "cleanup":
        host.stop_core.side_effect = supersede
    else:
        host.validate_selection.side_effect = supersede
    try:
        await host.observe_once()
        host.owner.prepare.assert_not_called()
        assert host.start_task is None
        host.start_strategy.assert_not_awaited()
        host.owner.require_recovery.assert_not_called()
        host.owner.store.publish.assert_not_called()
        if await_point == "cleanup":
            host.validate_selection.assert_not_awaited()
    finally:
        if host.start_task is not None:
            host.start_task.cancel()
            await asyncio.gather(host.start_task, return_exceptions=True)
