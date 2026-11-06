"""Steady admission uses one fresh strict check; state transitions retain both."""
from test.hummingbot.cli.test_mm_service import setup_host
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest


async def ready_host():
    item, controllers, provider = await setup_host()
    item.owner.listing_bootstrap = item.owner.volatility_checkpoints = None

    def persist(**fields):
        fields['change'](item.mm_session, 1000)
        return item.mm_session

    item.store.update_mm_session.side_effect = persist
    assert await item.admit_mm_session()
    item.store.update_mm_session.reset_mock()
    return item, controllers, provider


@pytest.mark.asyncio
async def test_steady_running_admission_does_one_reference_and_exposure_pass():
    item, controllers, _ = await ready_host()
    for controller in controllers.values():
        controller.update_reference = MagicMock(wraps=controller.update_reference)
        controller.exposure_within_caps = MagicMock(wraps=controller.exposure_within_caps)
    assert await item.admit_mm_session()
    assert all(controller.update_reference.call_count == 1 for controller in controllers.values())
    assert all(controller.exposure_within_caps.call_count == 1 for controller in controllers.values())
    item.store.update_mm_session.assert_not_called()


@pytest.mark.asyncio
async def test_steady_path_retains_strict_readiness_without_bootstrap_permission():
    item, controllers, _ = await ready_host()
    for controller in controllers.values():
        controller.revoke_session()
    item.mm_runtime_ready = MagicMock(side_effect=lambda **kwargs: kwargs.get('allow_bootstrap_start', False))
    assert not await item.admit_mm_session()
    assert item.mm_runtime_ready.call_args_list == [call()]
    assert all(not controller._session_admitted for controller in controllers.values())


@pytest.mark.asyncio
async def test_next_admission_reads_current_provider_and_recovers_without_caching():
    item, controllers, provider = await ready_host()
    assert await item.admit_mm_session()
    provider.ready = False
    assert not await item.admit_mm_session()
    provider.ready = True
    assert await item.admit_mm_session()
    item.store.update_mm_session.assert_not_called()


@pytest.mark.asyncio
async def test_preparing_keeps_both_checks_and_cannot_admit_after_data_changes_during_commit():
    item, controllers, provider = await setup_host()
    item.owner.listing_bootstrap = item.owner.volatility_checkpoints = None
    item.mm_runtime_ready = MagicMock(wraps=item.mm_runtime_ready)

    def persist(**fields):
        fields['change'](item.mm_session, 1000)
        provider.ready = False
        return item.mm_session

    item.store.update_mm_session.side_effect = persist
    assert not await item.admit_mm_session()
    assert item.mm_runtime_ready.call_args_list == [call(allow_bootstrap_start=True), call()]
    assert item.mm_session.status == 'running'
    assert all(not controller._session_admitted for controller in controllers.values())


@pytest.mark.parametrize('qualified', [False, True])
@pytest.mark.asyncio
async def test_bootstrap_path_keeps_qualification_and_both_readiness_checks(qualified):
    item, _, _ = await ready_host()
    item.owner.listing_bootstrap = SimpleNamespace(sync_qualification=MagicMock(return_value=qualified),
                                                   needs_start=lambda: False)
    item.mm_runtime_ready = MagicMock(wraps=item.mm_runtime_ready)
    assert await item.admit_mm_session() is qualified
    assert item.mm_runtime_ready.call_args_list == ([call(allow_bootstrap_start=True), call()] if qualified else [])
    item.store.update_mm_session.assert_not_called()


@pytest.mark.parametrize('status', ['stopped', 'expired', 'stopping'])
@pytest.mark.asyncio
async def test_inactive_sessions_cannot_use_steady_admission(status):
    item, _, _ = await ready_host()
    item.mm_session.status = status
    item.mm_runtime_ready = MagicMock(return_value=True)
    assert not await item.admit_mm_session()
    item.mm_runtime_ready.assert_not_called()
