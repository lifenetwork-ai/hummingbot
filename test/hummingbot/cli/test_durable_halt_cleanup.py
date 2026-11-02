"""Durable HALT is an action boundary, not only an operational label."""
import asyncio
import time
from test.hummingbot.cli.test_controller_stall_confirmation import running_host

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('reported_running', [False, True])
async def test_durable_halt_cleans_existing_core_before_publishing_halted(monkeypatch, late, reported_running):
    item, controller = running_host(monkeypatch)
    controller._control_progress_at = time.monotonic()
    item.running = reported_running
    row = item.owner.check_owner.return_value
    reads = 0
    live_orders = {'maker-bid', 'maker-ask'}

    def state():
        nonlocal reads
        reads += 1
        return {**row, 'recovery_state': {'halt': True} if not late or reads >= 2 else {}}

    async def cleanup():
        live_orders.clear()
        item.running = False
        item.hb.trading_core.strategy = None
        return True

    async def publish(version):
        assert not live_orders, 'HALT was published while resting quotes remained'
        assert item.phase == 'halted' and not item.running

    item.owner.check_owner.side_effect = state
    item.stop_core.side_effect = cleanup
    item.publish_status.side_effect = publish
    await item.observe_once()
    item.stop_core.assert_awaited_once()
    assert item.error == 'RecoveryRequired'


@pytest.mark.asyncio
async def test_durable_halt_cleanup_failure_prevents_a_successful_observation(monkeypatch):
    item, controller = running_host(monkeypatch)
    controller._control_progress_at = time.monotonic()
    item.owner.check_owner.return_value['recovery_state'] = {'halt': True}
    item.stop_core.return_value = False
    with pytest.raises(RuntimeError, match='cleanup'):
        await item.observe_once()
    item.publish_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_durable_halt_also_stops_pending_startup(monkeypatch):
    item, controller = running_host(monkeypatch)
    item.running = False
    item.owner.check_owner.return_value['recovery_state'] = {'halt': True}
    task = asyncio.create_task(asyncio.Event().wait())
    item.start_task = task
    item.hb.trading_core.strategy = None

    async def cleanup():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        item.start_task = None
        return True

    item.stop_core.side_effect = cleanup
    try:
        await item.observe_once()
        assert task.done() and task.cancelled()
        item.stop_core.assert_awaited_once()
        assert item.phase == 'halted'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_already_idle_halt_does_not_repeat_cleanup(monkeypatch):
    from test.hummingbot.cli.test_service_publication import host

    item = host(monkeypatch)
    item.owner.allow_new_orders = True
    item.owner.check_owner.return_value['recovery_state'] = {'halt': True}
    await item.observe_once()
    item.stop_core.assert_not_awaited()
    assert item.phase == 'halted' and not item.running
    assert not item.owner.allow_new_orders


@pytest.mark.asyncio
async def test_prior_halt_allows_recovery_to_cancel_restored_orders_before_stopping(monkeypatch):
    item, _ = running_host(monkeypatch)
    item.running = False
    item.hb.trading_core.strategy = None
    item.owner.check_owner.return_value['recovery_state'] = {'halt': True}
    item.owner.reconciling = True
    item.owner.allow_new_orders = False
    release = asyncio.Event()
    live_orders = {'restored-maker'}

    async def recover():
        await release.wait()
        live_orders.clear()
        item.owner.reconciling = False
        return False  # Prior HALT still prevents strategy admission.

    task = asyncio.create_task(recover())
    item.start_task = task

    async def cleanup():
        if item.start_task is not None:
            item.start_task.cancel()
            await asyncio.gather(item.start_task, return_exceptions=True)
        item.start_task = None
        return True

    item.stop_core.side_effect = cleanup
    try:
        await asyncio.sleep(0)
        await item.observe_once()
        assert not task.done(), 'HALT interrupted recovery before restored orders were cancelled'
        assert item.phase == 'starting' and not item.running
        item.stop_core.assert_not_awaited()
        release.set()
        assert await task is False
        await item.observe_once()
        assert not live_orders and not task.cancelled()
        assert item.phase == 'halted'
        item.stop_core.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
