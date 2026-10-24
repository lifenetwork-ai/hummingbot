#!/usr/bin/env python
from typing import Any, Dict

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Sequence, Text
from sqlalchemy.orm import relationship

from . import HummingbotBase


class OrderStatus(HummingbotBase):
    __tablename__ = "order_statuses"
    __table_args__ = (Index("idx_order_statuses_order_id_timestamp",
                            "order_id", "timestamp"),
                      )

    id = Column(Integer, Sequence("seq_order_statuses_id", metadata=HummingbotBase.metadata), primary_key=True, nullable=False)
    order_id = Column(Text, ForeignKey("orders.id"), nullable=False)
    timestamp = Column(BigInteger, nullable=False)
    status = Column(Text, nullable=False)
    order = relationship("Order", back_populates="status")

    def __repr__(self) -> str:
        return f"OrderStatus(id={self.id}, order_id='{self.order_id}', timestamp={self.timestamp}, " \
            f"status='{self.status}')"

    @staticmethod
    def to_bounty_api_json(order_status: "OrderStatus") -> Dict[str, Any]:
        return {
            "order_id": order_status.order_id,
            "timestamp": order_status.timestamp,
            "event_type": order_status.status,
            "raw_json": {
            }
        }
