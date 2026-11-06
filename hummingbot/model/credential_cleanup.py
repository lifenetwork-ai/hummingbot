"""Owner-fenced Disconnect progress/removal using the existing receipt/control row."""
import math
import re

from sqlalchemy import func, or_, select, update

from hummingbot.core.admin_identity import Principal
from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import TERMINAL_STATUSES
from hummingbot.model.bot_state import BotState
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.position import Position

STAGES = {"checking_account", "cancelling_orders", "closing_positions", "verifying", "detaching", "action_required", "waiting_for_dependency"}
PROOFS = {"orders_terminal_verified", "positions_flat_verified", "ledger_confirmed", "consumers_detached",
          "account_verified", "position_checkpoints_verified", "observed_at", "owner_generation",
          "remaining_order_count", "remaining_position_count"}
FINAL_PROOFS = ("orders_terminal_verified", "positions_flat_verified", "ledger_confirmed", "consumers_detached")


def validate_evidence(evidence):
    if set(evidence) - PROOFS:
        raise ValueError("Unreviewed cleanup evidence fields")
    for name, value in evidence.items():
        if name == "observed_at":
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799
        elif name == "owner_generation":
            valid = type(value) is int and value > 0
        elif name in ("remaining_order_count", "remaining_position_count"):
            valid = type(value) is int and 0 <= value <= 5000
        else:
            valid = type(value) is bool
        if not valid:
            raise ValueError("Invalid cleanup evidence value")


def lock_operation(store, connection, identity, owner_uuid, generation):
    state = connection.execute(select(BotState, func.extract("epoch", func.clock_timestamp()).label("database_now")).where(
        BotState.bot_id == store.bot_id, BotState.owner_uuid == owner_uuid, BotState.owner_generation == generation).with_for_update()).mappings().first()
    if state is None:
        return None
    operation = (state["recovery_state"] or {}).get("credential_operation") or {}
    if (operation.get("id") != identity or operation.get("action") != "credentials_disconnect" or state["desired_state"] != "stopped"):
        return None
    receipt = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == store.bot_id, AdminCommand.id == identity,
                                                            AdminCommand.action == "credentials_disconnect", AdminCommand.effective_version == state["control_version"],
                                                            AdminCommand.status.in_(("accepted", "applying")))).mappings().first()
    return (dict(state), dict(operation), dict(receipt)) if receipt else None


def actor(store, connection, identity):
    row = connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.bot_id == store.bot_id,
                                                           AdminAuditEvent.operation_id == identity, AdminAuditEvent.action == "credentials_disconnect", AdminAuditEvent.outcome == "accepted")).mappings().one()
    return Principal(row["actor_id"], row["actor_kind"], row["actor_role"], row["actor_email"]), row["request_id"]


def fresh(evidence, state, generation):
    observed = evidence.get("observed_at")
    return (type(observed) in (int, float) and math.isfinite(observed) and evidence.get("owner_generation") == generation
            and 0 <= float(state["database_now"]) - observed <= 30)


