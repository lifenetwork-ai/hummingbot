"""Read-only schema admission for the synchronous legacy recorder.

This is a reporting compatibility seam, never LIFE financial authority. Schema
creation/upgrades belong exclusively to the central numbered migration runner.
"""
from sqlalchemy import UniqueConstraint, inspect, select, text

SCHEMA = 'life_recorder'
ERROR = 'RECORDER_POSTGRES_SCHEMA_INCOMPATIBLE'


def recorder_tables():
    # Explicit imports keep the schema independent of incidental import order.
    from hummingbot.model.controllers import Controllers
    from hummingbot.model.executors import Executors
    from hummingbot.model.funding_payment import FundingPayment
    from hummingbot.model.inventory_cost import InventoryCost
    from hummingbot.model.market_data import MarketData
    from hummingbot.model.market_state import MarketState
    from hummingbot.model.metadata import Metadata
    from hummingbot.model.order import Order
    from hummingbot.model.order_status import OrderStatus
    from hummingbot.model.position import Position
    from hummingbot.model.range_position_collected_fees import RangePositionCollectedFees
    from hummingbot.model.range_position_update import RangePositionUpdate
    from hummingbot.model.trade_fill import TradeFill
    return tuple(model.__table__ for model in (
        Controllers, Executors, FundingPayment, InventoryCost, MarketData, MarketState,
        Metadata, Order, OrderStatus, Position, RangePositionCollectedFees, RangePositionUpdate, TradeFill))


def validate_schema(engine, expected_version):
    """No DDL, metadata stamping, file copies or destructive FK removal."""
    from hummingbot.model.metadata import Metadata

    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            if SCHEMA not in inspector.get_schema_names():
                raise ValueError(ERROR)
            if conn.scalar(text("SELECT has_schema_privilege(current_user, 'life_recorder', 'CREATE')")):
                raise ValueError(ERROR)
            if conn.scalar(text('SELECT has_table_privilege(current_user, \'life_recorder."Metadata"\', \'INSERT,UPDATE,DELETE\')')):
                raise ValueError(ERROR)
            for table in recorder_tables():
                actual = {column['name']: column for column in inspector.get_columns(table.name, schema=SCHEMA)}
                if set(actual) != set(table.c.keys()):
                    raise ValueError(ERROR)
                for column in table.columns:
                    stored = actual[column.name]
                    expected_type = column.type.compile(dialect=engine.dialect)
                    # PostgreSQL stores an unqualified SQL FLOAT as float8.
                    if expected_type == 'FLOAT':
                        expected_type = 'DOUBLE PRECISION'
                    if (stored['type'].compile(dialect=engine.dialect) != expected_type
                            or stored['nullable'] != column.nullable):
                        raise ValueError(ERROR)
                primary = inspector.get_pk_constraint(table.name, schema=SCHEMA)['constrained_columns']
                if primary != [column.name for column in table.primary_key.columns]:
                    raise ValueError(ERROR)
                uniques = {tuple(item['column_names']) for item in inspector.get_unique_constraints(table.name, schema=SCHEMA)}
                expected_uniques = {tuple(column.name for column in item.columns) for item in table.constraints
                                    if isinstance(item, UniqueConstraint)}
                if uniques != expected_uniques:
                    raise ValueError(ERROR)
                indexes = {index['name']: index for index in inspector.get_indexes(table.name, schema=SCHEMA)}
                for index in table.indexes:
                    actual_index = indexes.get(index.name)
                    if (actual_index is None or actual_index['column_names'] != [column.name for column in index.columns]
                            or bool(actual_index['unique']) != bool(index.unique)):
                        raise ValueError(ERROR)
                # Legacy callbacks can receive fills/status before the order ACK.
                # Only these projection tables omit FKs; financial FKs stay intact.
                if inspector.get_foreign_keys(table.name, schema=SCHEMA):
                    raise ValueError(ERROR)
            version = conn.scalar(select(Metadata.value).where(Metadata.key == 'local_db_version'))
            if version != expected_version:
                raise ValueError(ERROR)
    except Exception:
        # Never include a configured URL, password or server detail in admission errors.
        raise ValueError(ERROR) from None
