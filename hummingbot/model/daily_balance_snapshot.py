from sqlalchemy import Column, Date, DateTime, Index, Numeric, Text

from hummingbot.model import HummingbotBase


class DailyBalanceSnapshot(HummingbotBase):
    __tablename__ = "daily_balance_snapshots"
    __table_args__ = (Index("idx_daily_balance_snapshots_scope_day", "account_id", "environment",
                            "reporting_timezone", "day"),)
    account_id = Column(Text, primary_key=True, nullable=False)
    environment = Column(Text, primary_key=True, nullable=False)
    currency = Column(Text, primary_key=True, nullable=False)
    reporting_timezone = Column(Text, primary_key=True, nullable=False)
    day = Column(Date, primary_key=True, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    connector = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    cash_balance = Column(Numeric(38, 18))
    available_cash = Column(Numeric(38, 18))
    equity = Column(Numeric(38, 18))
    available_equity = Column(Numeric(38, 18))
