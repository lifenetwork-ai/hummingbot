"""Current operational state only; strategy configuration stays in YAML."""
from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.model import HummingbotBase


class BotState(HummingbotBase):
    __tablename__ = "bot_state"
    __table_args__ = (
        CheckConstraint("desired_state IN ('stopped', 'running')", name="chk_bot_state_desired_state"),
    )

    bot_id = Column(Text, primary_key=True, nullable=False)
    config_file_path = Column(Text)
    config_type = Column(Text)
    desired_state = Column(Text, nullable=False, server_default=text("'stopped'"))
    control_version = Column(BigInteger, nullable=False, server_default=text("0"))
    acknowledged_version = Column(BigInteger, nullable=False, server_default=text("0"))
    owner_uuid = Column(Text)
    owner_generation = Column(BigInteger, nullable=False, server_default=text("0"))
    heartbeat = Column(DateTime(timezone=True))
    status = Column(JSONB().with_variant(JSON(), "sqlite"))
    revision = Column(Text)
    # Current unresolved external effects only, not an event/order journal.
    recovery_state = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, server_default=text("'{}'"))
