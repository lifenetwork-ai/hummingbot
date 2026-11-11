"""A cold HALT is an operator state, not permission to restart trading."""
from test.hummingbot.cli.test_service_publication import host
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_cold_halt_keeps_owner_http_and_observer_without_starting_strategy(monkeypatch):
    item = host(monkeypatch)
    row = item.owner.check_owner.return_value
    row.update(desired_state='running', recovery_state={'halt': True, 'runtime_active': True,
               'shutdown_incomplete': True, 'reason': 'An order submission has an unresolved outcome; no automatic resubmission is allowed.'})
    item.applied_version = None
    item.hb.trading_core._is_running = False
    item.start_strategy = AsyncMock(return_value=False)
    item.validate_selection = AsyncMock()
    item.publish_status = AsyncMock()
    monkeypatch.setattr('hummingbot.core.mm_liquidity.capture_liquidity', lambda value: None)

    for _ in range(4):
        await item.observe_once()
        assert item.phase == 'halted' and not item.running
        assert item.start_task is None
        assert not item.owner.allow_new_orders
        assert (await item.health(SimpleNamespace(path='/startup'))).status == 200
        assert (await item.health(SimpleNamespace(path='/live'))).status == 200
        assert (await item.health(SimpleNamespace(path='/ready'))).status == 503
    item.owner.prepare.assert_not_called()
    item.validate_selection.assert_not_awaited()
    item.start_strategy.assert_not_awaited()
    assert item.publish_status.await_count == 4
    assert row['recovery_state']['halt'] and row['recovery_state']['shutdown_incomplete']
    # Stop must still be acknowledged so an admin can run Check; no implicit reset.
    row.update(desired_state='stopped', control_version=1)
    await item.observe_once()
    assert item.phase == 'halted' and item.applied_version == 1
    item.owner.prepare.assert_not_called()
    assert row['recovery_state']['halt']
    # Only a separately confirmed reset followed by Start can admit a strategy.
    row.update(desired_state='running', control_version=3, recovery_state={})
    await item.observe_once()
    assert item.start_task is not None
    await item.start_task
    item.owner.prepare.assert_called_once_with(3, 'test.yml')
    item.start_strategy.assert_awaited_once()
