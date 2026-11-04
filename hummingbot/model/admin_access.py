"""Scoped account authorization and safe security audit storage."""
import uuid

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
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.core.admin_identity import AccessDenied, Principal
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

    def resolve_google(self, claims, *, bind=False, request_id=None):
        email, subject = claims["email"].casefold(), claims["sub"]
        with self.engine.begin() as connection:
            query = select(AdminAccount).where(AdminAccount.bot_id == self.bot_id, AdminAccount.email == email)
            if bind:
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
            if row is None:
                raise AccessDenied("account_not_allowed", status=403)
            if not row["active"]:
                raise AccessDenied("account_disabled", status=403)
            if row["subject"] is None:
                if not bind:
                    raise AccessDenied("account_onboarding_required", status=403)
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
        if set(safe) - {"version", "previous_version", "role", "previous_role", "active", "previous_active"}:
            raise ValueError("Unreviewed audit detail fields")
        connection.execute(AdminAuditEvent.__table__.insert().values(
            bot_id=self.bot_id, event_key=event_key or uuid.uuid4().hex, request_id=request_id,
            operation_id=operation_id, actor_kind=actor.kind if actor else "unknown",
            actor_id=actor.id if actor else None, actor_email=actor.email if actor else None,
            actor_role=actor.role if actor else None, action=action, target_id=target_id,
            outcome=outcome, reason_code=reason_code, details=safe, created_at=func.clock_timestamp()))
