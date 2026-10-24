import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model import get_declarative_base
from hummingbot.model.bot_state import BotState
from hummingbot.model.db_migration.postgres.runner import (
    MIGRATION_LOCK,
    SCRIPTS,
    PostgreSQLBootstrapError,
    bootstrap_postgres,
    validate_schema,
)
from hummingbot.model.inventory_cost import InventoryCost
from hummingbot.model.market_state import MarketState
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType


def test_unqualified_scripts_use_runner_search_path_and_replay(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA hbot_test_shadow"))
        connection.execute(text("SET search_path = hbot_test_shadow"))
    try:
        for enabled in (True, True, False):
            bootstrap_postgres(pg_engine, auto_migrate=enabled)
        with pg_engine.connect() as connection:
            assert set(inspect(connection).get_table_names(schema="public")) == set(get_declarative_base().metadata.tables)
            assert inspect(connection).get_table_names(schema="hbot_test_shadow") == []
            validate_schema(connection)
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text("SET search_path = public"))
            connection.execute(text("DROP SCHEMA hbot_test_shadow"))


def test_empty_and_populated_replay_100_times(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with Session(pg_engine) as session, session.begin():
        session.add(BotState(bot_id="test", desired_state="stopped", status={"recover": True}))
        session.add(InventoryCost(base_asset="BTC", quote_asset="USDT", base_volume=Decimal("1.000000000000000001"),
                                  quote_volume=Decimal("12345.123456789012345678")))
        session.add(MarketState(config_file_path="test.yml", market="okx", timestamp=1720000000123,
                                saved_state={"nested": ["state"]}))
    for _ in range(100):
        bootstrap_postgres(pg_engine, auto_migrate=True)
    with Session(pg_engine) as session:
        assert session.query(BotState).count() == 1
        assert session.query(MarketState).one().saved_state == {"nested": ["state"]}
        cost = session.query(InventoryCost).one()
        assert cost.base_volume == Decimal("1.000000000000000001")
        assert cost.quote_volume == Decimal("12345.123456789012345678")
    with pg_engine.connect() as connection:
        validate_schema(connection)
        identifiers = connection.execute(text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public'
        """)).scalars().all()
        assert all(name == name.lower() and len(name) <= 63 for name in identifiers)
        assert set(inspect(connection).get_table_names(schema="public")) == set(get_declarative_base().metadata.tables)


def test_populated_previous_canonical_schema_adds_recovery_columns_without_data_loss(pg_engine):
    with pg_engine.begin() as connection:
        for script in sorted(SCRIPTS.glob("*.sql"))[:5]:
            connection.exec_driver_sql(script.read_text())
        connection.execute(text("""INSERT INTO public.orders
            (id,config_file_path,strategy,market,symbol,base_asset,quote_asset,creation_timestamp,
             order_type,amount,leverage,price,last_status,last_update_timestamp)
            VALUES ('previous-order','old.yml','pure_market_making','okx','BTC-USDT','BTC','USDT',
                    1720000000123,'LIMIT',1000001,1,99123456,'OrderCreated',1720000000123)"""))
        connection.execute(text("INSERT INTO public.bot_state (bot_id,control_version,status) VALUES ('test',7,'{}')"))
        connection.exec_driver_sql("INSERT INTO public.market_states (config_file_path,market,timestamp,saved_state) "
                                   "VALUES ('old.yml','okx',1720000000123,'{\"tracked\":true}')")
    for automatic in (True, True, False):
        bootstrap_postgres(pg_engine, auto_migrate=automatic)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT amount,price,creation_timestamp,creation_pending FROM public.orders")).one() == (
            1000001, 99123456, 1720000000123, False)
        assert connection.execute(text("SELECT control_version,recovery_state FROM public.bot_state")).one() == (7, {})
        assert connection.execute(text("SELECT saved_state FROM public.market_states")).scalar() == {"tracked": True}


def test_auto_disabled_absent_and_valid_schema(pg_engine):
    with pytest.raises(PostgreSQLBootstrapError, match="missing table"):
        bootstrap_postgres(pg_engine, auto_migrate=False)
    assert inspect(pg_engine).get_table_names() == []
    bootstrap_postgres(pg_engine, auto_migrate=True)
    # Real limited role proves validation has no implicit DDL or data writes.
    with pg_engine.begin() as connection:
        connection.execute(text("DO $$ BEGIN CREATE ROLE hbot_test_dml; EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO hbot_test_dml"))
        connection.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hbot_test_dml"))
        connection.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hbot_test_dml"))
    with pg_engine.connect() as connection:
        connection.execute(text("SET ROLE hbot_test_dml"))
        connection.commit()
        try:
            validate_schema(connection)
            connection.execute(text("INSERT INTO public.bot_state(bot_id) VALUES ('limited')"))
            connection.commit()
        finally:
            connection.execute(text("RESET ROLE"))
            connection.commit()


@pytest.mark.parametrize("ddl,expected", [
    ("DROP INDEX public.idx_orders_config_file_path_creation_timestamp; CREATE INDEX idx_orders_config_file_path_creation_timestamp ON public.orders(market)", "index mismatch"),
    ("ALTER TABLE public.orders ALTER COLUMN amount TYPE NUMERIC", "type/nullability mismatch"),
    ("ALTER TABLE public.orders DROP CONSTRAINT pk_orders CASCADE", "primary key mismatch"),
    ("ALTER TABLE public.bot_state ALTER COLUMN desired_state SET DEFAULT 'running'", "default mismatch"),
    ("ALTER TABLE public.bot_state DROP CONSTRAINT chk_bot_state_desired_state", "check constraint mismatch"),
    ("ALTER TABLE public.order_statuses DROP CONSTRAINT fk_order_statuses_order_id", "foreign key mismatch"),
    ("ALTER TABLE public.orders ADD COLUMN incompatible TEXT NOT NULL", "incompatible required column"),
])
def test_drift_rejected_with_migrations_enabled_or_disabled(pg_engine, ddl, expected):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with pg_engine.begin() as connection:
        connection.exec_driver_sql(ddl)
    for enabled in (False, True):
        with pytest.raises(PostgreSQLBootstrapError, match=expected):
            bootstrap_postgres(pg_engine, auto_migrate=enabled)


def test_full_script_transaction_and_recovery(pg_engine, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE IF NOT EXISTS public.probe_committed(id integer); INSERT INTO public.probe_committed VALUES (1);")
    (tmp_path / "002_fail.sql").write_text("CREATE TABLE public.probe_rolled_back(id integer); SELECT 1/0;")
    with pytest.raises(PostgreSQLBootstrapError, match="002_fail.sql"):
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    tables = inspect(pg_engine).get_table_names()
    assert "probe_committed" in tables
    assert "probe_rolled_back" not in tables
    with pg_engine.begin() as connection:
        connection.execute(text("DROP TABLE public.probe_committed"))
    bootstrap_postgres(pg_engine, auto_migrate=True)


def test_do_block_not_split_on_semicolons(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    (tmp_path / "001_block.sql").write_text("DO $$ BEGIN PERFORM 1; PERFORM 2; END $$; SELECT 3;")
    bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)


def test_concurrent_bootstrap_and_lock_timeout(pg_engine):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(bootstrap_postgres, pg_engine, auto_migrate=True) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    with pg_engine.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK})
        owner.commit()
        try:
            with pytest.raises(PostgreSQLBootstrapError, match="lock deadline"):
                bootstrap_postgres(pg_engine, auto_migrate=True, lock_wait=0.2)
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
            owner.commit()
    bootstrap_postgres(pg_engine, auto_migrate=True)


def test_total_deadline_cancels_multi_statement_file(pg_engine, tmp_path):
    (tmp_path / "001_slow.sql").write_text("CREATE TABLE public.probe_slow(id integer); SELECT pg_sleep(3); SELECT pg_sleep(3);")
    started = time.monotonic()
    with pytest.raises(PostgreSQLBootstrapError):
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path, total_timeout=0.3)
    assert time.monotonic() - started < 3
    assert "probe_slow" not in inspect(pg_engine).get_table_names()
    bootstrap_postgres(pg_engine, auto_migrate=True)


def test_terminated_migration_session_rolls_back_and_releases_lock(pg_engine, tmp_path):
    (tmp_path / "001_interrupted.sql").write_text("CREATE TABLE public.probe_killed(id integer); SELECT pg_sleep(20);")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bootstrap_postgres, pg_engine, auto_migrate=True, scripts=tmp_path)
        deadline = time.monotonic() + 5
        pid = None
        while time.monotonic() < deadline:
            with pg_engine.connect() as connection:
                pid = connection.execute(text("SELECT pid FROM pg_stat_activity WHERE wait_event='PgSleep' AND datname=current_database() LIMIT 1")).scalar()
                if pid:
                    connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
                    connection.commit()
                    break
            threading.Event().wait(0.02)
        assert pid, "Migration never reached the test barrier"
        with pytest.raises(PostgreSQLBootstrapError):
            future.result(timeout=5)
    assert "probe_killed" not in inspect(pg_engine).get_table_names()
    bootstrap_postgres(pg_engine, auto_migrate=True)


@pytest.mark.parametrize("canonical_also_present", [False, True])
def test_legacy_names_fail_without_creating_parallel_tables(pg_engine, canonical_also_present):
    with pg_engine.begin() as connection:
        connection.execute(text('CREATE TABLE public."Order" (id text PRIMARY KEY)'))
        if canonical_also_present:
            connection.execute(text("CREATE TABLE public.orders (id text PRIMARY KEY)"))
    for enabled in (True, False):
        with pytest.raises(PostgreSQLBootstrapError, match="cutover"):
            bootstrap_postgres(pg_engine, auto_migrate=enabled)
    assert set(inspect(pg_engine).get_table_names()) == ({"Order", "orders"} if canonical_also_present else {"Order"})


def test_connection_manager_uses_pg_without_file_or_orm_ddl(pg_engine):
    settings = RuntimeSettings.from_environment({"DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"],
                                                "ENABLE_AUTO_MIGRATION": "true"})
    original_engine = SQLConnectionManager._postgres_engine
    original_singleton = SQLConnectionManager._scm_trade_fills_instance
    try:
        with patch("hummingbot.model.sql_connection_manager.data_path", side_effect=AssertionError("No file path")), \
                patch.object(get_declarative_base().metadata, "create_all", side_effect=AssertionError("No ORM DDL")):
            engine = SQLConnectionManager.initialize_postgres(settings)
            manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
            assert manager.engine is engine
            assert manager.db_path is None
            with manager.get_new_session() as session:
                assert session.query(BotState).count() == 0
    finally:
        if SQLConnectionManager._postgres_engine is not None:
            SQLConnectionManager._postgres_engine.dispose()
        SQLConnectionManager._postgres_engine = original_engine
        SQLConnectionManager._scm_trade_fills_instance = original_singleton


def test_explicit_migration_ignores_auto_disabled(pg_engine, tmp_path):
    env = {**os.environ, "DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"],
           "ENABLE_AUTO_MIGRATION": "false", "K_SERVICE": "offline-test"}
    env.pop("HBOT_ENV_FILE", None)
    root = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = str(root)
    result = subprocess.run([sys.executable, "-m", "hummingbot.model.db_migration.postgres"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "postgres:hbot_test_only" not in result.stderr
    assert "orders" in inspect(pg_engine).get_table_names()


def test_explicit_legacy_cutover_preserves_data_sequences_and_replays(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with Session(pg_engine) as session, session.begin():
        session.add(MarketState(config_file_path="legacy.yml", market="okx", timestamp=1720000000123,
                                saved_state={"order": "existing"}))
    with pg_engine.begin() as connection:
        connection.execute(text('ALTER TABLE public.market_states RENAME TO "MarketState"'))
        connection.execute(text('ALTER SEQUENCE public.seq_market_states_id RENAME TO "MarketState_id_seq"'))
        connection.execute(text('ALTER TABLE public."MarketState" RENAME CONSTRAINT pk_market_states TO "MarketState_pkey"'))
        connection.execute(text('ALTER INDEX public.uq_market_states_config_file_path_market RENAME TO ms_config_market_index'))
    bootstrap_postgres(pg_engine, auto_migrate=True, legacy_cutover=True)
    bootstrap_postgres(pg_engine, auto_migrate=True, legacy_cutover=True)
    with Session(pg_engine) as session, session.begin():
        original = session.query(MarketState).one()
        assert original.id == 1 and original.saved_state == {"order": "existing"}
        session.add(MarketState(config_file_path="new.yml", market="okx", timestamp=1720000000124, saved_state={}))
        session.flush()
        assert session.query(MarketState).filter_by(config_file_path="new.yml").one().id == 2


def test_cutover_failure_rolls_back_renames_and_new_schema(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text('CREATE TABLE public."Order" (id text PRIMARY KEY)'))
        connection.execute(text('INSERT INTO public."Order" VALUES (\'existing\')'))
    with pytest.raises(PostgreSQLBootstrapError):
        # An intentionally incomplete legacy table fails full schema validation.
        bootstrap_postgres(pg_engine, auto_migrate=True, legacy_cutover=True)
    assert inspect(pg_engine).get_table_names() == ["Order"]
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT id FROM public."Order"')).scalar() == "existing"


def test_cutover_both_names_fails_atomically(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text('CREATE TABLE public."Order" (id text PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE public.orders (id text PRIMARY KEY)'))
    with pytest.raises(PostgreSQLBootstrapError):
        bootstrap_postgres(pg_engine, auto_migrate=True, legacy_cutover=True)
    assert set(inspect(pg_engine).get_table_names()) == {"Order", "orders"}
