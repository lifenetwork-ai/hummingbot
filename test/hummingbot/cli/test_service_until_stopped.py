from random import Random
from test.hummingbot.cli.test_service_availability import host
from test.hummingbot.core.test_mm_until_stopped import anchors, catalogue, request
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.mm_session import MMSession


@pytest.mark.asyncio
async def test_halt_blocks_random_rollover_when_unlimited_session_has_no_overall_deadline(monkeypatch):
    session = MMSession.create(await catalogue(), request(), rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    row = dict(control_version=1, config_type='mm-session', config_file_path='life_mm_profiles.yaml',
               desired_state='running', recovery_state={'halt': True}, mm_session=session.model_dump(mode='json'), database_now=1601)
    service = host()
    service.owner, service.store = MagicMock(), MagicMock()
    service.owner.try_acquire.return_value = True
    service.owner.check_owner.return_value = row
    service.applied_version = 1
    service.applied_mm_identity = (session.id, session.generation)
    service.poll_reconciliation = AsyncMock()
    service.poll_funding = MagicMock()
    service.stop_core = AsyncMock(return_value=True)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)
    await service.observe_once()
    assert service.phase == 'halted'
    service.store.update_mm_session.assert_not_called()
    assert service.mm_session.profile_id == session.profile_id


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,pending,should_advance', [
    ('until_stopped', True, False), ('until_stopped', False, True), ('timed', True, True)])
async def test_random_rollover_waits_for_startup_reconciliation_but_overall_expiry_does_not(monkeypatch, mode, pending, should_advance):
    import asyncio

    from hummingbot.model.bot_state_store import ControlConflict
    selected = request() if mode == 'until_stopped' else request().model_copy(update={'duration_mode': 'timed', 'duration_seconds': 120})
    session = MMSession.create(await catalogue(), selected, rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    row = dict(control_version=1, config_type='mm-session', config_file_path='life_mm_profiles.yaml',
               desired_state='running', recovery_state={}, mm_session=session.model_dump(mode='json'), database_now=1601)
    service = host()
    service.owner, service.store = MagicMock(), MagicMock()
    service.owner.try_acquire.return_value = True
    service.owner.check_owner.return_value = row
    service.applied_version, service.applied_mm_identity = 1, (session.id, session.generation)
    service.start_task = asyncio.create_task(asyncio.Event().wait() if pending else asyncio.sleep(0))
    if not pending:
        await service.start_task
    service.store.update_mm_session.side_effect = ControlConflict('End test after observing the scheduled advance')
    service.publish_status = MagicMock()
    service.poll_reconciliation = AsyncMock()
    service.poll_funding = service.poll_outflow = MagicMock()
    service.stop_core = AsyncMock(return_value=True)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)
    try:
        await service.observe_once()
        assert service.store.update_mm_session.call_count == int(should_advance)
        service.stop_core.assert_not_awaited()
        service.owner.require_recovery.assert_not_called()
        assert not session.permits(session_id=session.id, generation=0, now=1601)
    finally:
        service.start_task.cancel()
        await asyncio.gather(service.start_task, return_exceptions=True)


def test_random_rollover_also_waits_for_controller_account_and_reference_initialization():
    from types import SimpleNamespace
    service = host()
    controller = SimpleNamespace(_startup_stage='leverage_change')
    service._mm_controllers = lambda: [controller]
    assert service.mm_startup_pending()
    controller._startup_stage = 'reference_books'
    assert service.mm_startup_pending()
    controller._startup_stage = 'ready'
    assert not service.mm_startup_pending()
