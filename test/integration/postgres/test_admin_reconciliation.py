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


def test_funding_checkpoint_does_not_invalidate_trading_reconciliation(checks):
    from sqlalchemy import update

    from hummingbot.core.admin_reconciliation import ReconciliationRequest
    from hummingbot.model.bot_state import BotState
    store, _ = checks
    request = ReconciliationRequest(market="all")
    before = store.reconciliation_scope(request, 0)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(
            # Funding updates preserve the owner's journal/fencing metadata.
            recovery_state=BotState.recovery_state.op('||')({"funding_sync": {"covered_until_ms": 1800000000000}})))
    assert store.reconciliation_scope(request, 0)["fingerprint"] == before["fingerprint"]
    with store.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(
            recovery_state=BotState.recovery_state.op('||')({"funding_sync": {"covered_until_ms": 1800000120000}, "halt": True})))
    assert store.reconciliation_scope(request, 0)["fingerprint"] != before["fingerprint"]


def test_empty_check_queue_does_not_lock_control_row(checks):
    from sqlalchemy import event, select

    from hummingbot.model.bot_state import BotState
    store, owner = checks
    statements = []

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(store.engine, 'before_cursor_execute', observe)
    try:
        assert store.claim_check(owner.owner_uuid, owner.generation) is None
    finally:
        event.remove(store.engine, 'before_cursor_execute', observe)
    assert len(statements) == 1
    assert 'admin_commands' in statements[0] and 'FOR UPDATE' not in statements[0]
    # The idle path must also remain usable while a control transaction owns
    # the bot row. A tight SQL deadline turns accidental lock waits into failure.
    with store.engine.begin() as blocker:
        blocker.execute(select(BotState.bot_id).where(BotState.bot_id == store.bot_id).with_for_update())

        def deadline(connection):
            connection.exec_driver_sql("SET LOCAL lock_timeout = '150ms'")

        event.listen(store.engine, 'begin', deadline)
        try:
            assert store.claim_check(owner.owner_uuid, owner.generation) is None
        finally:
            event.remove(store.engine, 'begin', deadline)


@pytest.mark.parametrize('change', ['completed', 'owner_changed'])
def test_check_candidate_is_revalidated_after_unlocked_probe(checks, change):
    from sqlalchemy import event, update

    from hummingbot.model.admin_command import AdminCommand
    from hummingbot.model.bot_state import BotState
    store, owner = checks
    receipt = store.request_check('race-check', {'market': 'spot'})
    changed = []

    def between_probe_and_claim(connection, cursor, statement, parameters, context, many):
        if changed or not statement.startswith('SELECT admin_commands.id ') or 'FOR UPDATE' in statement:
            return
        changed.append(True)
        with store.engine.begin() as concurrent:
            if change == 'completed':
                concurrent.execute(update(AdminCommand).where(AdminCommand.id == receipt['id']).values(status='succeeded'))
            else:
                concurrent.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(owner_generation=owner.generation + 1))

    event.listen(store.engine, 'after_cursor_execute', between_probe_and_claim)
    try:
        assert store.claim_check(owner.owner_uuid, owner.generation) is None
    finally:
        event.remove(store.engine, 'after_cursor_execute', between_probe_and_claim)
    assert changed == [True]
    assert store.command_by_id(receipt['id'])['status'] == ('succeeded' if change == 'completed' else 'accepted')


def test_stale_check_worker_cannot_claim_but_current_owner_can_reclaim(checks):
    store, owner = checks
    receipt = store.request_check('reclaim-check', {'market': 'spot'})
    assert store.claim_check('stale-owner', owner.generation) is None
    first = store.claim_check(owner.owner_uuid, owner.generation)
    assert first['id'] == receipt['id'] and first['status'] == 'applying'
    assert store.claim_check(owner.owner_uuid, owner.generation - 1) is None
    second = store.claim_check(owner.owner_uuid, owner.generation)
    assert second['id'] == receipt['id']
    assert second['result']['lease'] == {'owner_uuid': owner.owner_uuid, 'generation': owner.generation}
