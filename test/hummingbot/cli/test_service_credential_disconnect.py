import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings


def host():
    instance = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}), asyncio.Event(), Mock())
    instance.owner = SimpleNamespace(owner_uuid="owner", generation=1)
    instance.hb = SimpleNamespace(trading_core=SimpleNamespace())
    instance.store = SimpleNamespace(engine=None, bot_id="bot")
    instance.managed_credentials = Mock()
    instance.stop_core = AsyncMock(return_value=True)
    return instance


@pytest.mark.asyncio
async def test_disconnect_is_dispatched_without_ordinary_stop_or_strategy_start():
    service = host()
    waiting = asyncio.Event()
    row = dict(database_now=1000, mm_session=None, recovery_state={"credential_operation": {
        "id": "disconnect", "action": "credentials_disconnect"}})

    async def pending(identity):
        await waiting.wait()
    with patch("hummingbot.cli.credential_disconnect.DisconnectCoordinator") as coordinator:
        coordinator.return_value.run = AsyncMock(side_effect=pending)
        assert await service.observe_credential_operation(row)
        await asyncio.sleep(0)
        assert service.credential_cleanup_task is not None and not service.credential_cleanup_task.done()
        service.stop_core.assert_not_awaited()
        assert service.phase == "disconnecting"
        service.credential_cleanup_task.cancel()
        await asyncio.gather(service.credential_cleanup_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_blocked_cleanup_waits_for_retry_instead_of_spinning_or_losing_its_fence():
    service = host()
    row = dict(database_now=1000, mm_session=None, recovery_state={"credential_operation": {
        "id": "disconnect", "action": "credentials_disconnect", "stage": "action_required", "operator_action_required": True}})
    assert await service.observe_credential_operation(row)
    assert service.credential_cleanup_task is None
    assert service.phase == "credential_action_required"


@pytest.mark.asyncio
async def test_wrong_source_keeps_admin_usable_and_blocks_application_of_an_existing_operation():
    service = host()
    service.managed_credentials = None
    row = dict(database_now=1000, mm_session=None, recovery_state={"credential_operation": {
        "id": "disconnect", "action": "credentials_disconnect"}})
    assert await service.observe_credential_operation(row)
    assert service.phase == "halted" and service.availability.snapshot()["incidents"]


@pytest.mark.asyncio
async def test_cleanup_branch_keeps_owner_publication_fresh_without_starting_idle_readers(monkeypatch):
    from hummingbot.core import order_submission_guard
    monkeypatch.setattr(order_submission_guard, "install_guard", lambda owner: None)
    service = host()
    service.owner.connection = Mock()
    service.owner.check_owner = Mock(return_value=dict(control_version=2))
    service.observe_credential_operation = AsyncMock(return_value=True)
    service.publish_status = AsyncMock()
    service.poll_funding = Mock()
    await service.observe_once()
    service.publish_status.assert_awaited_once_with(2)
    service.poll_funding.assert_not_called()


@pytest.mark.asyncio
async def test_stop_core_called_by_cleanup_does_not_await_its_own_task():
    service = host()
    service.stop_core = ServiceHost.stop_core.__get__(service)
    service.owner.allow_new_orders = False
    service.hb.trading_core = SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={}), _is_running=False)
    service.credential_cleanup_task = asyncio.current_task()
    assert await asyncio.wait_for(service.stop_core(), .1)
