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
    host = SimpleNamespace(store=None, owner=MagicMock(submission_tasks=set(), transport_tasks=set()),
                           hb=SimpleNamespace(trading_core=core), running=False, start_task=None, error='RecoveryRequired', halt_requested=True,
                           funding=SimpleNamespace(credentials_changed=AsyncMock()),
                           managed_credentials=SimpleNamespace(configs={}, reload=AsyncMock()), log=MagicMock())
    instance = RecoveryCoordinator(host)
    monkeypatch.setattr(module, 'verify_exchange', AsyncMock(return_value={'verified': True}))
    return instance, store


@pytest.mark.asyncio
async def test_pending_send_is_reported_before_position_replay_or_credential_loading(monkeypatch):
    instance, store = coordinator(monkeypatch)
    store.snapshot.return_value['orders'] = [{'id': 'busy-order', 'creation_pending': True}]
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
