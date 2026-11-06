"""Transaction-level cleanup races; no exchange or Google/GCP calls."""
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_cleanup_retry_repair import blocked
from test.integration.postgres.test_credential_store import managed as managed_fixture
from threading import Barrier

import pytest
from sqlalchemy import select, update

from hummingbot.core.admin_identity import AccessDenied
from hummingbot.core.credential_encryption import CredentialContext
from hummingbot.model.admin_access import AdminAccount, AdminAuditEvent
from hummingbot.model.bot_state_store import ControlConflict

managed = managed_fixture


@pytest.mark.parametrize('race', ['same_retry_key', 'different_retry_keys', 'retry_repair', 'repair_repair'])
def test_concurrent_cleanup_requests_commit_one_transition_and_one_child(managed, race):
    store, actor, root = blocked(managed)
    envelope = managed[2].encrypt({'api_key': 'race-key', 'secret_key': 'race-secret', 'passphrase': 'race-pass'},
                                  CredentialContext(store.bot_id, 'okx', 'okx', 'demo', 2, 'www:account'))
    before = store.load('okx', 'okx', 'demo')
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        key = 'same-retry' if race == 'same_retry_key' else 'request-' + str(index)
        request = dict(expected_version=1, expected_control_version=2, expected_operation_version=1,
                       idempotency_key=key, request_id='race-' + str(index))
        try:
            if race == 'repair_repair' or race == 'retry_repair' and index == 1:
                result = store.accept_cleanup_key(actor, exchange='okx', connector='okx', environment='demo',
                                                  operation_id=root['id'], account_id='www:account', envelope=envelope, **request)
            else:
                result = store.retry_disconnect(actor, root['id'], **request)
            return 'accepted', result
        except ControlConflict as error:
            return 'conflict', error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, index) for index in range(2)]
        results = [future.result(timeout=20) for future in futures]
    accepted = [result for status, result in results if status == 'accepted']
    conflicts = [result for status, result in results if status == 'conflict']
    assert len({row['id'] for row in accepted}) == 1
    if race == 'same_retry_key':
        assert len(accepted) == 2 and accepted[0] == accepted[1] and not conflicts
    else:
        assert len(accepted) == 1 and conflicts == ['operation_version_changed']
    state = store.store.read()
    operation = state['recovery_state']['credential_operation']
    repaired = accepted[0]['action'] == 'credentials_cleanup_key'
    after = store.load('okx', 'okx', 'demo')
    assert state['control_version'] == 2 and state['desired_state'] == 'stopped'
    assert operation['id'] == root['id'] and operation['transition'] == 2
    assert operation['stage'] == 'waiting_for_owner' and not operation['operator_action_required']
    assert after['state'] == 'revoking' and after['version'] == (2 if repaired else 1)
    assert operation['affected_versions'] == {'okx': after['version']}
    assert after['envelope'] == (envelope.data if repaired else before['envelope'])
    with store.engine.connect() as connection:
        events = connection.execute(select(AdminAuditEvent).where(
            AdminAuditEvent.operation_id == root['id'],
            AdminAuditEvent.action.in_(['credentials_cleanup_retry', 'credentials_cleanup_key']))).mappings().all()
    assert len(events) == 1 and events[0]['target_id'] == accepted[0]['id']


@pytest.mark.parametrize('change', [{'active': False}, {'role': 'read'}], ids=['disabled', 'downgraded'])
def test_prepared_cleanup_repair_rechecks_role_before_committing_key(managed, change):
    store, actor, root = blocked(managed)
    scope = dict(exchange='okx', connector='okx', environment='demo', operation_id=root['id'], expected_version=1,
                 expected_control_version=2, expected_operation_version=1, idempotency_key='prepared-repair')
    store.prepare_cleanup_key(actor, **scope)
    envelope = managed[2].encrypt({'api_key': 'prepared-key'},
                                  CredentialContext(store.bot_id, 'okx', 'okx', 'demo', 2, 'www:account'))
    before_key, before_state, before_receipt = store.load('okx', 'okx', 'demo'), store.store.read(), store.operation(root['id'])
    with store.engine.begin() as connection:
        connection.execute(update(AdminAccount).where(AdminAccount.id == actor.id).values(**change, version=actor.account_version + 1))
    with pytest.raises(AccessDenied):
        store.accept_cleanup_key(actor, **scope, envelope=envelope, account_id='www:account', request_id='denied-repair')
    assert store.load('okx', 'okx', 'demo') == before_key
    after_state = store.store.read()
    assert after_state['control_version'] == before_state['control_version']
    assert after_state['recovery_state'] == before_state['recovery_state']
    assert store.operation(root['id']) == before_receipt
    with store.engine.connect() as connection:
        assert not connection.execute(select(AdminAuditEvent.id).where(
            AdminAuditEvent.operation_id == root['id'], AdminAuditEvent.action == 'credentials_cleanup_key')).first()
