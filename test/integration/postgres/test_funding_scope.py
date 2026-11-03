"""Funding pair discovery must stay fresh without scanning every old order."""
import pytest
from sqlalchemy import event, text

from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.funding_bill import FundingBillStore


@pytest.fixture
def scope_store(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    return FundingBillStore(pg_engine, 'hummingbot', owner_backend_pid=None)


def seed(engine):
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO orders (id,config_file_path,strategy,market,symbol,base_asset,quote_asset,
                creation_timestamp,order_type,amount,leverage,price,last_status,last_update_timestamp)
            SELECT 'scope-'||g,'test.yml','life_mm',CASE WHEN g%5=0 THEN 'okx' ELSE 'okx_perpetual' END,
                CASE WHEN g%5=0 THEN 'SPOTONLY-USDT' ELSE (ARRAY['BTC-USDT','ETH-USDT','AVAX-USDT'])[1+g%3] END,
                'TEST','USDT',1700000000000+g,'LIMIT_MAKER',.001,1,100,'OrderCancelled',1700000000000+g
            FROM generate_series(1,5000) g
        """))
        c.execute(text('ANALYZE orders'))


def test_scope_empty_and_spot_only_history(scope_store):
    assert scope_store.scope() == (set(), None)
    seed(scope_store.engine)
    with scope_store.engine.begin() as c:
        c.execute(text("DELETE FROM orders WHERE market='okx_perpetual'"))
    assert scope_store.scope() == (set(), None)


def test_scope_finds_unconfigured_pairs_and_late_historical_imports(scope_store):
    seed(scope_store.engine)
    assert scope_store.scope() == ({'BTC-USDT', 'ETH-USDT', 'AVAX-USDT'}, 1700000000001)
    with scope_store.engine.begin() as c:
        # A new, unconfigured pair and an earlier import for an existing pair
        # must be visible immediately, without TTL expiry or dirty signaling.
        c.execute(text("""INSERT INTO orders SELECT 'import-'||id,config_file_path,strategy,market,
            CASE WHEN id='scope-1' THEN 'NEW-USDT' ELSE symbol END,base_asset,quote_asset,
            CASE WHEN id='scope-1' THEN 1600000000000 ELSE 1500000000000 END,
            order_type,amount,leverage,price,last_status,last_update_timestamp,
            exchange_order_id,position,creation_pending FROM orders WHERE id IN ('scope-1','scope-2')"""))
    assert scope_store.scope() == ({'BTC-USDT', 'ETH-USDT', 'AVAX-USDT', 'NEW-USDT'}, 1500000000000)


def test_scope_reads_pair_boundaries_instead_of_all_history(scope_store):
    seed(scope_store.engine)
    statements = []

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters))

    event.listen(scope_store.engine, 'before_cursor_execute', observe)
    try:
        pairs, earliest = scope_store.scope()
    finally:
        event.remove(scope_store.engine, 'before_cursor_execute', observe)
    assert pairs == {'BTC-USDT', 'ETH-USDT', 'AVAX-USDT'} and earliest == 1700000000001
    assert len(statements) == 1
    statement, parameters = statements[0]
    with scope_store.engine.connect() as c:
        plan = c.exec_driver_sql('EXPLAIN (ANALYZE, FORMAT JSON) ' + statement, parameters).scalar_one()[0]['Plan']

    def leaf_rows(node):
        children = node.get('Plans', [])
        if children:
            return sum(leaf_rows(child) for child in children)
        return node['Actual Rows'] * node['Actual Loops']

    # A row-visit budget, independent of host scheduling and wall-clock speed.
    assert leaf_rows(plan) < 100
