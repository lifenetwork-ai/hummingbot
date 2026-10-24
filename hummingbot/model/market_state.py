#!/usr/bin/env python

from sqlalchemy import JSON, BigInteger, Column, Index, Integer, Sequence, Text

from . import HummingbotBase


class MarketState(HummingbotBase):
    __tablename__ = "market_states"
    __table_args__ = (Index("uq_market_states_config_file_path_market",
                            "config_file_path", "market", unique=True),)

    id = Column(Integer, Sequence("seq_market_states_id", metadata=HummingbotBase.metadata), primary_key=True, nullable=False)
    config_file_path = Column(Text, nullable=False)
    market = Column(Text, nullable=False)
    timestamp = Column(BigInteger, nullable=False)
    saved_state = Column(JSON, nullable=False)

    def __repr__(self) -> str:
        return f"MarketState(id='{self.id}', config_file_path='{self.config_file_path}', market='{self.market}', " \
            f"timestamp={self.timestamp}, saved_state={self.saved_state})"
