import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
from hummingbot.core.bot_ownership import BotOwnership


@pytest.fixture
def readiness_startup():
    connector = SimpleNamespace(ready=False, in_flight_orders={}, cancel_all=AsyncMock())
    owner = BotOwnership.__new__(BotOwnership)
    owner.automatic_recovery = AutomaticOrderRecovery(owner)
    owner.config_file_path = 'test.yml'
    owner.allow_new_orders, owner.reconciling = True, True
    state = {}
    owner.check_owner = MagicMock(return_value={'recovery_state': state})
    owner.require_recovery = MagicMock()
    owner._open_orders = AsyncMock(return_value=[])
    owner._refresh_orders = AsyncMock()
    owner._check_intents = MagicMock()
    core = SimpleNamespace(strategy=SimpleNamespace(config=None),
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=MagicMock()),
                           markets_recorder=SimpleNamespace(),
                           connector_manager=SimpleNamespace(connectors={'okx': connector}))
    return SimpleNamespace(owner=owner, connector=connector, core=core, state=state)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['recover', 'stop', 'prior_halt'])
async def test_connector_outage_outlasts_reconciliation_deadline_without_inventing_halt(
        readiness_startup, monkeypatch, outcome):
    from hummingbot.core.order_submission_guard import SubmissionBlocked

    item = readiness_startup
    if outcome == 'prior_halt':
        item.state.update(halt=True, reason='existing unresolved send')
    wait_for = asyncio.wait_for

    async def short_deadline(awaitable, timeout):
        return await wait_for(awaitable, .02 if timeout == 30 else timeout)

    monkeypatch.setattr('hummingbot.core.bot_ownership.asyncio.wait_for', short_deadline)
    task = asyncio.create_task(item.owner.reconcile(item.core))
    try:
        # Three reconciliation budgets elapse while the connector reconnects.
        # Only shorten the deadline; the readiness waiter runs normally.
        await asyncio.sleep(.06)
        assert not task.done(), 'Connector readiness consumed the reconciliation deadline'
        assert not item.owner.allow_new_orders and item.owner.reconciling
        item.owner._open_orders.assert_not_awaited()
        item.connector.cancel_all.assert_not_awaited()
        item.owner._check_intents.assert_not_called()
        item.owner.require_recovery.assert_not_called()
        if outcome == 'stop':
            from hummingbot.cli.service import ServiceHost
            from hummingbot.client.config.runtime_config import RuntimeSettings

            host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                               asyncio.Event(), MagicMock())
            host.owner, host.hb = item.owner, SimpleNamespace(trading_core=item.core)
            host.start_task, host.phase, host.bootstrapped = task, 'starting', True
            item.core._is_running = True
            item.owner.shutdown_core = AsyncMock(return_value=True)
            for path, status in (('/startup', 200), ('/live', 200), ('/ready', 503)):
                assert (await host.health(SimpleNamespace(path=path))).status == status
            assert await wait_for(host.stop_core(), 1)
            assert task.cancelled() and host.start_task is None
            item.owner.shutdown_core.assert_awaited_once_with(item.core)
        else:
            item.connector.ready = True
            if outcome == 'prior_halt':
                with pytest.raises(SubmissionBlocked, match='prior unresolved'):
                    await wait_for(task, 1)
                assert item.state == {'halt': True, 'reason': 'existing unresolved send'}
            else:
                await wait_for(task, 1)
                assert item.owner.allow_new_orders
            item.owner._open_orders.assert_awaited_once()
            item.owner._check_intents.assert_called_once()
            item.core.trade_fill_db.assert_persistence_healthy.assert_called_once()
        assert item.owner.allow_new_orders is (outcome == 'recover')
        assert not item.owner.reconciling
        item.owner.require_recovery.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['open_orders_read', 'cancel_orders'])
async def test_readiness_fix_preserves_bounded_exchange_reconciliation(readiness_startup, monkeypatch, phase):
    item = readiness_startup
    item.connector.ready = True
    wait_for = asyncio.wait_for

    async def short_deadline(awaitable, timeout):
        return await wait_for(awaitable, .02 if timeout == 30 else timeout)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr('hummingbot.core.bot_ownership.asyncio.wait_for', short_deadline)
    if phase == 'open_orders_read':
        item.owner._open_orders.side_effect = blocked
    else:
        item.connector.in_flight_orders['order'] = SimpleNamespace(is_done=False)
        item.connector.cancel_all.side_effect = blocked
    with pytest.raises(asyncio.TimeoutError):
        await wait_for(item.owner.reconcile(item.core), 1)
    assert not item.owner.allow_new_orders and not item.owner.reconciling
    item.owner.require_recovery.assert_called_once()
    item.owner._check_intents.assert_not_called()


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
