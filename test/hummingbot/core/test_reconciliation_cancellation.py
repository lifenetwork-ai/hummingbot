import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.startup_order_reconciliation import UntrackedOpenOrders


@pytest.fixture
def readiness_startup():
    connector = SimpleNamespace(name='okx', ready=False, in_flight_orders={}, cancel_all=AsyncMock())
    owner = BotOwnership(MagicMock(), 'test')
    owner.config_file_path = 'test.yml'
    owner.allow_new_orders, owner.reconciling = True, True
    state = {}
    owner.check_owner = MagicMock(return_value={'recovery_state': state})
    owner._mutate_recovery = MagicMock(side_effect=lambda change: change(state))
    owner.require_recovery = MagicMock()
    owner._open_orders = AsyncMock(return_value=[])
    owner._refresh_orders = AsyncMock()
    owner._check_intents = MagicMock()
    core = SimpleNamespace(strategy=SimpleNamespace(config=None),
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=MagicMock(), get_new_session=MagicMock()),
                           markets_recorder=SimpleNamespace(),
                           connector_manager=SimpleNamespace(connectors={'okx': connector}))
    return SimpleNamespace(owner=owner, connector=connector, core=core, state=state)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['ready', 'stop', 'untracked', 'read_failed'])
async def test_startup_reads_both_open_order_lists_before_touching_orders(readiness_startup, outcome):
    item = readiness_startup
    item.connector.ready = True
    perpetual = SimpleNamespace(name='okx_perpetual', ready=True, in_flight_orders={}, cancel_all=AsyncMock())
    item.core.connector_manager.connectors['okx_perpetual'] = perpetual
    active = set()
    started = asyncio.Event()
    first, second = asyncio.Event(), asyncio.Event()

    async def open_orders(connector):
        active.add(connector.name)
        if len(active) == 2:
            started.set()
        try:
            await (first if connector is item.connector else second).wait()
            if connector is item.connector:
                if outcome == 'untracked':
                    return [{'clOrdId': 'unknown'}]
                if outcome == 'read_failed':
                    raise OSError('order list read failed')
            return []
        finally:
            active.remove(connector.name)

    item.owner._open_orders.side_effect = open_orders
    task = asyncio.create_task(item.owner.reconcile(item.core))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not item.owner.allow_new_orders
        item.owner._refresh_orders.assert_not_awaited()
        if outcome == 'stop':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            first.set()
            if outcome == 'ready':
                await asyncio.sleep(0)
                item.owner._refresh_orders.assert_not_awaited()
                second.set()
                await task
            else:
                error = UntrackedOpenOrders if outcome == 'untracked' else OSError
                with pytest.raises(error):
                    await task
        assert not active
        assert item.owner.allow_new_orders is (outcome == 'ready')
        assert item.owner._refresh_orders.await_count == (2 if outcome == 'ready' else 0)
        assert item.owner.require_recovery.call_count == int(outcome in ('untracked', 'read_failed'))
        assert ('startup_untracked_orders' in item.state) is (outcome == 'untracked')
        item.connector.cancel_all.assert_not_awaited()
        perpetual.cancel_all.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_refreshes_later_order_list_after_waiting_for_cancel_visibility(readiness_startup, monkeypatch):
    item = readiness_startup
    item.connector.ready = True
    perpetual = SimpleNamespace(ready=True, in_flight_orders={})
    item.core.connector_manager.connectors['okx_perpetual'] = perpetual
    early_read = asyncio.Event()
    late_order = {'clOrdId': 'untracked-after-spot-proof'}

    async def open_orders(connector):
        if connector is item.connector:
            return [{'clOrdId': 'old-spot-cancel'}]
        if early_read.is_set():
            return [late_order]
        early_read.set()
        return []

    async def visibility(owner, manager, connector, orders, known):
        if connector is item.connector:
            await asyncio.wait_for(early_read.wait(), timeout=5)
            return True
        assert orders == [late_order]
        raise UntrackedOpenOrders({'reason_code': 'untracked_order_identity_unproven'})

    item.owner._open_orders.side_effect = open_orders
    monkeypatch.setattr('hummingbot.core.bot_ownership.reconcile_open_order_visibility', visibility)
    with pytest.raises(UntrackedOpenOrders):
        await item.owner.reconcile(item.core)
    assert item.owner._open_orders.await_count == 3
    item.owner._refresh_orders.assert_not_awaited()
    assert not item.owner.allow_new_orders
    assert item.state['startup_untracked_orders']['reason_code'] == 'untracked_order_identity_unproven'


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
    owner = BotOwnership(MagicMock(), 'test')
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
    owner = BotOwnership(MagicMock(), 'test')
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
    owner = BotOwnership(MagicMock(), 'test')
    old = owner.automatic_recovery
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
