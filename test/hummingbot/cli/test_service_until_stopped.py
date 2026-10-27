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
