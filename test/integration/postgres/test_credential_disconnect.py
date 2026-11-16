from test.integration.postgres.test_credential_store import managed as managed_fixture, save

import pytest
from sqlalchemy import update

from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict

managed = managed_fixture


def loaded(managed, *, both=False):
    store, actor, cipher = managed
    receipt = save(store, actor, cipher)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid="owner", owner_generation=1))
    assert store.finish_set(receipt["id"], "owner", 1, versions={"okx": 1})
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(acknowledged_version=1))
    if both:
        receipt = save(store, actor, cipher, connector="okx_perpetual", key="perpetual", expected_control_version=1)
        assert store.finish_set(receipt["id"], "owner", 1, versions={"okx_perpetual": 1})
        with store.engine.begin() as connection:
            connection.execute(update(BotState).values(acknowledged_version=2))
    return store, actor


def disconnect(store, actor, *, control=1, key="disconnect"):
    return store.accept_disconnect(actor, exchange="okx", connector="okx", environment="demo", expected_version=1,
                                   expected_control_version=control, idempotency_key=key, request_id="disconnect-request")


def test_disconnect_without_session_retains_secret_until_automatic_cleanup_is_confirmed(managed):
    store, actor = loaded(managed)
    result = disconnect(store, actor)
    row, saved = store.store.read(), store.load("okx", "okx", "demo")
    assert result["status"] == "accepted" and result["session_id"] is None
    assert result["action"] == "credentials_disconnect" and row["desired_state"] == "stopped"
    assert row["control_version"] == 2 and row["recovery_state"]["credential_operation"]["id"] == result["id"]
    assert saved["state"] == "revoking" and saved["envelope"] is not None
    assert disconnect(store, actor) == result
    assert store.operation(result["id"])["status"] != "succeeded"


def test_disconnect_fences_running_bot_and_includes_companion_but_preserves_unrelated_halt(managed):
    store, actor = loaded(managed, both=True)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(desired_state="running", recovery_state={"runtime_active": True,
                                                                                            "halt": True, "reason": "operator review remains required"}))
    result = disconnect(store, actor, control=2)
    state = store.store.read()
    operation = state["recovery_state"]["credential_operation"]
    assert state["desired_state"] == "stopped" and state["recovery_state"]["halt"] is True
    assert operation["affected_versions"] == {"okx": 1, "okx_perpetual": 1}
    assert store.load("okx", "okx_perpetual", "demo")["state"] == "active"
    for action in ("stopped", "running"):
        with pytest.raises(ControlConflict) as error:
            store.store.control(expected_version=result["effective_version"], desired_state=action)
        assert error.value.code == "credential_operation_in_progress"


def test_ordinary_observation_cannot_complete_disconnect_and_stale_owner_cannot_erase(managed):
    from hummingbot.model.admin_store import AdminStore
    store, actor = loaded(managed)
    receipt = disconnect(store, actor)
    with store.engine.begin() as connection:
        AdminStore.record_observation(connection, store.bot_id, receipt["effective_version"],
                                      dict(running=False, engine=dict(strategy_running=False, clock_running=False)), dict(desired_state="stopped", mm_session=None))
    assert store.operation(receipt["id"])["status"] == "accepted"
    assert not store.finish_disconnect(receipt["id"], "stale-owner", 0, evidence={})
    assert store.load("okx", "okx", "demo")["envelope"] is not None


def test_removal_requires_current_flat_proof_and_detachment_and_is_atomic_with_audit(managed):
    store, actor = loaded(managed, both=True)
    receipt = disconnect(store, actor, control=2)
    proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True, consumers_detached=True,
                 observed_at=float(store.store.read()["database_now"]), owner_generation=1)
    assert not store.finish_disconnect(receipt["id"], "owner", 1, evidence=proof)
    assert store.progress_disconnect(receipt["id"], "owner", 1, stage="detaching", evidence=proof)
    assert store.finish_disconnect(receipt["id"], "owner", 1, evidence=proof)
    assert store.load("okx", "okx", "demo")["state"] == "revoked"
    assert store.load("okx", "okx", "demo")["envelope"] is None
    assert store.load("okx", "okx_perpetual", "demo")["state"] == "active"
    assert store.operation(receipt["id"])["status"] == "succeeded"
    assert store.store.read()["desired_state"] == "stopped"
    assert "credential_operation" not in store.store.read()["recovery_state"]