def progress(store, identity, owner_uuid, generation, *, stage, evidence=None, reason_code=None, operator_action_required=False, next_retry_at=None):
    evidence = evidence or {}
    validate_evidence(evidence)
    if stage not in STAGES or reason_code is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason_code):
        raise ValueError("Invalid safe cleanup progress")
    with store.engine.begin() as connection:
        locked = lock_operation(store, connection, identity, owner_uuid, generation)
        if locked is None:
            return False
        state, operation, receipt = locked
        recovery = dict(state["recovery_state"] or {})
        if stage == "closing_positions" and (not fresh(evidence, state, generation)
                                             or evidence.get("orders_terminal_verified") is not True or evidence.get("account_verified") is not True
                                             or evidence.get("position_checkpoints_verified") is not True):
            return False
        if stage == "closing_positions" and recovery.get("shutdown_incomplete"):
            # A failed recorder can leave the prior process's shutdown fence.
            # Only its replacement, after frozen-checkpoint/readback proof, may
            # retire that process fence. Preserve risk HALTs and all send guards.
            previous = operation.get("approved_generation")
            if (not operation.get("checkpoint_frozen") or type(previous) is not int or not 0 < previous < generation
                    or recovery.get("runtime_active") or recovery.get("pending_mutations")
                    or evidence.get("remaining_order_count") != 0):
                return False
            unresolved = connection.execute(select(Order.id).where(or_(
                Order.creation_pending.is_(True), Order.last_status.notin_(TERMINAL_STATUSES))).limit(1)).first()
            if unresolved:
                return False
            recovery.pop("shutdown_incomplete", None)
        result = dict(stage=stage, credential_version=operation["version"], reason_code=reason_code,
                      operator_action_required=operator_action_required, owner_generation=generation, **{
                          key: value for key, value in evidence.items() if key != "owner_generation"})
        if stage in ("action_required", "waiting_for_dependency"):
            result["blocked_stage"] = operation.get("blocked_stage") or operation.get("stage") or "checking_account"
            operation["blocked_stage"] = result["blocked_stage"]
        else:
            operation.pop("blocked_stage", None)
        if next_retry_at is not None:
            result["next_retry_at"] = next_retry_at
        if (receipt["result"] or {}).get("stage") != stage or (receipt["result"] or {}).get("reason_code") != reason_code:
            transition = operation.get("transition", 0) + 1
            operation["transition"] = transition
            accepted_actor, request_id = actor(store, connection, identity)
            store.access.audit(connection, actor=accepted_actor, action="credentials_disconnect_progress", outcome="applying",
                               request_id=request_id, operation_id=identity, target_id=operation["connector"], exchange=operation["exchange"],
                               connector=operation["connector"], environment=operation["environment"], reason_code=reason_code,
                               event_key=identity + ":progress:" + str(transition), details={"stage": stage, "version": operation["version"]})
        operation["stage"] = stage
        result["operation_version"] = operation.get("transition", 0)
        operation["operator_action_required"] = operator_action_required
        operation["next_retry_at"] = next_retry_at
        operation["reason_code"] = reason_code
        if stage == "closing_positions":
            operation["approved_generation"] = generation
        recovery["credential_operation"] = operation
        connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(recovery_state=recovery))
        connection.execute(update(AdminCommand).where(AdminCommand.id == identity).values(
            status="applying", result=result, error=reason_code, updated_at=func.clock_timestamp()))
        return True


def finish(store, identity, owner_uuid, generation, *, evidence):
    validate_evidence(evidence)
    with store.engine.begin() as connection:
        locked = lock_operation(store, connection, identity, owner_uuid, generation)
        if locked is None:
            return False
        state, operation, receipt = locked
        if (operation.get("stage") != "detaching" or not fresh(evidence, state, generation)
                or any(evidence.get(key) is not True or receipt["result"].get(key) is not True for key in FINAL_PROOFS)
                or (state["recovery_state"] or {}).get("pending_mutations") or (state["recovery_state"] or {}).get("shutdown_incomplete")):
            return False
        unresolved = connection.execute(select(Order.id).where(or_(Order.creation_pending.is_(True),
                                                                   Order.last_status.notin_(TERMINAL_STATUSES))).limit(1)).first()
        exposure = connection.execute(select(Position.id).where(Position.connector_name == "okx_perpetual", Position.amount != 0).limit(1)).first()
        if unresolved or exposure:
            return False
        selected = connection.execute(select(ExchangeCredential).where(*store.scope(
            operation["exchange"], operation["connector"], operation["environment"]))).mappings().first()
        if (selected is None or selected["operation_id"] != identity or selected["state"] != "revoking"
                or selected["version"] != operation["version"]):
            return False
        companions = connection.execute(select(ExchangeCredential.connector, ExchangeCredential.version,
                                               ExchangeCredential.account_id, ExchangeCredential.exchange, ExchangeCredential.environment).where(
            ExchangeCredential.bot_id == store.bot_id, ExchangeCredential.state != "revoked")).all()
        if ({row.connector: row.version for row in companions} != operation["affected_versions"]
                or any((row.account_id, row.exchange, row.environment) != (
                    selected["account_id"], operation["exchange"], operation["environment"]) for row in companions)):
            return False
        connection.execute(update(ExchangeCredential).where(*store.scope(operation["exchange"], operation["connector"], operation["environment"])).values(
            state="revoked", envelope=None, changed_at=func.clock_timestamp()))
        accepted_actor, request_id = actor(store, connection, identity)
        store.access.audit(connection, actor=accepted_actor, action="credentials_disconnect", outcome="succeeded", request_id=request_id,
                           operation_id=identity, target_id=operation["connector"], exchange=operation["exchange"], connector=operation["connector"],
                           environment=operation["environment"], event_key=identity + ":removed", details={"version": operation["version"]})
        result = {**receipt["result"], **evidence, "stage": "removed", "reason_code": None, "operator_action_required": False}
        result.pop("next_retry_at", None)
        connection.execute(update(AdminCommand).where(AdminCommand.id == identity).values(
            status="succeeded", result=result, error=None, updated_at=func.clock_timestamp()))
        finish_children(store, connection, operation, result)
        recovery = dict(state["recovery_state"])
        recovery.pop("credential_operation")
        recovery["runtime_active"] = False
        values = dict(recovery_state=recovery, acknowledged_version=state["control_version"])
        if state["mm_session"] and state["mm_session"].get("status") == "stopping":
            session = {**state["mm_session"], "status": "stopped"}
            from hummingbot.model.mm_event import record_transition
            record_transition(connection, store.bot_id, state["mm_session"], session, state["control_version"],
                              source="credential_disconnect", principal=accepted_actor.id)
            values["mm_session"] = session
        connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(**values))
        return True


