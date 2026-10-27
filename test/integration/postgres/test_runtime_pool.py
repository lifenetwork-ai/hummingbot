"""Retain a bounded warm pool without weakening the dedicated owner session."""
from contextlib import ExitStack

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError

from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_owner_and_reader_bursts_reuse_connections_with_the_same_five_connection_ceiling(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    connected = []

    def created(connection, record):
        connected.append(connection)

    event.listen(pg_engine, "connect", created)
    try:
        for index in range(20):
            with ExitStack() as stack:
                readers = [stack.enter_context(pg_engine.connect()) for _ in range(4)]
                assert all(reader.execute(text("SELECT 1")).scalar_one() == 1 for reader in readers)
                assert owner.check_owner()["owner_uuid"] == owner.owner_uuid
                if index == 0:
                    with pytest.raises(TimeoutError):
                        pg_engine.connect()
        # One already-warm owner plus four readers; repeated bursts add no new backend.
        assert len(connected) <= 4
    finally:
        event.remove(pg_engine, "connect", created)
        owner.release()


def test_idle_backend_reconnect_does_not_reconnect_a_lost_owner(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    try:
        with pg_engine.connect() as idle:
            idle_pid = idle.execute(text("SELECT pg_backend_pid()")).scalar_one()
        # Terminate an idle reader using the distinct owner session.
        owner.connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": idle_pid})
        owner.connection.commit()
        with pg_engine.connect() as reader:
            assert reader.execute(text("SELECT pg_backend_pid()")).scalar_one() != idle_pid
            reader.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": owner.backend_pid})
            reader.commit()
        with pytest.raises(OwnershipLost):
            owner.check_owner()
        assert owner.lost and not owner.allow_new_orders
        with pytest.raises(OwnershipLost):
            owner.try_acquire()
    finally:
        owner.release()
