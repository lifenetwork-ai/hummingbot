"""Reproducible reporting measurements; milliseconds are evidence, not gates."""
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.integration.postgres.test_performance import measure, queries, report

from sqlalchemy import event, text

from hummingbot.model.admin_reporting import daily, save_daily_balances
from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from scripts.test_mm_load import seed_history


def test_reporting_large_history_measurement(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)

    class Seeder:
        def sql(self, sql):
            with pg_engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 30):
                connection.execute(text("SET LOCAL statement_timeout='30s'"))
                connection.execute(text(sql))
    seed_history(Seeder(), 100000)

    def read():
        return daily(pg_engine, reporting_timezone="UTC")

    with queries(pg_engine) as statements:
        result = read()
    assert sum(day["fill_count"] for market in result["markets"] for day in market["days"]) == 100000
    assert sum(day["fees"][0]["net"] for market in result["markets"] for day in market["days"]) == Decimal("0.1")
    report("daily_100000_fills", {**measure(read, samples=11), "queries": len(statements)})
    captured = []

    def capture(connection, cursor, statement, params, *args):
        if "AS fill_count" in statement or "AS currency" in statement:
            captured.append((statement, params))

    event.listen(pg_engine, "before_cursor_execute", capture)
    try:
        read()
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)
    with pg_engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 30):
        # Measure the deliberately JIT-enabled baseline even when compilation
        # exceeds the runtime budget. Actual daily() reads retain their 2s limit.
        connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
        original_jit = connection.exec_driver_sql("SHOW jit").scalar_one()
        before = [connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).scalar_one()
                  for sql, params in captured]
        connection.exec_driver_sql("SET LOCAL jit = off")
        after = [connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).scalar_one()
                 for sql, params in captured]
    report("daily_aggregate_plans", {"default_jit": original_jit, "default": before, "jit_off": after})
    with pg_engine.connect() as connection:
        assert connection.exec_driver_sql("SHOW jit").scalar_one() == original_jit
    assert len(statements) <= 6


def test_reporting_snapshot_measurement(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    observed = (datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()
    accounts = [dict(account_id="benchmark", identity_verified=True, environment="demo", balances=[dict(
        currency=f"COIN{i}", connector="okx", source="okx_account_balance", observed_at=observed,
        cash_balance="100", available_cash="90", equity="100", available_equity="90") for i in range(100)])]

    def write():
        with pg_engine.begin() as connection:
            return save_daily_balances(connection, accounts, "UTC")

    assert write() == 100
    with queries(pg_engine) as statements:
        assert write() == 0
    report("snapshot_100_currencies_no_change", {**measure(write, samples=11), "queries": len(statements)})
    assert len(statements) == 1