def finish_children(store, connection, operation, result):
    # Stream accepted requests; never load an unbounded operation history into memory.
    # Reuse the indexed audit operation link and command PK rather than scanning
    # command JSON history or looking up each accepted actor separately.
    audit_fields = ("actor_id", "actor_kind", "actor_role", "actor_email", "request_id")
    children = connection.execute(select(AdminCommand, *(
        getattr(AdminAuditEvent, key).label("accepted_" + key) for key in audit_fields)).join(
        AdminAuditEvent, AdminAuditEvent.target_id == AdminCommand.id).where(
            AdminAuditEvent.bot_id == store.bot_id, AdminAuditEvent.operation_id == operation["id"],
            AdminAuditEvent.outcome == "accepted", AdminAuditEvent.action == AdminCommand.action,
            AdminCommand.bot_id == store.bot_id,
            AdminCommand.action.in_(("credentials_cleanup_key", "credentials_cleanup_retry")),
            AdminCommand.status.in_(("accepted", "applying"))).execution_options(yield_per=100)).mappings()
    for child in children:
        replaced = (child["action"] == "credentials_cleanup_key" and
                    child["result"]["credential_version"] != operation["affected_versions"].get(child["payload"]["connector"]))
        error = "credential_version_replaced" if replaced else None
        child_result = {**result, "cleanup_operation_id": operation["id"],
                        "credential_version": child["result"]["credential_version"],
                        "stage": "replaced" if replaced else "cleanup_completed", "reason_code": error}
        connection.execute(update(AdminCommand).where(AdminCommand.id == child["id"]).values(
            status="failed" if replaced else "succeeded", result=child_result, error=error, updated_at=func.clock_timestamp()))
        accepted = Principal(child["accepted_actor_id"], child["accepted_actor_kind"], child["accepted_actor_role"], child["accepted_actor_email"])
        store.access.audit(connection, actor=accepted, action=child["action"], outcome="failed" if replaced else "succeeded",
                           request_id=child["accepted_request_id"], operation_id=operation["id"], target_id=child["id"],
                           exchange=operation["exchange"], connector=child["payload"]["connector"], environment=operation["environment"],
                           reason_code=error, event_key=child["id"] + ":completed", details={"child_operation_id": child["id"]})


def snapshot(store, identity, owner_uuid, generation, *, include_tracking_states=True):
    from hummingbot.core.control_errors import ControlConflict
    with store.engine.begin() as connection:
        locked = lock_operation(store, connection, identity, owner_uuid, generation)
        if locked is None:
            return None
        state, operation, receipt = locked
        positions = [dict(row) for row in connection.execute(select(Position).where(
            Position.connector_name == "okx_perpetual", Position.amount != 0).order_by(Position.id).limit(1001)).mappings()]
        orders = [dict(row) for row in connection.execute(select(Order).where(or_(Order.creation_pending.is_(True),
                                                                                  Order.last_status.notin_(TERMINAL_STATUSES))).order_by(Order.id).limit(5001)).mappings()]
        if len(positions) > 1000 or len(orders) > 5000:
            raise ControlConflict("Cleanup scope exceeds its safe batch limit", code="cleanup_scope_limit")
        configurations = {row["config_file_path"] for row in orders}
        states = [dict(row) for row in connection.execute(select(MarketState).where(
            MarketState.config_file_path.in_(configurations))).mappings()] if include_tracking_states and configurations else []
        return dict(state=state, operation=operation, receipt=receipt, positions=positions, orders=orders, tracking_states=states)
