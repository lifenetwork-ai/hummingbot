import pytest

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def checks(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    try:
        yield AdminStore(state), owner
    finally:
        owner.release()


def test_check_retry_and_owner_fencing_never_change_trading_authority(checks):
    store, owner = checks
    from sqlalchemy import update

    from hummingbot.model.bot_state import BotState
    with store.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(recovery_state={"halt": True}))
    before = store.store.read()
    payload = {"market": "all"}
    first = store.request_check("check-1", payload)
    assert first["status"] == "accepted"
    assert store.request_check("check-1", payload)["id"] == first["id"]
    with pytest.raises(ControlConflict):
        store.request_check("check-1", {"market": "spot"})
    with pytest.raises(ControlConflict):
        store.request_check("check-2", payload)
    claim = store.claim_check(owner.owner_uuid, owner.generation)
    assert claim["id"] == first["id"]
    report = {"state": "incomplete", "markets": [], "started_at": None, "finished_at": None}
    assert not store.finish_check(first["id"], "old-owner", owner.generation, report)
    assert store.finish_check(first["id"], owner.owner_uuid, owner.generation, report)
    latest = store.latest_check()
    assert latest["status"] == "succeeded" and latest["result"]["state"] == "incomplete"
    after = store.store.read()
    for key in ("control_version", "desired_state", "mm_session", "recovery_state"):
        assert after[key] == before[key]


def test_owner_unavailable_check_is_terminal_without_starting_a_strategy(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    store = AdminStore(state)
    receipt = store.request_check("offline-check", {"market": "spot"})
    assert receipt["result"]["state"] == "unavailable"
    assert receipt["status"] == "failed"
    assert store.store.read()["control_version"] == 0
