"""A bad optional balance sample cannot roll back a healthy control receipt."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import DataError, OperationalError

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def publication(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    store.control(expected_version=0, desired_state="stopped")
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    now = datetime.now(timezone.utc)
    with pg_engine.begin() as connection:
        connection.execute(AdminCommand.__table__.insert().values(
            id="stop-command", bot_id="hummingbot", idempotency_key="stop-key", request_hash="hash",
            action="stop", expected_version=0, effective_version=1, status="accepted", payload={},
            created_at=now, updated_at=now))
    status = dict(running=False, engine=dict(strategy_running=False, clock_running=False), reporting={"accounts": []})
    try:
        yield store, owner, status
    finally:
        owner.release()


def publish(store, owner, status):
    return store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status)


@pytest.mark.parametrize("error", [ValueError("bad snapshot"), DataError("sample", {}, ValueError("invalid numeric"))])
def test_optional_daily_snapshot_failure_commits_real_control_outcome(publication, monkeypatch, error):
    store, owner, status = publication

    def broken(*args):
        raise error

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances", broken)
    assert publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 1
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"
    assert store.read()["status"]["daily_snapshot_error"] == type(error).__name__


def test_actual_bad_pg_value_is_rolled_back_to_savepoint_only(publication):
    store, owner, status = publication
    status["reporting"] = dict(accounts=[dict(account_id="account", environment="demo", identity_verified=True, balances=[dict(
        currency="USDT", connector="okx", source="okx_account_balance", observed_at=datetime.now(timezone.utc).timestamp(),
        cash_balance="not-a-number")])])
    assert publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 1
    assert store.read()["status"]["daily_snapshot_error"]
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"


def test_skipping_optional_snapshot_avoids_savepoint_and_keeps_required_control_commit(publication, monkeypatch):
    from sqlalchemy import event
    store, owner, status = publication
    statements = []

    def statement(connection, cursor, sql, parameters, context, many):
        statements.append(sql.upper())

    def broken(*args):
        pytest.fail("Skipped optional snapshot must not execute")

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances", broken)
    event.listen(store.engine, "before_cursor_execute", statement)
    try:
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                             status=status, save_balances=False)
    finally:
        event.remove(store.engine, "before_cursor_execute", statement)
    assert not any("SAVEPOINT" in sql for sql in statements)
    assert store.read()["acknowledged_version"] == 1
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"


@pytest.mark.parametrize("target", ["snapshot", "receipt"])
def test_critical_publication_failure_still_rolls_back_and_propagates(publication, monkeypatch, target):
    store, owner, status = publication

    def broken(*args):
        raise OperationalError("critical-query", {}, RuntimeError("connection lost"))

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances" if target == "snapshot" else
                        "hummingbot.model.admin_store.AdminStore.record_observation", broken)
    with pytest.raises(OperationalError):
        publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 0
    assert AdminStore(store).command_by_id("stop-command")["status"] == "accepted"
