"""Distinguish publication lock waits from statement timeouts on real PostgreSQL.

These are controlled mechanisms, not attribution of the historical startup exit.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def publication_store(engine):
    bootstrap_postgres(engine, auto_migrate=True)
    store = BotStateStore(engine, "hummingbot")
    store.ensure()
    with engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET owner_uuid='owner', owner_generation=1, control_version=1"))
    return store


def publish(store):
    return store.publish(owner_uuid="owner", owner_generation=1, version=1,
                         status={"running": False}, save_balances=False)


def test_publication_lock_timeout_has_blocker_and_cannot_acknowledge(pg_engine):
    store = publication_store(pg_engine)
    before = store.read()
    with pg_engine.connect() as blocker, pg_engine.connect() as witness, ThreadPoolExecutor(max_workers=1) as executor:
        transaction = blocker.begin()
        pid = blocker.execute(text("SELECT pg_backend_pid() FROM bot_state FOR UPDATE")).scalar_one()
        try:
            future = executor.submit(publish, store)
            observed = None
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not future.done():
                # A new transaction avoids caching pg_stat_activity snapshots.
                observed = witness.execute(text("""SELECT wait_event_type, wait_event FROM pg_stat_activity
                    WHERE :pid = ANY(pg_blocking_pids(pid)) AND query LIKE 'SELECT bot_state.status%%'"""),
                                           {"pid": pid}).first()
                witness.commit()
                if observed:
                    break
                time.sleep(.01)
            assert observed is not None and observed.wait_event_type == "Lock"
            with pytest.raises(OperationalError) as error:
                future.result(timeout=4)
            assert error.value.orig.pgcode == "55P03"  # lock_timeout=1s, not statement_timeout=2s
            assert not error.value.connection_invalidated
        finally:
            transaction.rollback()
    after = store.read()
    assert (after["acknowledged_version"], after["heartbeat"], after["status"]) == (
        before["acknowledged_version"], before["heartbeat"], before["status"])
    assert publish(store)  # A later healthy invocation may publish; no retry was hidden in the failed call.
    assert store.read()["acknowledged_version"] == 1


def test_unrelated_row_lock_does_not_block_publication(pg_engine):
    store = publication_store(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("CREATE TABLE publication_unrelated_row (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO publication_unrelated_row VALUES (1)"))
    with pg_engine.begin() as blocker:
        blocker.execute(text("SELECT id FROM publication_unrelated_row FOR UPDATE"))
        assert publish(store)
    assert store.read()["acknowledged_version"] == 1


def test_statement_timeout_is_distinct_and_rolls_back(pg_engine):
    with pg_engine.connect() as connection:
        assert connection.execute(text("SHOW statement_timeout")).scalar_one() == "2s"
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "1s"
        with pytest.raises(OperationalError) as error:
            connection.execute(text("SELECT pg_sleep(2.5)"))
        assert error.value.orig.pgcode == "57014"
        connection.rollback()
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
