"""A delayed event loop must not turn a runnable controller into a latched HALT."""
import asyncio
import time
from test.hummingbot.cli.test_service_availability import host
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def running_host(monkeypatch):
    item = host()
    controller = SimpleNamespace(
        config=SimpleNamespace(manual_kill_switch=False), _control_error=None,
        processed_data={}, _control_progress_at=time.monotonic() - 10,
        update_interval=1, _control_wait_until=None, _control_stage="sleep")
    core = item.hb.trading_core
    core.strategy = SimpleNamespace(controllers={"controller": controller})
    core.trade_fill_db = SimpleNamespace(persistence_healthy=True)
    core._strategy_running = True
    item.running, item.phase, item.applied_version = True, "running", 0
    row = dict(control_version=0, config_type="controller", config_file_path="test.yml",
               desired_state="running", recovery_state={}, mm_session=None)
    item.owner = MagicMock()
    item.owner.try_acquire.return_value = True
    item.owner.check_owner.return_value = row
    item.owner.allow_new_orders = True
    item.owner.shutdown_requested = False
    item.stop_core = AsyncMock(return_value=True)
    item.poll_reconciliation = AsyncMock()
    item.poll_funding, item.poll_outflow, item.publish_status = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr("hummingbot.core.order_submission_guard.install_guard", lambda owner: None)
    monkeypatch.setattr("hummingbot.core.mm_liquidity.capture_liquidity", lambda host: None)
    return item, controller


@pytest.mark.asyncio
@pytest.mark.parametrize("wake_source", ["ready_callback", "expired_timer"])
async def test_runnable_controller_gets_a_turn_before_stall_is_latched(monkeypatch, wake_source):
    item, controller = running_host(monkeypatch)
    wake = asyncio.Event()
    gates = []

    async def delayed_controller():
        await wake.wait()
        gates.append(item.runtime_healthy())
        controller._control_progress_at = time.monotonic()
        gates.append(item.runtime_healthy())
        await asyncio.Event().wait()

    task = asyncio.create_task(delayed_controller())
    controller._control_loop_task = task
    try:
        await asyncio.sleep(0)  # Controller is suspended on the expired wait.
        # Reproduce ready-queue ordering after a delayed loop: the observer
        # runs before the callback that wakes an otherwise healthy controller.
        loop = asyncio.get_running_loop()
        if wake_source == "expired_timer":
            loop.call_later(-1, wake.set)
        else:
            loop.call_soon(wake.set)
        await item.observe_once()
        item.owner.require_recovery.assert_not_called()
        item.stop_core.assert_not_awaited()
        assert item.running and item.phase == "running"
        assert gates == [False, True]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_genuinely_stalled_controller_still_latches_halt(monkeypatch):
    item, controller = running_host(monkeypatch)
    controller._control_stage = "control"
    task = asyncio.create_task(asyncio.Event().wait())
    controller._control_loop_task = task
    try:
        await item.observe_once()
        item.owner.require_recovery.assert_called_once()
        item.stop_core.assert_awaited_once()
        assert not item.running and item.phase == "halted"
        assert item.error == "controller_task_stalled"
        assert not item.runtime_healthy()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_controller_failure_during_confirmation_cannot_reopen_send_gate(monkeypatch):
    item, controller = running_host(monkeypatch)
    task = asyncio.create_task(asyncio.Event().wait())
    controller._control_loop_task = task
    try:
        asyncio.get_running_loop().call_soon(setattr, controller, "_control_error", "RuntimeError")
        await item.observe_once()
        item.owner.require_recovery.assert_called_once()
        item.stop_core.assert_awaited_once()
        assert item.error == "controller_task_failed"
        assert not item.runtime_healthy()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_cancels_stall_confirmation_without_reopening_gate(monkeypatch):
    item, _ = running_host(monkeypatch)
    task = asyncio.create_task(item.confirmed_runtime_problem())
    asyncio.get_running_loop().call_soon(task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not item.runtime_healthy()
    item.owner.require_recovery.assert_not_called()
