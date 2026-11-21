"""Exact exchange bill identities alongside the unchanged upstream event table."""
import json

from sqlalchemy import BigInteger, Column, Index, Text, bindparam, cast, exists, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, insert

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
        """Discover each historical pair with an existing-index boundary seek.

        (market, symbol, creation_timestamp) orders each pair's earliest order
        first. Advancing strictly past the preceding symbol avoids rescanning
        all orders on every poll, while still seeing unconfigured pairs and
        newly imported earlier history in one fresh SQL statement/snapshot.
        """
        with self.engine.connect() as c:
            rows = c.execute(text("""
                WITH RECURSIVE scope AS (
                    (SELECT symbol, creation_timestamp FROM orders WHERE market='okx_perpetual'
                     ORDER BY symbol, creation_timestamp LIMIT 1)
                    UNION ALL
                    SELECT next_pair.* FROM scope CROSS JOIN LATERAL (
                        SELECT symbol, creation_timestamp FROM orders
                        WHERE market='okx_perpetual' AND symbol > scope.symbol
                        ORDER BY symbol, creation_timestamp LIMIT 1
                    ) next_pair
                ) SELECT symbol, creation_timestamp FROM scope
            """)).all()
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
        serialize = self.engine.dialect._json_serializer or json.dumps
        prepared_checkpoint = serialize(checkpoint)
        with self.engine.begin() as c:
            held = exists(select(1).select_from(text("pg_locks")).where(text(
                "locktype='advisory' AND granted AND pid=:owner_pid AND classid=:namespace AND objid=:owner_key AND objsubid=2")))
            query = select(BotState.bot_id).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == generation, held)
            authority = {"owner_pid": self.owner_backend_pid, "namespace": OWNER_NAMESPACE, "owner_key": OWNER_KEY}
            state = c.execute(query, authority).first()
            if state is None or not owner_uuid:
                raise ControlConflict("Funding observation owner changed")
            if normalized:
                # Do bill IO before locking the recorder's authority row. Check
                # identities after ON CONFLICT resolves concurrent inserts, so
                # a competing different bill cannot be silently accepted.
                c.execute(insert(table).values(list(normalized.values())).on_conflict_do_nothing())
                existing = c.execute(select(table).where(tuple_(table.c.account_id, table.c.environment,
                                                                table.c.bill_id).in_(list(normalized)))).mappings().all()
                if len(existing) != len(normalized):
                    raise ValueError("Funding bill coverage changed")
                for old in existing:
                    key = tuple(old[k] for k in ("account_id", "environment", "bill_id"))
                    if any(old[k] != value for k, value in normalized[key].items()):
                        raise ValueError("Funding bill identity conflict")
            # A busy or replaced owner cannot commit speculative bills/cursor.
            # No wait queue is inserted ahead of financial FOR SHARE readers.
            target = query.with_for_update(skip_locked=True).cte('funding_checkpoint_target')
            value = cast(bindparam('funding_checkpoint_json', prepared_checkpoint), JSONB)
            result = c.execute(update(BotState).where(BotState.bot_id == target.c.bot_id).values(
                recovery_state=func.jsonb_set(BotState.recovery_state, text("'{funding_sync}'"), value))
                .returning(BotState.bot_id), authority).first()
            if result is None:
                raise ControlConflict("Funding checkpoint authority changed or is busy", code='funding_checkpoint_deferred')
        return checkpoint
