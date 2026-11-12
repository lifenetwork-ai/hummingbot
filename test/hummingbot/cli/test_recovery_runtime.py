"""Stopped recovery never owns a trading loop or an exchange write transport."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli import recovery_runtime as module
from hummingbot.cli.recovery_runtime import RecoveryCoordinator, RecoveryReader
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.recovery import RecoveryBlocked


def coordinator(monkeypatch, *, action='recovery_check'):
    store = MagicMock()
    store.claim.return_value = {'id': 'op', 'action': action}
    store.snapshot.return_value = {'state': {'desired_state': 'stopped', 'control_version': 1, 'acknowledged_version': 1,
                                             'recovery_state': {'halt': True, 'reason': 'Strategy initialization/reconciliation failed; inspect sanitized logs.'}},
                                   'orders': [], 'fills': [], 'positions': [], 'bindings': []}
    monkeypatch.setattr(module, 'RecoveryStore', lambda _: store)
    core = SimpleNamespace(strategy=None, _is_running=False, connector_manager=SimpleNamespace(connectors={}))
    host = SimpleNamespace(store=None, owner=MagicMock(submission_tasks=set(), transport_tasks=set(), terminal_tasks=set()),
                           hb=SimpleNamespace(trading_core=core), running=False, start_task=None, error='RecoveryRequired', halt_requested=True,
                           funding=SimpleNamespace(credentials_changed=AsyncMock()),
                           managed_credentials=SimpleNamespace(configs={}, reload=AsyncMock()), log=MagicMock())
    instance = RecoveryCoordinator(host)
    monkeypatch.setattr(module, 'verify_exchange', AsyncMock(return_value={'verified': True}))
    return instance, store


@pytest.mark.asyncio
@pytest.mark.parametrize('beyond_page', [False, True])
async def test_pending_send_is_reported_before_position_replay_or_credential_loading(monkeypatch, beyond_page):
    instance, store = coordinator(monkeypatch)
    store.snapshot.return_value['orders'] = [] if beyond_page else [{'id': 'busy-order', 'creation_pending': True}]
    store.snapshot.return_value['pending_intents'] = beyond_page
    instance.host.managed_credentials = None
    replay = MagicMock(side_effect=AssertionError('Pending send cannot establish position proof'))
    monkeypatch.setattr(module, 'replay_positions', replay)
    try:
        await instance.run('op')
        assert store.fail.call_args.args[-1] == 'recovery_pending_intents'
        replay.assert_not_called()
        module.verify_exchange.assert_not_awaited()
        store.finish_reset.assert_not_called()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['recovery_check', 'recovery_reset'])
async def test_verified_completion_never_starts_trading(monkeypatch, action):
    instance, store = coordinator(monkeypatch, action=action)
    try:
        await instance.run('op')
        store.fail.assert_not_called()
        getattr(store, 'finish_check' if action == 'recovery_check' else 'finish_reset').assert_called_once()
        assert instance.host.running is False and instance.host.owner.allow_new_orders is False
        assert instance.host.hb.trading_core.strategy is None
        instance.host.funding.credentials_changed.assert_awaited_once()
        if action == 'recovery_reset':
            assert instance.host.error is None and instance.host.halt_requested is False
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_active_trading_core_blocks_without_contacting_exchange(monkeypatch):
    instance, store = coordinator(monkeypatch)
    instance.host.hb.trading_core.connector_manager.connectors['okx'] = object()
    try:
        await instance.run('op')
        assert store.fail.call_args.args[-1] == 'recovery_stop_incomplete'
        module.verify_exchange.assert_not_awaited()
        store.finish_check.assert_not_called()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_network_failure_sanitizes_error_and_preserves_halt(monkeypatch):
    instance, store = coordinator(monkeypatch)
    module.verify_exchange.side_effect = RuntimeError('CANARY-secret-transport-message')
    try:
        await instance.run('op')
        assert store.fail.call_args.args[-1] == 'recovery_check_unavailable'
        assert instance.host.halt_requested and instance.host.error == 'RecoveryRequired'
        assert 'CANARY' not in str(instance.host.log.mock_calls)
        store.finish_check.assert_not_called()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [OwnershipLost('lost'), asyncio.CancelledError()])
async def test_cancellation_and_ownership_loss_cannot_clear_halt(monkeypatch, error):
    instance, store = coordinator(monkeypatch)
    module.verify_exchange.side_effect = error
    try:
        with pytest.raises(type(error)):
            await instance.run('op')
        store.finish_check.assert_not_called()
        store.finish_reset.assert_not_called()
        assert instance.host.halt_requested
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_reader_restricts_path_and_never_exposes_write_interface():
    connector = MagicMock(name='okx')
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': []})
    reader = RecoveryReader(connector)
    assert not hasattr(reader, '_api_post')
    with pytest.raises(RecoveryBlocked, match='recovery_read_scope_invalid'):
        await reader._api_get(path_url='/api/v5/trade/order-algo')
    connector._api_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_spot_account_position_read_uses_registered_conservative_bucket():
    connector = MagicMock()
    connector.name = 'okx'
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': []})
    await RecoveryReader(connector)._api_get(path_url='/api/v5/account/positions')
    assert connector._api_get.call_args.kwargs['limit_id'] == '/api/v5/account/config'


@pytest.mark.asyncio
async def test_canceled_exchange_proof_closes_readers_without_db_repair(monkeypatch):
    instance, store = coordinator(monkeypatch)
    reader = SimpleNamespace(close=AsyncMock())

    async def cancel_proof(*args):
        instance.readers['okx'] = reader
        raise asyncio.CancelledError()

    module.verify_exchange.side_effect = cancel_proof
    try:
        with pytest.raises(asyncio.CancelledError):
            await instance.run('op')
        reader.close.assert_awaited_once()
        assert not instance.readers
        store.finish_reset.assert_not_called()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['recovery_check', 'recovery_reset'])
async def test_reviewed_reset_discards_old_automatic_scope_only_after_commit(monkeypatch, action):
    from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
    instance, store = coordinator(monkeypatch, action=action)
    old = AutomaticOrderRecovery(instance.host.owner)
    old.status.update(phase='settled_stopped', reason_code='session_authority_changed')
    old.resume_scope = ('old-session', 1)
    old.bootstrap_orders = {'old-order': object()}
    instance.host.owner.automatic_recovery = old
    try:
        await instance.run('op')
        store.fail.assert_not_called()
        current = instance.host.owner.automatic_recovery
        if action == 'recovery_reset':
            assert current.status['phase'] == 'idle' and current.resume_scope is None
            assert not current.bootstrap_orders
        else:
            assert current is old and current.resume_scope == ('old-session', 1)
        assert not instance.host.owner.allow_new_orders and not instance.host.running
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_failed_reset_never_discards_automatic_scope_or_reopens_admission(monkeypatch):
    from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
    instance, store = coordinator(monkeypatch, action='recovery_reset')
    old = AutomaticOrderRecovery(instance.host.owner)
    old.status.update(phase='settled_stopped')
    old.resume_scope = ('old-session', 1)
    instance.host.owner.automatic_recovery = old
    store.finish_reset.side_effect = IOError('uncertain commit')
    try:
        await instance.run('op')
        assert instance.host.owner.automatic_recovery is old
        assert old.resume_scope == ('old-session', 1) and instance.host.halt_requested
        assert not instance.host.owner.allow_new_orders
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['automatic', 'terminal'])
async def test_review_requires_all_proof_workers_to_be_drained(monkeypatch, kind):
    from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
    instance, store = coordinator(monkeypatch, action='recovery_reset')
    automatic = AutomaticOrderRecovery(instance.host.owner)
    instance.host.owner.automatic_recovery = automatic
    task = asyncio.create_task(asyncio.Event().wait())
    if kind == 'automatic':
        automatic.task = task
    else:
        instance.host.owner.terminal_tasks.add(task)
    try:
        await instance.run('op')
        assert store.fail.call_args.args[-1] == 'recovery_stop_incomplete'
        store.finish_reset.assert_not_called()
        module.verify_exchange.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await instance.close()


@pytest.mark.asyncio
async def test_bulk_finalization_runs_off_loop_and_physical_owner_check_stays_on_loop(monkeypatch):
    import threading
    instance, store = coordinator(monkeypatch)
    loop_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    checked = []

    def physical_check():
        checked.append(threading.get_ident())
        assert threading.get_ident() == loop_thread
        return {}

    def finish(owner, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(2)
        owner.check_owner()

    instance.host.owner.check_owner.side_effect = physical_check
    store.finish_check.side_effect = finish
    task = asyncio.create_task(instance.run('op'))
    try:
        while not entered.is_set():
            await asyncio.sleep(.001)
        # The owner loop can process other work while the DB proof is blocked.
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        await task
        assert checked and set(checked) == {loop_thread}
        store.fail.assert_not_called()
    finally:
        release.set()
        await instance.close()


@pytest.mark.asyncio
async def test_cancelled_bulk_finalization_drains_without_clearing_halt(monkeypatch):
    import threading
    instance, store = coordinator(monkeypatch, action='recovery_reset')
    entered, release = threading.Event(), threading.Event()
    committed = []

    def finish(owner, *args):
        entered.set()
        assert release.wait(2)
        owner.check_owner()
        committed.append(True)

    store.finish_reset.side_effect = finish
    task = asyncio.create_task(instance.run('op'))
    try:
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not committed and instance.host.halt_requested
    finally:
        release.set()
        await instance.close()
