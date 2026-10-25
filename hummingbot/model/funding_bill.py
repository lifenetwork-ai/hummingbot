"""Exact exchange bill identities alongside the unchanged upstream event table."""
from sqlalchemy import BigInteger, Column, Index, Text, exists, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from hummingbot.model import HummingbotBase
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.decimal_type_decorator import SqliteDecimal


class FundingBill(HummingbotBase):
    __tablename__ = "funding_bills"
    __table_args__ = (Index("idx_funding_bills_account_environment_timestamp_symbol", "account_id", "environment", "timestamp", "symbol"),)
    account_id = Column(Text, primary_key=True, nullable=False)
    environment = Column(Text, primary_key=True, nullable=False)
    bill_id = Column(Text, primary_key=True, nullable=False)
    symbol = Column(Text, nullable=False)
    timestamp = Column(BigInteger, nullable=False)
    currency = Column(Text, nullable=False)
    amount = Column(SqliteDecimal(6), nullable=False)
    rate = Column(SqliteDecimal(6))


class FundingBillStore:
    def __init__(self, engine, bot_id, *, owner_backend_pid):
        self.engine, self.bot_id = engine, bot_id
        self.owner_backend_pid = owner_backend_pid

    def load(self):
        with self.engine.connect() as c:
            state = c.execute(select(BotState.recovery_state).where(BotState.bot_id == self.bot_id)).scalar_one()
            return (state or {}).get("funding_sync", {})

    def scope(self):
        """One bounded aggregate, independent of number of configured pairs."""
        from sqlalchemy import func

        from hummingbot.model.order import Order
        with self.engine.connect() as c:
            rows = c.execute(select(Order.symbol, func.min(Order.creation_timestamp)).where(
                Order.market == "okx_perpetual").group_by(Order.symbol)).all()
        return {row[0] for row in rows}, min((row[1] for row in rows), default=None)

    def commit(self, owner_uuid, generation, bills, checkpoint):
        if len(bills) > 100:
            raise ValueError("Funding page exceeds its bounded size")
        normalized = {}
        for row in bills:
            row = dict(row)
            row["amount"] = SqliteDecimal._finite_decimal(row["amount"])
            row["rate"] = SqliteDecimal._finite_decimal(row.get("rate"))
            key = tuple(row[k] for k in ("account_id", "environment", "bill_id"))
            if key in normalized and normalized[key] != row:
                raise ValueError("Funding bill identity conflict")
            normalized[key] = row
        from hummingbot.core.bot_ownership import OWNER_KEY, OWNER_NAMESPACE
        table = FundingBill.__table__
        with self.engine.begin() as c:
            held = exists(select(1).select_from(text("pg_locks")).where(text(
                "locktype='advisory' AND granted AND pid=:owner_pid AND classid=:namespace AND objid=:owner_key AND objsubid=2")))
            query = select(BotState.recovery_state).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == generation, held).with_for_update()
            state = c.execute(query, {"owner_pid": self.owner_backend_pid,
                                      "namespace": OWNER_NAMESPACE, "owner_key": OWNER_KEY}).first()
            if state is None or not owner_uuid:
                raise ControlConflict("Funding observation owner changed")
            if normalized:
                existing = c.execute(select(table).where(tuple_(table.c.account_id, table.c.environment,
                                                                table.c.bill_id).in_(list(normalized)))).mappings()
                for old in existing:
                    key = tuple(old[k] for k in ("account_id", "environment", "bill_id"))
                    if any(old[k] != value for k, value in normalized[key].items()):
                        raise ValueError("Funding bill identity conflict")
                c.execute(insert(table).values(list(normalized.values())).on_conflict_do_nothing())
            recovery = {**(state[0] or {}), "funding_sync": checkpoint}
            c.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(recovery_state=recovery))
        return checkpoint
