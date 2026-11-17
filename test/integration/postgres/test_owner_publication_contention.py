"""Publication yields to row locks; unrelated statement failures remain distinct.

These are controlled mechanisms, not attribution of the historical startup exit.
"""
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


def test_publication_busy_authority_yields_without_acknowledging(pg_engine):
    store = publication_store(pg_engine)
    before = store.read()
    with pg_engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        transaction = blocker.begin()
        blocker.execute(text("SELECT bot_id FROM bot_state FOR UPDATE")).all()
        try:
            future = executor.submit(publish, store)
            assert future.result(timeout=3) is False
        finally:
            transaction.rollback()
    after = store.read()
    assert (after["acknowledged_version"], after["heartbeat"], after["status"]) == (
        before["acknowledged_version"], before["heartbeat"], before["status"])
    assert publish(store)  # The next observer cycle retries; no timeout/heartbeat is fabricated.
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
