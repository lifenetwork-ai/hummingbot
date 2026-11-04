"""Encrypted credential writes with the existing control fence and safe receipts.

This module never accepts a plaintext credential dictionary. Read-only candidate
validation and envelope encryption happen before its short commit transaction.
"""
import hashlib
import json
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from hummingbot.core.credential_encryption import CredentialEnvelope
from hummingbot.model.admin_access import AdminAccessStore
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import IdempotencyConflict
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential

CREDENTIAL_ACTIONS = ("credentials_set", "credentials_disconnect", "credentials_cleanup_key", "credentials_cleanup_retry")
SAFE_COLUMNS = tuple(column for column in ExchangeCredential.__table__.c if column.name != "envelope")


class CredentialStore:
    def __init__(self, store):
        self.store, self.engine, self.bot_id = store, store.engine, store.bot_id
        self.access = AdminAccessStore(self.engine, self.bot_id)

    def scope(self, exchange, connector, environment):
        return (ExchangeCredential.bot_id == self.bot_id, ExchangeCredential.exchange == exchange,
                ExchangeCredential.connector == connector, ExchangeCredential.environment == environment)

    def load(self, exchange, connector, environment):
        """Private engine-only envelope load; never use for HTTP serialization."""
        with self.engine.connect() as connection:
            row = connection.execute(select(ExchangeCredential).where(*self.scope(exchange, connector, environment))).mappings().first()
            return dict(row) if row else None

    def metadata(self, exchange=None):
        query = select(*SAFE_COLUMNS).where(ExchangeCredential.bot_id == self.bot_id)
        if exchange is not None:
            query = query.where(ExchangeCredential.exchange == exchange)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query.order_by(
                ExchangeCredential.exchange, ExchangeCredential.connector, ExchangeCredential.environment)).mappings()]

    def operation(self, identity):
        with self.engine.connect() as connection:
            row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                                AdminCommand.id == identity,
                                                                AdminCommand.action.in_(CREDENTIAL_ACTIONS))).mappings().first()
            return dict(row) if row else None

    def status_snapshot(self, exchange):
        with self.engine.connect() as connection:
            metadata = [dict(row) for row in connection.execute(select(*SAFE_COLUMNS).where(
                ExchangeCredential.bot_id == self.bot_id, ExchangeCredential.exchange == exchange)).mappings()]
            identities = [row["operation_id"] for row in metadata if row["operation_id"]]
            operations = {row["id"]: dict(row) for row in connection.execute(select(
                AdminCommand.id, AdminCommand.status, AdminCommand.error, AdminCommand.result).where(
                AdminCommand.bot_id == self.bot_id, AdminCommand.id.in_(identities))).mappings()} if identities else {}
        return metadata, self.store.read(), operations

    def record_failure(self, actor, *, request_id, exchange, connector, environment, reason_code):
        # The adapter maps failures into fixed safe codes. Never persist its request/body.
        with self.engine.begin() as connection:
            current = self.access.authorize(connection, actor, "write")
            self.access.audit(connection, actor=current, action="credentials_set", outcome="failed", request_id=request_id,
                              target_id=connector, exchange=exchange, connector=connector, environment=environment, reason_code=reason_code)

    def set_payload(self, actor, *, exchange, connector, environment, expected_version, expected_control_version):
        if any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in (exchange, connector, environment)):
            raise ValueError("Invalid credential scope")
        if any(type(value) is not int or value < 0 for value in (expected_version, expected_control_version)):
            raise ValueError("Invalid credential precondition")
        return dict(action="credentials_set", bot_id=self.bot_id, actor_id=actor.id, actor_kind=actor.kind,
                    exchange=exchange, connector=connector, environment=environment,
                    expected_version=expected_version, expected_control_version=expected_control_version)

    @staticmethod
    def request_hash(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def lock_request(self, connection, actor, key, payload):
        if (not isinstance(key, str) or not 1 <= len(key) <= 128
                or any(not 33 <= ord(character) <= 126 for character in key)):
            raise ValueError("Idempotency-Key must contain 1–128 visible ASCII characters")
        current_actor = self.access.authorize(connection, actor, "write")
        state = connection.execute(select(BotState).where(BotState.bot_id == self.bot_id).with_for_update()).mappings().first()
        if state is None:
            raise ControlConflict("Bot state unavailable")
        previous = connection.execute(select(AdminCommand).where(
            AdminCommand.bot_id == self.bot_id, AdminCommand.idempotency_key == key)).mappings().first()
        if previous and previous["request_hash"] != self.request_hash(payload):
            raise IdempotencyConflict("Idempotency-Key is bound to another operation")
        return current_actor, dict(state), dict(previous) if previous else None

    def check_stopped(self, connection, state, payload):
        from hummingbot.model.order import Order
        from hummingbot.model.position import Position
        from hummingbot.model.trade_fill import TradeFill
        recovery = state["recovery_state"] or {}
        if recovery.get("credential_operation"):
            raise ControlConflict("Wait for the current connection operation", code="credential_operation_in_progress")
        if state["control_version"] != payload["expected_control_version"]:
            raise ControlConflict("Control version changed", code="control_version_changed")
        if (state["desired_state"] != "stopped" or state["acknowledged_version"] != state["control_version"]
                or recovery.get("runtime_active") or recovery.get("pending_mutations")):
            raise ControlConflict("Stop and confirm order cleanup first", code="bot_must_be_stopped")
        if recovery.get("halt"):
            raise ControlConflict("Resolve the existing recovery incident first", code="recovery_required")
        saved = connection.execute(select(ExchangeCredential.version).where(*self.scope(
            payload["exchange"], payload["connector"], payload["environment"]))).scalar_one_or_none()
        if (saved or 0) != payload["expected_version"]:
            raise ControlConflict("Connection version changed", code="credential_version_conflict")
        bindings = connection.execute(select(ExchangeCredential.account_id).where(
            ExchangeCredential.bot_id == self.bot_id).limit(1)).first()
        if bindings is None and any(connection.execute(select(model).limit(1)).first() is not None
                                    for model in (Order, TradeFill, Position)):
            raise ControlConflict("Existing ledger requires operator-reviewed account binding", code="account_binding_required")

    def prepare_set(self, actor, *, exchange, connector, environment, expected_version, expected_control_version, idempotency_key):
        payload = self.set_payload(actor, exchange=exchange, connector=connector, environment=environment,
                                   expected_version=expected_version, expected_control_version=expected_control_version)
        with self.engine.begin() as connection:
            _, state, previous = self.lock_request(connection, actor, idempotency_key, payload)
            if previous:
                return previous
            self.check_stopped(connection, state, payload)
        return None

    def accept_set(self, actor, *, exchange, connector, environment, account_id, expected_version,
                   expected_control_version, idempotency_key, envelope, request_id):
        if not isinstance(envelope, CredentialEnvelope):
            raise ValueError("Credential storage requires an encrypted envelope")
        if not isinstance(account_id, str) or not 1 <= len(account_id) <= 255:
            raise ValueError("Invalid verified account binding")
        payload = self.set_payload(actor, exchange=exchange, connector=connector, environment=environment,
                                   expected_version=expected_version, expected_control_version=expected_control_version)
        with self.engine.begin() as connection:
            current_actor, state, previous = self.lock_request(connection, actor, idempotency_key, payload)
            if previous:
                return previous
            self.check_stopped(connection, state, payload)
            bindings = connection.execute(select(ExchangeCredential.exchange, ExchangeCredential.environment,
                                                 ExchangeCredential.account_id).where(ExchangeCredential.bot_id == self.bot_id)).all()
            if any(binding != (exchange, environment, account_id) for binding in bindings):
                raise ControlConflict("The key belongs to another exchange account", code="exchange_account_mismatch")
            identity, version, control = str(uuid.uuid4()), expected_version + 1, expected_control_version + 1
            values = dict(bot_id=self.bot_id, exchange=exchange, connector=connector, environment=environment, version=version,
                          account_id=account_id, state="active", envelope=envelope.data, changed_by=current_actor.id,
                          changed_at=func.clock_timestamp(), operation_id=identity)
            statement = insert(ExchangeCredential).values(**values)
            connection.execute(statement.on_conflict_do_update(index_elements=["bot_id", "exchange", "connector", "environment"], set_=values))
            recovery = {**(state["recovery_state"] or {}), "credential_operation": dict(
                id=identity, action="credentials_set", exchange=exchange, connector=connector, environment=environment, version=version)}
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(
                control_version=control, recovery_state=recovery))
            receipt = dict(connection.execute(AdminCommand.__table__.insert().values(
                id=identity, bot_id=self.bot_id, idempotency_key=idempotency_key, request_hash=self.request_hash(payload),
                action="credentials_set", session_id=None, expected_version=expected_control_version, effective_version=control,
                status="accepted", payload=payload, result={"stage": "waiting_for_owner", "credential_version": version},
                created_at=func.clock_timestamp(), updated_at=func.clock_timestamp()).returning(*AdminCommand.__table__.c)).mappings().one())
            self.access.audit(connection, actor=current_actor, action="credentials_set", outcome="accepted", request_id=request_id,
                              operation_id=identity, target_id=connector, exchange=exchange, connector=connector, environment=environment,
                              details={"version": version, "previous_version": expected_version})
            return receipt

    def finish_set(self, identity, owner_uuid, owner_generation, *, versions, error=None):
        from hummingbot.core.admin_identity import Principal
        from hummingbot.model.admin_access import AdminAuditEvent
        with self.engine.begin() as connection:
            state = connection.execute(select(BotState).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation).with_for_update()).mappings().first()
            if state is None:
                return False
            recovery = dict(state["recovery_state"] or {})
            operation = recovery.get("credential_operation") or {}
            if operation.get("id") != identity or operation.get("action") != "credentials_set":
                return False
            receipt = connection.execute(select(AdminCommand).where(AdminCommand.id == identity,
                                                                    AdminCommand.bot_id == self.bot_id, AdminCommand.action == "credentials_set",
                                                                    AdminCommand.effective_version == state["control_version"])).mappings().first()
            if receipt is None or state["desired_state"] != "stopped":
                return False
            if error is None and versions.get(operation["connector"]) != operation["version"]:
                return False
            accepted = connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.bot_id == self.bot_id,
                                                                        AdminAuditEvent.operation_id == identity, AdminAuditEvent.outcome == "accepted")).mappings().one()
            actor = Principal(accepted["actor_id"], accepted["actor_kind"], accepted["actor_role"], accepted["actor_email"])
            result = dict(stage="failed" if error else "loaded", credential_version=operation["version"],
                          owner_uuid=owner_uuid, owner_generation=owner_generation, reason_code=error,
                          operator_action_required=bool(error))
            connection.execute(update(AdminCommand).where(AdminCommand.id == identity).values(
                status="failed" if error else "succeeded", error=error, result=result, updated_at=func.clock_timestamp()))
            self.access.audit(connection, actor=actor, action="credentials_set", outcome="failed" if error else "succeeded",
                              request_id=accepted["request_id"], operation_id=identity, target_id=operation["connector"],
                              event_key=identity + ":loaded", reason_code=error, exchange=operation["exchange"],
                              connector=operation["connector"], environment=operation["environment"],
                              details={"version": operation["version"]})
            recovery.pop("credential_operation")
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(recovery_state=recovery))
            return True

    def claim_set(self, identity, owner_uuid, owner_generation):
        authority = select(BotState.bot_id).where(
            BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid, BotState.owner_generation == owner_generation,
            BotState.control_version == AdminCommand.effective_version,
            BotState.recovery_state["credential_operation"]["id"].astext == identity).exists()
        with self.engine.begin() as connection:
            result = connection.execute(update(AdminCommand).where(
                AdminCommand.bot_id == self.bot_id, AdminCommand.id == identity, AdminCommand.action == "credentials_set",
                AdminCommand.status.in_(("accepted", "applying")), authority).values(
                status="applying", result=func.jsonb_set(AdminCommand.result, "{stage}", '"loading"'), updated_at=func.clock_timestamp()))
            return result.rowcount == 1
