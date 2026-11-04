"""Current encrypted connection plus retained, non-secret identity/version metadata."""
from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.model import HummingbotBase


class ExchangeCredential(HummingbotBase):
    __tablename__ = "exchange_credentials"
    __table_args__ = (
        CheckConstraint("version > 0", name="chk_exchange_credentials_version"),
        CheckConstraint("state IN ('active', 'revoking', 'revoked')", name="chk_exchange_credentials_state"),
        CheckConstraint("(state = 'revoked' AND envelope IS NULL) OR (state != 'revoked' AND envelope IS NOT NULL)",
                        name="chk_exchange_credentials_envelope"),
    )
    bot_id = Column(Text, ForeignKey("bot_state.bot_id"), primary_key=True, nullable=False)
    exchange = Column(Text, primary_key=True, nullable=False)
    connector = Column(Text, primary_key=True, nullable=False)
    environment = Column(Text, primary_key=True, nullable=False)
    version = Column(BigInteger, nullable=False)
    account_id = Column(Text, nullable=False)
    state = Column(Text, nullable=False)
    envelope = Column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"))
    changed_by = Column(Text, nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False)
    operation_id = Column(Text)
