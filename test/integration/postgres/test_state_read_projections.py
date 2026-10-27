from sqlalchemy import text

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_publication_projection_excludes_private_recovery_and_catalogue_but_reads_fresh_control(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(
            recovery_state={"halt": True, "private_diagnostic": "not_for_reporting"},
            mm_session={"id": "session", "generation": 2, "status": "running", "catalogue": {"large": "configuration"}},
            status={"running": False, "phase": "halted"}))
    full = store.read()
    public = store.read(publication=True)
    assert "recovery_state" not in public
    assert public["recovery_summary"] == {"halted": True, "pending_mutations": False}
    assert "private_diagnostic" not in str(public)
    assert "catalogue" not in public["mm_session"]
    assert public["mm_session"] == {key: value for key, value in full["mm_session"].items() if key != "catalogue"}
    assert public["status"] == full["status"]
    assert full["recovery_state"]["halt"] and "catalogue" in full["mm_session"]
    store.control(expected_version=0)
    assert store.read(publication=True)["control_version"] == 1
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET mm_session=NULL"))
    assert store.read(publication=True)["mm_session"] is None
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(mm_session=None))
    assert store.read()["mm_session"] is None
    assert store.read(publication=True)["mm_session"] is None


def test_owner_gate_keeps_fresh_authority_without_reporting_payload(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    try:
        with pg_engine.begin() as connection:
            connection.execute(BotState.__table__.update().values(status={"large_report": "unused"}, recovery_state={"halt": True}))
        row = owner.check_owner()
        assert "status" not in row
        assert row["recovery_state"]["halt"] is True
        assert row["backend_pid"] == owner.backend_pid and row["owner_generation"] == owner.generation
        store.control(expected_version=0, desired_state="stopped")
        assert owner.check_owner()["control_version"] == 1
    finally:
        owner.release()
