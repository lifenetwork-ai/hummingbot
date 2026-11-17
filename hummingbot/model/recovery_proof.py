"""Bounded non-financial deduplication evidence for one reviewed recovery job."""
from sqlalchemy import Column, ForeignKey, Integer, Text, UniqueConstraint

from hummingbot.model import HummingbotBase


class RecoveryProofFill(HummingbotBase):
    __tablename__ = 'recovery_proof_fills'
    __table_args__ = (UniqueConstraint('command_id', 'scan', 'connector', 'bill_id'),)
    command_id = Column(Text, ForeignKey('admin_commands.id'), primary_key=True, nullable=False)
    scan = Column(Integer, primary_key=True, nullable=False)
    connector = Column(Text, primary_key=True, nullable=False)
    venue_order_id = Column(Text, primary_key=True, nullable=False)
    trade_id = Column(Text, primary_key=True, nullable=False)
    bill_id = Column(Text, nullable=False)