def test_nonzero_checkpoint_or_unknown_order_prevents_erasure(managed):
    from hummingbot.model.position import Position
    store, actor = loaded(managed)
    receipt = disconnect(store, actor)
    proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True, consumers_detached=True,
                 observed_at=float(store.store.read()["database_now"]), owner_generation=1)
    store.progress_disconnect(receipt["id"], "owner", 1, stage="detaching", evidence=proof)
    with store.engine.begin() as connection:
        connection.execute(Position.__table__.insert().values(id="residual", controller_id="controller", connector_name="okx_perpetual",
                                                              side="BUY", trading_pair="BTC-USDT", amount="0.00000001", breakeven_price=100, timestamp=1,
                                                              volume_traded_quote=0, realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=0))
    assert not store.finish_disconnect(receipt["id"], "owner", 1, evidence=proof)
    assert store.load("okx", "okx", "demo")["envelope"] is not None


def test_failed_final_audit_rolls_back_secret_removal_and_keeps_durable_fence(managed, monkeypatch):
    store, actor = loaded(managed)
    receipt = disconnect(store, actor)
    proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True, consumers_detached=True,
                 observed_at=float(store.store.read()['database_now']), owner_generation=1)
    store.progress_disconnect(receipt['id'], 'owner', 1, stage='detaching', evidence=proof)

    def failed(*args, **kwargs):
        raise RuntimeError('injected audit failure')
    monkeypatch.setattr(store.access, 'audit', failed)
    with pytest.raises(RuntimeError):
        store.finish_disconnect(receipt['id'], 'owner', 1, evidence=proof)
    assert store.load('okx', 'okx', 'demo')['envelope'] is not None
    assert store.store.read()['recovery_state']['credential_operation']['id'] == receipt['id']


def test_absent_owner_accepts_cleanup_but_cannot_report_completion_or_remove_secret(managed):
    store, actor = loaded(managed)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid=None))
    receipt = disconnect(store, actor)
    assert receipt['status'] == 'accepted' and receipt['result']['stage'] == 'waiting_for_owner'
    assert not store.finish_disconnect(receipt['id'], 'owner', 1, evidence={})
    assert store.load('okx', 'okx', 'demo')['envelope'] is not None


@pytest.mark.parametrize('change', [None, 'operation', 'version', 'command', 'companion', 'running'])
def test_retiring_credential_binding_requires_exact_current_disconnect(managed, monkeypatch, change):
    from sqlalchemy.orm import Session

    from hummingbot.model.admin_command import AdminCommand
    from hummingbot.model.exchange_credential import ExchangeCredential
    from hummingbot.model.runtime_journal import bindings, retiring_bindings
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, actor = loaded(managed, both=True)
    with Session(store.engine) as session:
        expected = bindings(session, store.bot_id)
    receipt = disconnect(store, actor, control=2)
    with Session(store.engine) as session, session.begin():
        selected = session.get(ExchangeCredential, (store.bot_id, 'okx', 'okx', 'demo'))
        if change == 'operation':
            selected.operation_id = 'other-operation'
        elif change == 'version':
            selected.version += 1
        elif change == 'command':
            session.get(AdminCommand, receipt['id']).effective_version += 1
        elif change == 'companion':
            session.get(ExchangeCredential, (store.bot_id, 'okx', 'okx_perpetual', 'demo')).state = 'revoking'
    state = store.store.read()
    if change == 'running':
        state['desired_state'] = 'running'
    with Session(store.engine) as session:
        assert len(bindings(session, store.bot_id)) < len(expected)
        actual = retiring_bindings(session, state)
    assert actual == (expected if change is None else None)
