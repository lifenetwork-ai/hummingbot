from sqlalchemy import JSON, Column, Float, Index, Integer, Sequence, Text

from hummingbot.model import HummingbotBase


class Controllers(HummingbotBase):
    __tablename__ = "controllers"
    __table_args__ = (
        Index("idx_controllers_type", "type"),
    )

    id = Column(Text, primary_key=False)
    controller_id = Column(Integer, Sequence("seq_controllers_controller_id", metadata=HummingbotBase.metadata), primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False)
    type = Column(Text, nullable=False)
    config = Column(JSON, nullable=False)
