"""Ownership can change while any cleanup readback dependency is awaited."""
from decimal import Decimal
from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture
from test.integration.postgres.test_disconnect_coordinator import Runtime, positions
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from hummingbot.cli.credential_disconnect import DisconnectCoordinator
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.model.bot_state import BotState

managed = managed_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['cancel', 'close', 'verification_cancel', 'detach'])
@pytest.mark.parametrize('raise_native_loss', [False, True], ids=['absent_snapshot', 'native_exception'])
async def test_cleanup_lost_owner_stops_without_generic_error_and_replacement_resumes(managed, monkeypatch, caplog, boundary, raise_native_loss):
    store, actor = loaded(managed, both=True)
    positions(store)
    root = disconnect(store, actor, control=2)
    runtime = Runtime(store, {('BTC-USDT', 'LONG'): Decimal('.1'), ('BTC-USDT', 'SHORT'): Decimal('.1')})
    method = {'cancel': 'cancel_orders', 'close': 'close_position',
              'verification_cancel': 'cancel_orders', 'detach': 'detach'}[boundary]
    original, calls, losses = getattr(runtime, method), [], []

    async def change_owner(*args):
        await original(*args)
        calls.append(method)
        target = 2 if boundary == 'verification_cancel' else 1
        if len(calls) == target and not losses:
            with store.engine.begin() as connection:
                connection.execute(update(BotState).values(owner_uuid='replacement', owner_generation=2))
            losses.append(boundary)
            if raise_native_loss:
                raise OwnershipLost('Injected native ownership loss at cleanup readback')

    monkeypatch.setattr(runtime, method, change_owner)
    coordinator = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='owner', generation=1), runtime)
    assert not await coordinator.run(root['id'])
    assert losses == [boundary]
    records = [r for r in caplog.records if getattr(r, 'event', None) == 'credential_cleanup_incomplete']
    assert len(records) == 1 and records[0].reason_code == 'owner_unavailable'
    assert records[0].error_type != 'TypeError'
    assert store.operation(root['id'])['status'] != 'succeeded'
    assert store.load('okx', 'okx', 'demo')['envelope'] is not None
    assert not store.store.read()['recovery_state']['credential_operation'].get('operator_action_required')
    assert len([call for call in runtime.calls if isinstance(call, tuple)]) == {
        'cancel': 0, 'close': 1, 'verification_cancel': 2, 'detach': 2}[boundary]
    replacement = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='replacement', generation=2), runtime)
    assert await replacement.run(root['id'])
    assert sorted(call for call in runtime.calls if isinstance(call, tuple)) == [('BUY', Decimal('.1')), ('SELL', Decimal('.1'))]
    assert store.operation(root['id'])['status'] == 'succeeded' and not runtime.actual
