"""Freshly authorized retry/repair requests continue one durable cleanup fence."""
import uuid

from sqlalchemy import func, select, update

from hummingbot.core.credential_encryption import CredentialEnvelope
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential


def payload(store, actor, operation_id, *, action, expected_version, expected_control_version, expected_operation_version,
            exchange=None, connector=None, environment=None):
    parent = store.operation(operation_id)
    if parent is None or parent["action"] != "credentials_disconnect":
        raise ControlConflict("Read the active cleanup operation first", code="cleanup_operation_changed")
    original = parent["payload"]
    if type(expected_operation_version) is not int or expected_operation_version < 0:
        raise ValueError("Invalid cleanup operation version")
    scope = store.set_payload(actor, exchange=exchange or original["exchange"], connector=connector or original["connector"],
                              environment=environment or original["environment"], expected_version=expected_version, expected_control_version=expected_control_version)
    return {**scope, "action": action, "cleanup_operation_id": operation_id, "expected_operation_version": expected_operation_version}


def blocked_scope(store, connection, state, request):
    operation = dict((state["recovery_state"] or {}).get("credential_operation") or {})
    if operation.get("id") != request["cleanup_operation_id"] or operation.get("action") != "credentials_disconnect":
        raise ControlConflict("Cleanup operation changed", code="cleanup_operation_changed")
    if state["control_version"] != request["expected_control_version"]:
        raise ControlConflict("Control version changed", code="control_version_changed")
    if operation.get("transition", 0) != request["expected_operation_version"]:
        raise ControlConflict("Cleanup progress changed", code="operation_version_changed")
    if operation.get("stage") not in ("action_required", "waiting_for_dependency"):
        raise ControlConflict("Cleanup is already working", code="credential_operation_in_progress")
    if operation["exchange"] != request["exchange"] or operation["environment"] != request["environment"]:
        raise ControlConflict("Cleanup scope changed", code="exchange_account_mismatch")
    saved = connection.execute(select(ExchangeCredential).where(*store.scope(
        request["exchange"], request["connector"], request["environment"]))).mappings().first()
    if (saved is None or operation["affected_versions"].get(request["connector"]) != request["expected_version"]
            or saved["version"] != request["expected_version"]):
        raise ControlConflict("Connection version changed", code="credential_version_conflict")
    return operation, dict(saved)


def schedule(store, connection, state, operation):
    version = operation.get("transition", 0) + 1
    operation = {**operation, "transition": version, "stage": "waiting_for_owner", "operator_action_required": False,
                 "reason_code": None, "next_retry_at": 0}
    connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(
        recovery_state={**state["recovery_state"], "credential_operation": operation}))
    connection.execute(update(AdminCommand).where(AdminCommand.id == operation["id"]).values(status="applying", error=None,
                                                                                             result=dict(stage="waiting_for_owner", credential_version=operation["version"], operation_version=version,
                                                                                                         operator_action_required=False), updated_at=func.clock_timestamp()))
    return operation


def receipt(store, connection, actor, key, request, operation, request_id, credential_version):
    identity = str(uuid.uuid4())
    row = dict(connection.execute(AdminCommand.__table__.insert().values(
        id=identity, bot_id=store.bot_id, idempotency_key=key, request_hash=store.request_hash(request), action=request["action"],
        session_id=None, expected_version=request["expected_control_version"], effective_version=request["expected_control_version"],
        status="accepted", payload=request, result=dict(stage="waiting_for_cleanup", credential_version=credential_version,
                                                        cleanup_operation_id=operation["id"], operation_version=operation["transition"]),
        created_at=func.clock_timestamp(), updated_at=func.clock_timestamp()).returning(*AdminCommand.__table__.c)).mappings().one())
    store.access.audit(connection, actor=actor, action=request["action"], outcome="accepted", request_id=request_id,
                       operation_id=operation["id"], target_id=identity, exchange=request["exchange"], connector=request["connector"],
                       environment=request["environment"], event_key=identity + ":accepted",
                       details={"version": credential_version, "previous_version": request["expected_version"], "child_operation_id": identity})
    return row


def retry(store, actor, operation_id, *, expected_version, expected_control_version, expected_operation_version, idempotency_key, request_id):
    request = payload(store, actor, operation_id, action="credentials_cleanup_retry", expected_version=expected_version,
                      expected_control_version=expected_control_version, expected_operation_version=expected_operation_version)
    with store.engine.begin() as connection:
        current, state, previous = store.lock_request(connection, actor, idempotency_key, request)
        if previous:
            return previous
        operation, _ = blocked_scope(store, connection, state, request)
        operation = schedule(store, connection, state, operation)
        return receipt(store, connection, current, idempotency_key, request, operation, request_id, expected_version)


def prepare_repair(store, actor, *, exchange, connector, environment, operation_id, expected_version,
                   expected_control_version, expected_operation_version, idempotency_key):
    request = payload(store, actor, operation_id, action="credentials_cleanup_key", expected_version=expected_version,
                      expected_control_version=expected_control_version, expected_operation_version=expected_operation_version,
                      exchange=exchange, connector=connector, environment=environment)
    with store.engine.begin() as connection:
        _, state, previous = store.lock_request(connection, actor, idempotency_key, request)
        if previous:
            return previous
        blocked_scope(store, connection, state, request)
        return None


def repair(store, actor, *, exchange, connector, environment, operation_id, expected_version, expected_control_version,
           expected_operation_version, idempotency_key, account_id, envelope, request_id):
    if not isinstance(envelope, CredentialEnvelope):
        raise ValueError("Cleanup credentials require an encrypted envelope")
    request = payload(store, actor, operation_id, action="credentials_cleanup_key", expected_version=expected_version,
                      expected_control_version=expected_control_version, expected_operation_version=expected_operation_version,
                      exchange=exchange, connector=connector, environment=environment)
    with store.engine.begin() as connection:
        current, state, previous = store.lock_request(connection, actor, idempotency_key, request)
        if previous:
            return previous
        operation, saved = blocked_scope(store, connection, state, request)
        if account_id != saved["account_id"]:
            raise ControlConflict("Use credentials for the same verified exchange account", code="exchange_account_mismatch")
        version = expected_version + 1
        operation["affected_versions"] = {**operation["affected_versions"], connector: version}
        selected = operation["connector"] == connector
        if selected:
            operation["version"] = version
        operation = schedule(store, connection, state, operation)
        result = receipt(store, connection, current, idempotency_key, request, operation, request_id, version)
        connection.execute(update(ExchangeCredential).where(*store.scope(exchange, connector, environment)).values(
            version=version, envelope=envelope.data, changed_by=current.id, changed_at=func.clock_timestamp(),
            state="revoking" if selected else "active", operation_id=operation_id if selected else result["id"]))
        return result
