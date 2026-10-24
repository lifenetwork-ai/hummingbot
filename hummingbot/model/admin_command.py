"""Durable admin retry receipts, separate from current bot/session state."""
from sqlalchemy import JSON, BigInteger, Column, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.model import HummingbotBase


class AdminCommand(HummingbotBase):
    __tablename__ = "admin_commands"
    __table_args__ = (UniqueConstraint("bot_id", "idempotency_key"),
                      Index("idx_admin_commands_bot_id_created_at", "bot_id", "created_at"),
                      Index("idx_admin_commands_bot_id_status_effective_version", "bot_id", "status", "effective_version"))
    id = Column(Text, primary_key=True, nullable=False)
    bot_id = Column(Text, ForeignKey("bot_state.bot_id"), nullable=False)
    idempotency_key = Column(Text, nullable=False)
    request_hash = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    session_id = Column(Text)
    expected_version = Column(BigInteger, nullable=False)
    effective_version = Column(BigInteger, nullable=False)
    status = Column(Text, nullable=False)
    payload = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    error = Column(Text)
    result = Column(JSONB().with_variant(JSON(), "sqlite"))
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
