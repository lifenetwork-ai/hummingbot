"""Scoped account authorization and safe security audit storage."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.core.admin_identity import AccessDenied, Principal, normalize_email
from hummingbot.model import HummingbotBase


class AdminAccount(HummingbotBase):
    __tablename__ = "admin_accounts"
    __table_args__ = (
        UniqueConstraint("bot_id", "email"), UniqueConstraint("bot_id", "issuer", "subject"),
        CheckConstraint("role IN ('admin', 'write', 'read')", name="chk_admin_accounts_role"),
        CheckConstraint("(issuer IS NULL AND subject IS NULL) OR (issuer IS NOT NULL AND subject IS NOT NULL)",
                        name="chk_admin_accounts_binding"),
    )
    id = Column(Text, primary_key=True, nullable=False)
    bot_id = Column(Text, ForeignKey("bot_state.bot_id"), nullable=False)
    email = Column(Text, nullable=False)
    issuer = Column(Text)
    subject = Column(Text)
    role = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False)
    version = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class AdminAuditEvent(HummingbotBase):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        UniqueConstraint("event_key"),
        Index("idx_admin_audit_events_bot_id_created_at_id", "bot_id", "created_at", "id"),
        Index("idx_admin_audit_events_bot_id_operation_id_id", "bot_id", "operation_id", "id"),
    )
    id = Column(BigInteger, Sequence("seq_admin_audit_events_id"), primary_key=True, nullable=False)
    bot_id = Column(Text, ForeignKey("bot_state.bot_id"), nullable=False)
    event_key = Column(Text, nullable=False)
    request_id = Column(Text, nullable=False)
    operation_id = Column(Text)
    actor_kind = Column(Text, nullable=False)
    actor_id = Column(Text)
    actor_email = Column(Text)
    actor_role = Column(Text)
    action = Column(Text, nullable=False)
    target_id = Column(Text)
    exchange = Column(Text)
    connector = Column(Text)
    environment = Column(Text)
    outcome = Column(Text, nullable=False)
    reason_code = Column(Text)
    details = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AdminAccessStore:
    def __init__(self, engine, bot_id):
        self.engine, self.bot_id = engine, bot_id

    @staticmethod
    def principal(row):
        return Principal(row["id"], "google", row["role"], row["email"], row["version"])

    @staticmethod
    def lock_accounts(connection):
        connection.execute(text("SELECT pg_advisory_xact_lock(1212301635, 1)"))

    def bound_admins(self):
        table = AdminAccount.__table__.alias("bound_admin")
        return select(func.count()).select_from(table).where(
            table.c.bot_id == self.bot_id, table.c.active.is_(True), table.c.role == "admin", table.c.subject.is_not(None))

    def resolve_google(self, claims, *, bind=False, request_id=None, bootstrap_enabled=True):
        email, subject = claims["email"].casefold(), claims["sub"]
        with self.engine.begin() as connection:
            if bind:
                self.lock_accounts(connection)
            query = select(AdminAccount, self.bound_admins().scalar_subquery().label("bound_admins")).where(
                AdminAccount.bot_id == self.bot_id, AdminAccount.email == email)
            if bind:
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
            if row is None:
                raise AccessDenied("account_not_allowed", status=403)
            if not row["active"]:
                raise AccessDenied("account_disabled", status=403, actor=self.principal(row))
            if not bootstrap_enabled and not row["bound_admins"]:
                raise AccessDenied("bootstrap_required", status=503, actor=self.principal(row))
            if row["subject"] is None:
                if not bind:
                    raise AccessDenied("account_onboarding_required", status=403)
                previous = connection.execute(select(AdminAccount.id).where(
                    AdminAccount.bot_id == self.bot_id, AdminAccount.issuer == "https://accounts.google.com",
                    AdminAccount.subject == subject)).first()
                if previous:
                    raise AccessDenied("identity_binding_mismatch", status=403, actor=self.principal(row))
                row = dict(connection.execute(update(AdminAccount).where(AdminAccount.id == row["id"]).values(
                    issuer="https://accounts.google.com", subject=subject, version=row["version"] + 1,
                    updated_at=func.clock_timestamp()).returning(*AdminAccount.__table__.c)).mappings().one())
                self.audit(connection, actor=self.principal(row), action="account_bound", target_id=row["id"],
                           outcome="succeeded", request_id=request_id or uuid.uuid4().hex,
                           details={"version": row["version"]})
            elif row["subject"] != subject or row["issuer"] != "https://accounts.google.com":
                raise AccessDenied("identity_binding_mismatch", status=403)
            return self.principal(row)

    def authorize(self, connection, principal, required):
        if principal.kind == "basic":
            principal.require(required)
            return principal
        row = connection.execute(select(AdminAccount).where(AdminAccount.bot_id == self.bot_id,
                                                            AdminAccount.id == principal.id).with_for_update(read=True)).mappings().first()
        if row is None or not row["active"]:
            raise AccessDenied("account_disabled", status=403)
        current = self.principal(row)
        current.require(required)
        return current

    def audit(self, connection, *, actor, action, outcome, request_id, target_id=None,
              reason_code=None, operation_id=None, event_key=None, details=None):
        safe = details or {}
        if set(safe) - {"version", "previous_version", "role", "previous_role", "active", "previous_active", "target_email"}:
            raise ValueError("Unreviewed audit detail fields")
        connection.execute(AdminAuditEvent.__table__.insert().values(
            bot_id=self.bot_id, event_key=event_key or uuid.uuid4().hex, request_id=request_id,
            operation_id=operation_id, actor_kind=actor.kind if actor else "unknown",
            actor_id=actor.id if actor else None, actor_email=actor.email if actor else None,
            actor_role=actor.role if actor else None, action=action, target_id=target_id,
            outcome=outcome, reason_code=reason_code, details=safe, created_at=func.clock_timestamp()))

    def bootstrap_status(self):
        with self.engine.connect() as connection:
            count = connection.execute(self.bound_admins()).scalar_one()
            return {"google_admin_ready": count > 0, "active_google_admins": count}

    def record_login(self, principal, *, request_id):
        with self.engine.begin() as connection:
            actor = self.authorize(connection, principal, "read")
            self.audit(connection, actor=actor, action="login", outcome="succeeded", request_id=request_id)

    def record_denial(self, principal, *, request_id, reason_code, login=False):
        with self.engine.begin() as connection:
            self.audit(connection, actor=principal, action="login" if login else "access_denied", outcome="denied",
                       request_id=request_id, reason_code=reason_code)

    def add_account(self, principal, *, email, role, request_id, active=True):
        from hummingbot.model.bot_state_store import ControlConflict
        if role not in ("read", "write", "admin") or type(active) is not bool:
            raise ValueError("Invalid account fields")
        email = normalize_email(email)
        with self.engine.begin() as connection:
            self.lock_accounts(connection)
            actor = self.authorize(connection, principal, "admin")
            if connection.execute(select(AdminAccount.id).where(AdminAccount.bot_id == self.bot_id,
                                                                AdminAccount.email == email)).first():
                raise ControlConflict("account_exists", code="account_exists")
            row = dict(connection.execute(AdminAccount.__table__.insert().values(
                id=str(uuid.uuid4()), bot_id=self.bot_id, email=email, role=role, active=active, version=1,
                created_at=func.clock_timestamp(), updated_at=func.clock_timestamp()).returning(*AdminAccount.__table__.c)).mappings().one())
            self.audit(connection, actor=actor, action="account_added", target_id=row["id"], outcome="succeeded",
                       request_id=request_id, details={"target_email": email, "role": role, "active": active, "version": 1})
            return row

    def change_account(self, principal, account_id, *, expected_version, request_id, role=None, active=None):
        from hummingbot.model.bot_state_store import ControlConflict
        if (type(expected_version) is not int or expected_version < 1 or role is not None and role not in ("read", "write", "admin")
                or active is not None and type(active) is not bool or role is None and active is None):
            raise ValueError("Invalid account change")
        with self.engine.begin() as connection:
            self.lock_accounts(connection)
            actor = self.authorize(connection, principal, "admin")
            row = connection.execute(select(AdminAccount).where(AdminAccount.bot_id == self.bot_id,
                                                                AdminAccount.id == account_id).with_for_update()).mappings().first()
            if row is None:
                raise ControlConflict("account_unavailable", code="account_unavailable")
            if row["version"] != expected_version:
                raise ControlConflict("account_version_conflict", code="account_version_conflict")
            new_role, new_active = role if role is not None else row["role"], active if active is not None else row["active"]
            removing_admin = row["active"] and row["role"] == "admin" and row["subject"] is not None and (not new_active or new_role != "admin")
            if removing_admin and connection.execute(self.bound_admins()).scalar_one() <= 1:
                raise ControlConflict("last_admin_required", code="last_admin_required")
            result = dict(connection.execute(update(AdminAccount).where(AdminAccount.id == row["id"]).values(
                role=new_role, active=new_active, version=expected_version + 1, updated_at=func.clock_timestamp())
                .returning(*AdminAccount.__table__.c)).mappings().one())
            self.audit(connection, actor=actor, action="account_updated", target_id=account_id, outcome="succeeded",
                       request_id=request_id, details={"previous_version": expected_version, "version": expected_version + 1,
                                                       "target_email": row["email"], "previous_role": row["role"], "role": new_role,
                                                       "previous_active": row["active"], "active": new_active})
            return result

    def accounts_page(self, *, limit=100, cursor=None):
        from hummingbot.model.admin_store import _cursor, _cursor_values
        scope = {"bot_id": self.bot_id}
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid page size")
        query = select(AdminAccount).where(AdminAccount.bot_id == self.bot_id)
        point = _cursor_values(cursor, "admin_accounts", scope, 2) if cursor else None
        if point:
            query = query.where(AdminAccount.id > point[1])
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(query.order_by(AdminAccount.id).limit(limit + 1)).mappings()]
        return dict(items=rows[:limit], next_cursor=_cursor("admin_accounts", scope, [0, rows[limit - 1]["id"]]) if len(rows) > limit else None)

    def audit_page(self, *, limit=100, cursor=None, **filters):
        from sqlalchemy import tuple_

        from hummingbot.model.admin_store import _cursor, _cursor_values
        supported = {"actor_id", "action", "outcome", "target_id", "operation_id", "exchange", "connector", "environment", "from_time", "to_time"}
        if type(limit) is not int or not 1 <= limit <= 500 or set(filters) - supported:
            raise ValueError("Invalid audit filters")
        filters = {key: value for key, value in filters.items() if value is not None}
        scope = {"bot_id": self.bot_id, **{key: value.isoformat() if isinstance(value, datetime) else value for key, value in filters.items()}}
        point = _cursor_values(cursor, "admin_audit", scope, 2, max_first=253402300799999999) if cursor else None
        query = select(AdminAuditEvent).where(AdminAuditEvent.bot_id == self.bot_id)
        for field, value in filters.items():
            if field == "from_time":
                query = query.where(AdminAuditEvent.created_at >= value)
            elif field == "to_time":
                query = query.where(AdminAuditEvent.created_at < value)
            else:
                query = query.where(getattr(AdminAuditEvent, field) == value)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if point:
            from datetime import timedelta
            if not point[1].isascii() or not point[1].isdigit():
                raise ValueError("Invalid cursor")
            query = query.where(tuple_(AdminAuditEvent.created_at, AdminAuditEvent.id) < (epoch + timedelta(microseconds=point[0]), int(point[1])))
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(query.order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc()).limit(limit + 1)).mappings()]
        after = None
        if len(rows) > limit:
            last = rows[limit - 1]
            delta = last["created_at"] - epoch
            micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
            after = _cursor("admin_audit", scope, [micros, str(last["id"])])
        return dict(items=rows[:limit], next_cursor=after)
