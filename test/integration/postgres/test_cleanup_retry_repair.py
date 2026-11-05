from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture

import pytest

from hummingbot.core.credential_encryption import CredentialContext
from hummingbot.model.bot_state_store import ControlConflict

managed = managed_fixture


def blocked(managed, *, both=False):
    store, actor = loaded(managed, both=both)
    control = 2 if both else 1
    receipt = disconnect(store, actor, control=control)
    store.progress_disconnect(receipt["id"], "owner", 1, stage="action_required", reason_code="exchange_credentials_rejected",
                              operator_action_required=True)
    return store, actor, receipt


def test_retry_reschedules_same_root_fence_and_idempotent_receipt(managed):
    store, actor, root = blocked(managed)
    receipt = store.retry_disconnect(actor, root["id"], expected_version=1, expected_operation_version=1,
                                     expected_control_version=2, idempotency_key="retry", request_id="retry-request")
    assert receipt["action"] == "credentials_cleanup_retry" and receipt["result"]["cleanup_operation_id"] == root["id"]
    state = store.store.read()
    assert state["control_version"] == 2 and state["desired_state"] == "stopped"
    assert state["recovery_state"]["credential_operation"]["id"] == root["id"]
    assert not state["recovery_state"]["credential_operation"]["operator_action_required"]
    assert store.retry_disconnect(actor, root["id"], expected_version=1, expected_operation_version=1,
                                  expected_control_version=2, idempotency_key="retry", request_id="retry-replay") == receipt


def test_cleanup_key_repair_is_same_account_and_preserves_root_and_companion_scope(managed):
    store, actor, root = blocked(managed, both=True)
    cipher = managed[2]
    scope = dict(exchange="okx", connector="okx_perpetual", environment="demo", operation_id=root["id"],
                 expected_version=1, expected_control_version=3, expected_operation_version=1, idempotency_key="repair")
    envelope = cipher.encrypt({"api_key": "repair-canary-key", "secret_key": "repair-canary-secret", "passphrase": "repair-canary-pass"},
                              CredentialContext(store.bot_id, "okx", "okx_perpetual", "demo", 2, "www:account"))
    receipt = store.accept_cleanup_key(actor, **scope, account_id="www:account", envelope=envelope, request_id="repair-request")
    assert receipt["action"] == "credentials_cleanup_key"
    operation = store.store.read()["recovery_state"]["credential_operation"]
    assert operation["id"] == root["id"] and operation["affected_versions"] == {"okx": 1, "okx_perpetual": 2}
    assert store.load("okx", "okx", "demo")["state"] == "revoking"
    assert store.load("okx", "okx_perpetual", "demo")["version"] == 2
    assert "repair-canary" not in str(receipt)


def test_repair_rechecks_versions_and_rejects_wrong_account_without_altering_saved_key(managed):
    store, actor, root = blocked(managed)
    scope = dict(exchange="okx", connector="okx", environment="demo", operation_id=root["id"],
                 expected_version=1, expected_control_version=2, expected_operation_version=1, idempotency_key="repair")
    cipher = managed[2]
    before = store.load("okx", "okx", "demo")
    envelope = cipher.encrypt({"api_key": "candidate"}, CredentialContext(store.bot_id, "okx", "okx", "demo", 2, "www:different"))
    with pytest.raises(ControlConflict) as error:
        store.accept_cleanup_key(actor, **scope, account_id="www:different", envelope=envelope, request_id="rejected")
    assert error.value.code == "exchange_account_mismatch"
    assert store.load("okx", "okx", "demo") == before
    with pytest.raises(ControlConflict) as error:
        store.prepare_cleanup_key(actor, **{**scope, "expected_operation_version": 0})
    assert error.value.code == "operation_version_changed"


def test_retry_receipt_completes_with_parent_and_original_actor_after_role_change(managed):
    from sqlalchemy import select, update

    from hummingbot.model.admin_access import AdminAccount, AdminAuditEvent
    store, actor, root = blocked(managed)
    child = store.retry_disconnect(actor, root['id'], expected_version=1, expected_operation_version=1,
                                   expected_control_version=2, idempotency_key='retry-complete', request_id='retry-request')
    with store.engine.begin() as connection:
        connection.execute(update(AdminAccount).where(AdminAccount.id == actor.id).values(role='read', active=False, version=3))
    proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True, consumers_detached=True,
                 observed_at=float(store.store.read()['database_now']), owner_generation=1)
    assert store.progress_disconnect(root['id'], 'owner', 1, stage='detaching', evidence=proof)
    assert store.finish_disconnect(root['id'], 'owner', 1, evidence=proof)
    completed = store.operation(child['id'])
    assert completed['status'] == 'succeeded'
    assert completed['result']['cleanup_operation_id'] == root['id']
    assert completed['result']['stage'] == 'cleanup_completed'
    with store.engine.connect() as connection:
        audit = connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.target_id == child['id'],
                                                                 AdminAuditEvent.outcome == 'succeeded')).mappings().one()
        assert audit['actor_id'] == actor.id and audit['actor_role'] == 'write'
