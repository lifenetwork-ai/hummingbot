import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
from hummingbot.core.bot_ownership import BotOwnership


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,halt', [('readiness', False), ('open_orders_read', False), ('cancel_orders', True)])
async def test_reconciliation_cancel_distinguishes_readonly_startup_from_uncertain_exchange_effects(phase, halt):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    order = SimpleNamespace(is_done=False)
    connector = SimpleNamespace(ready=phase != 'readiness', in_flight_orders={'order': order} if halt else {},
                                cancel_all=blocked)
    owner = BotOwnership.__new__(BotOwnership)
    owner.automatic_recovery = AutomaticOrderRecovery(owner)
    owner.config_file_path = 'test.yml'
    owner.allow_new_orders = True
    owner.reconciling = True
    owner.check_owner = MagicMock(return_value={'recovery_state': {}})
    owner.require_recovery = MagicMock()
    owner._open_orders = blocked if phase == 'open_orders_read' else AsyncMock(return_value=[])
    owner._refresh_orders = AsyncMock()
    core = SimpleNamespace(strategy=SimpleNamespace(config=None), trade_fill_db=MagicMock(),
                           connector_manager=SimpleNamespace(connectors={'okx': connector}))
    task = asyncio.create_task(owner.reconcile(core))
    if phase == 'readiness':
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    else:
        await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not owner.allow_new_orders and not owner.reconciling
    assert owner.require_recovery.call_count == int(halt)


@pytest.mark.asyncio
@pytest.mark.parametrize('config_type,expected', [('mm-session', True), ('script', False)])
async def test_managed_runtime_baseline_does_not_require_an_enabled_drawdown_policy(config_type, expected):
    owner = BotOwnership.__new__(BotOwnership)
    owner.automatic_recovery = AutomaticOrderRecovery(owner)
    owner.config_file_path = 'life_mm_profiles.yaml'
    owner.check_owner = MagicMock(return_value={'config_type': config_type, 'recovery_state': {}})
    owner._mutate_recovery = MagicMock()
    owner._check_intents = MagicMock()
    recorder = SimpleNamespace(ensure_runtime_checkpoint=MagicMock(return_value=True))
    core = SimpleNamespace(strategy=SimpleNamespace(config=None, controllers={}),
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=MagicMock()),
                           markets_recorder=recorder, connector_manager=SimpleNamespace(connectors={}))
    await owner.reconcile(core)
    assert recorder.ensure_runtime_checkpoint.call_count == int(expected)
    assert owner.automatic_recovery.status['cold_recovery_supported'] is expected
    assert owner.allow_new_orders


@pytest.mark.asyncio
@pytest.mark.parametrize('unresolved', [False, True])
async def test_explicit_start_retires_only_a_fully_settled_previous_recovery_scope(unresolved):
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    owner = BotOwnership.__new__(BotOwnership)
    old = owner.automatic_recovery = AutomaticOrderRecovery(owner)
    old.status.update(phase='settled_stopped', reason_code='current_control_blocks_resume')
    old.resume_scope = ('previous-session', 1)
    owner.config_file_path = 'life_mm_profiles.yaml'
    owner.check_owner = MagicMock(return_value={'config_type': 'mm-session', 'recovery_state': {}})
    owner._mutate_recovery = MagicMock()
    owner.require_recovery = MagicMock()
    owner._check_intents = MagicMock(side_effect=SubmissionBlocked('Unresolved order') if unresolved else None)
    core = SimpleNamespace(strategy=SimpleNamespace(config=None, controllers={}),
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=MagicMock()),
                           markets_recorder=SimpleNamespace(ensure_runtime_checkpoint=MagicMock(return_value=True)),
                           connector_manager=SimpleNamespace(connectors={}))
    if unresolved:
        with pytest.raises(SubmissionBlocked):
            await owner.reconcile(core)
        assert owner.automatic_recovery is old and old.active
        assert not owner.allow_new_orders
    else:
        await owner.reconcile(core)
        assert not owner.automatic_recovery.active, 'The old session still revokes the new controller admission'
        assert owner.automatic_recovery.resume_scope is None
        assert owner.allow_new_orders
