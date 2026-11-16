"""Verify physical query work with a million retained, unrelated source fills."""
import json
import time
from pathlib import Path
from test.integration.postgres.test_overview_fee_projection import seed
from test.integration.postgres.test_overview_fees import store

from sqlalchemy import text

from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.overview_fee_projection import ATTEMPT_BATCH, SOURCE_BATCH
from scripts.test_mm_load import seed_history


def test_additive_reporting_migration_on_populated_legacy_schema_does_not_backfill_or_change_fills(pg_engine):
    admin = store(pg_engine)
    with admin.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE overview_fee_contributions, overview_fee_scopes')
        connection.exec_driver_sql('DROP INDEX idx_submission_attempts_overview_scope_order, idx_trade_fills_overview_order_market_trade')
        connection.execute(text("DELETE FROM hbot_schema_migrations WHERE name = '022_create_overview_fee_projection.sql'"))

    class Seeder:
        def sql(self, statement):
            with admin.engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 60):
                connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
                connection.execute(text(statement))

    seed_history(Seeder(), 100_000)
    with admin.engine.connect() as connection:
        before = connection.execute(text('SELECT count(*), sum(trade_fee_in_quote) FROM trade_fills')).one()
    assert bootstrap_postgres(admin.engine, auto_migrate=True)
    assert bootstrap_postgres(admin.engine, auto_migrate=True)
    with admin.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*), sum(trade_fee_in_quote) FROM trade_fills')).one() == before
        assert connection.execute(text('SELECT count(*) FROM overview_fee_contributions')).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM pg_indexes WHERE indexname IN "
                                       "('idx_submission_attempts_overview_scope_order','idx_trade_fills_overview_order_market_trade')")).scalar_one() == 2


def test_scope_and_fill_batches_do_not_scan_a_million_retained_rows(pg_engine):
    admin = store(pg_engine)

    class Seeder:
        def sql(self, statement):
            with admin.engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 120):
                connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
                connection.execute(text(statement))

    with admin.engine.begin() as connection:
        for table in ('orders', 'trade_fills', 'submission_attempts'):
            connection.exec_driver_sql(f'ALTER TABLE {table} SET (autovacuum_enabled = false)')
    seed_history(Seeder(), 1_000_000)
    Seeder().sql("""INSERT INTO submission_attempts (order_id,state,context)
        SELECT id, 'submitted', '{"bot_id":"other-bot","session":["other-session",0],"bindings":[]}'::jsonb
        FROM orders WHERE config_file_path='load-history.yaml'""")
    seed(admin.engine, 1000)
    # Many partial fills per order must not multiply physical work by both limits.
    Seeder().sql("""INSERT INTO trade_fills
        SELECT (jsonb_populate_record(NULL::trade_fills,
                    to_jsonb(t) || jsonb_build_object('exchange_trade_id', 'partial-' || n))).*
        FROM trade_fills t CROSS JOIN generate_series(1, 5) n
        WHERE t.order_id >= 'order-00000000' AND t.order_id < 'order-00000500'""")
    observations = {}

    def visited(node, relation):
        own = ((node.get('Actual Rows', 0) + node.get('Rows Removed by Filter', 0)
                + node.get('Rows Removed by Index Recheck', 0)) * node.get('Actual Loops', 1)
               if node.get('Relation Name') == relation else 0)
        return own + sum(visited(child, relation) for child in node.get('Plans', []))

    with admin.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
        connection.exec_driver_sql('SET LOCAL jit = off')
        for name, statement, arguments in (
            ('scope', ATTEMPT_BATCH, dict(bot_id='hummingbot', session_id='session', cursor_order='',
                                          include_cursor=False, batch_limit=501)),
            ('fills', SOURCE_BATCH, dict(orders=[f'order-{index:08d}' for index in range(500)],
                                         cursor_order='', cursor_market='', cursor_trade='', batch_limit=501)),
        ):
            plan = connection.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement.text), arguments).scalar_one()[0]
            observations[name] = dict(execution_ms=plan['Execution Time'], planning_ms=plan['Planning Time'],
                                      source_rows={relation: visited(plan['Plan'], relation)
                                                   for relation in ('trade_fills', 'submission_attempts')})
            assert observations[name]['source_rows']['trade_fills'] <= 2000
            assert observations[name]['source_rows']['submission_attempts'] <= 2000
            assert plan['Plan']['Actual Rows'] <= 501
    destination = Path('build/overview-reporting-tdd-20261116')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'million-row-query-plan.json').write_text(json.dumps(observations, indent=2) + '\n')
