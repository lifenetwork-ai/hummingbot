import time

import pytest
from sqlalchemy import text

from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from scripts.test_mm_load import seed_history


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


@pytest.mark.parametrize("table,columns,index", [
    ("orders", "creation_timestamp DESC,id DESC", "idx_orders_creation_timestamp_id"),
    ("trade_fills", "timestamp DESC,market DESC,order_id DESC,exchange_trade_id DESC", "idx_trade_fills_timestamp_market_order_id_exchange_trade_id"),
])
def test_default_all_market_pages_use_bounded_index_order_on_large_history(pg_engine, table, columns, index):
    bootstrap_postgres(pg_engine, auto_migrate=True)

    class Seeder:
        def sql(self, sql):
            with pg_engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 30):
                connection.execute(text("SET LOCAL statement_timeout='30s'"))
                connection.execute(text(sql))
    seed_history(Seeder(), 100000)
    with pg_engine.connect() as connection:
        plan = connection.execute(text(f"EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM {table} WHERE market IN ('okx','okx_perpetual') ORDER BY {columns} LIMIT 100")).scalar_one()[0]["Plan"]
    assert index in {node.get("Index Name") for node in nodes(plan)}, plan
    assert not {"Sort", "Seq Scan"} & {node["Node Type"] for node in nodes(plan)}
