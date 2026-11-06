"""Shutdown failures retain durable truth and escape to the engine restart fence."""
from decimal import Decimal
from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture
from test.integration.postgres.test_disconnect_coordinator import Runtime, positions
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from hummingbot.cli.credential_disconnect import DisconnectCoordinator
from hummingbot.connector.credential_adapters import CredentialAdapterError
from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.bot_state import BotState

managed = managed_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['before_removal', 'after_commit', 'during_dependency_failure'])
async def test_cleanup_shutdown_failure_retains_truth_and_recovers_after_restart(managed, monkeypatch, boundary):
    store, actor = loaded(managed, both=True)
    positions(store)
    root = disconnect(store, actor, control=2)
    runtime = Runtime(store, {('BTC-USDT', 'LONG'): Decimal('.1'), ('BTC-USDT', 'SHORT'): Decimal('.1')})
    shutdown = runtime.shutdown
    stop_attempts, failures = [], [True]

    async def failing_shutdown():
        stop_attempts.append('stop')
        if failures[0] and (boundary != 'after_commit' or len(stop_attempts) >= 2):
            raise CredentialAdapterError('cleanup_runtime_stop_incomplete', status=409)
        await shutdown()

    monkeypatch.setattr(runtime, 'shutdown', failing_shutdown)
    if boundary == 'during_dependency_failure':
        cancel = runtime.cancel_orders

        async def unavailable():
            if failures[0]:
                raise CredentialAdapterError('exchange_unavailable', status=503)
            await cancel()

        monkeypatch.setattr(runtime, 'cancel_orders', unavailable)
    coordinator = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='owner', generation=1), runtime)
    with pytest.raises(CredentialAdapterError, match='cleanup_runtime_stop_incomplete'):
        await coordinator.run(root['id'])
    receipt = store.operation(root['id'])
    if boundary == 'after_commit':
        assert receipt['status'] == 'succeeded' and store.load('okx', 'okx', 'demo')['envelope'] is None
        assert 'credential_operation' not in store.store.read()['recovery_state']
    else:
        reason = 'exchange_unavailable' if boundary == 'during_dependency_failure' else 'cleanup_runtime_stop_incomplete'
        assert receipt['status'] == 'applying' and receipt['result']['reason_code'] == reason
        assert receipt['result']['operator_action_required'] is (boundary == 'before_removal')
        assert store.load('okx', 'okx', 'demo')['envelope'] is not None
        store.retry_disconnect(actor, root['id'], expected_version=1, expected_control_version=3,
                               expected_operation_version=receipt['result']['operation_version'],
                               idempotency_key='reviewed-retry', request_id='restart-retry')
    failures[0] = False
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid='replacement', owner_generation=2))
    replacement = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='replacement', generation=2), runtime)
    assert await replacement.run(root['id']) is (boundary != 'after_commit')
    assert sorted(call for call in runtime.calls if isinstance(call, tuple)) == [('BUY', Decimal('.1')), ('SELL', Decimal('.1'))]
    assert not runtime.actual and store.store.read()['desired_state'] == 'stopped'
    assert store.load('okx', 'okx', 'demo')['envelope'] is None
    assert store.load('okx', 'okx_perpetual', 'demo')['envelope'] is not None
    with store.engine.connect() as connection:
        events = connection.execute(select(AdminAuditEvent).where(
            AdminAuditEvent.operation_id == root['id'], AdminAuditEvent.action == 'credentials_disconnect')).mappings().all()
    assert sorted(row['outcome'] for row in events) == ['accepted', 'succeeded']
