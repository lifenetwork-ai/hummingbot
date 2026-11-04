from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update

from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.core.credential_encryption import CredentialCipher, CredentialContext, LocalKeyProvider
from hummingbot.model.admin_access import AdminAccessStore, AdminAccount, AdminAuditEvent
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.credential_store import CredentialStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.exchange_credential import ExchangeCredential


@pytest.fixture
def managed(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    access = AdminAccessStore(pg_engine, state.bot_id)
    basic = Principal("basic:bootstrap", "basic", "admin")
    access.add_account(basic, email="operator@gmail.com", role="write", request_id="invite")
    actor = access.resolve_google(dict(email="operator@gmail.com", sub="operator"), bind=True)
    return CredentialStore(state), actor, CredentialCipher(LocalKeyProvider(b"K" * 32, "local-v1"))


def save(store, actor, cipher, *, account_id="www:account", key="save-1", expected_version=0, expected_control_version=0, secret="canary-secret",
         connector="okx", environment="demo", exchange="okx"):
    scope = dict(exchange=exchange, connector=connector, environment=environment)
    envelope = cipher.encrypt({"api_key": "canary-api-key", "secret_key": secret, "passphrase": "canary-passphrase"},
                              CredentialContext(store.bot_id, **scope, version=expected_version + 1, account_id=account_id))
    return store.accept_set(actor, **scope, account_id=account_id, expected_version=expected_version,
                            expected_control_version=expected_control_version, idempotency_key=key,
                            envelope=envelope, request_id="set-request")


def test_set_commits_only_envelope_safe_receipt_audit_and_no_auto_start(managed):
    store, actor, cipher = managed
    receipt = save(store, actor, cipher)
    assert receipt["action"] == "credentials_set" and receipt["session_id"] is None
    assert receipt["status"] == "accepted" and receipt["effective_version"] == 1
    state = store.store.read()
    assert state["desired_state"] == "stopped" and state["control_version"] == 1
    assert state["recovery_state"]["credential_operation"]["id"] == receipt["id"]
    assert store.store.read(publication=True)["recovery_summary"]["credential_operation_pending"] is True
    for table in ("admin_commands", "admin_audit_events", "exchange_credentials", "bot_state"):
        with store.engine.connect() as connection:
            export = connection.execute(text(f"SELECT row_to_json({table})::text FROM {table}")).scalars().all()
        assert "canary" not in str(export)
    assert store.operation(receipt["id"])["id"] == receipt["id"]
    assert store.operation("unknown") is None


def test_retry_returns_first_committed_receipt_without_changing_ciphertext(managed):
    store, actor, cipher = managed
    first = save(store, actor, cipher)
    second = save(store, actor, cipher, secret="different-canary")
    assert first == second
    row = store.load("okx", "okx", "demo")
    assert cipher.decrypt(row["envelope"], CredentialContext(store.bot_id, "okx", "okx", "demo", 1, "www:account"))["secret_key"] == "canary-secret"
    with pytest.raises(ControlConflict) as error:
        save(store, actor, cipher, expected_version=1)
    assert error.value.code == "idempotency_key_reused"
    with pytest.raises(ControlConflict) as error:
        save(store, Principal("basic:different-actor", "basic", "admin"), cipher)
    assert error.value.code == "idempotency_key_reused"
    with store.engine.connect() as connection:
        assert len(connection.execute(select(AdminCommand)).all()) == 1
        assert len(connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.action == "credentials_set")).all()) == 1


@pytest.mark.parametrize("change", [dict(desired_state="running"), dict(control_version=1, acknowledged_version=0),
                                    dict(recovery_state={"pending_mutations": [{"token": "pending"}]}),
                                    dict(recovery_state={"runtime_active": True})])
def test_replacement_requires_acknowledged_stop_and_no_external_effects(managed, change):
    store, actor, cipher = managed
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(**change))
    with pytest.raises(ControlConflict):
        save(store, actor, cipher)
    assert store.load("okx", "okx", "demo") is None


def test_slow_encryption_cannot_preserve_a_removed_role_and_audit_failure_rolls_back(managed):
    store, actor, cipher = managed
    with patch.object(store.access, "audit", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            save(store, actor, cipher)
    assert store.load("okx", "okx", "demo") is None and store.store.read()["control_version"] == 0
    with store.engine.begin() as connection:
        connection.execute(update(AdminAccount).where(AdminAccount.id == actor.id).values(role="read", version=2))
    with pytest.raises(AccessDenied, match="insufficient_role"):
        save(store, actor, cipher)


def test_account_binding_survives_revoke_and_blocks_companion_wrong_account(managed):
    store, actor, cipher = managed
    save(store, actor, cipher)
    with store.engine.begin() as connection:
        connection.execute(update(ExchangeCredential).values(state="revoked", envelope=None))
        connection.execute(update(BotState).values(acknowledged_version=1, recovery_state={}))
    with pytest.raises(ControlConflict) as error:
        save(store, actor, cipher, account_id="www:different", key="new-account", expected_version=1, expected_control_version=1)
    assert error.value.code == "exchange_account_mismatch"


def test_credential_versions_are_checked_before_committing_a_candidate(managed):
    store, actor, cipher = managed
    with pytest.raises(ControlConflict) as error:
        save(store, actor, cipher, expected_version=3)
    assert error.value.code == "credential_version_conflict"


def test_rotation_keeps_ledger_and_rejects_changed_environment_account_or_bot(managed):
    from hummingbot.model.position import Position
    store, actor, cipher = managed
    receipt = save(store, actor, cipher)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid="owner", owner_generation=1))
    assert store.finish_set(receipt["id"], "owner", 1, versions={"okx": 1})
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(acknowledged_version=1))
        connection.execute(Position.__table__.insert().values(id="held", controller_id="controller", connector_name="okx_perpetual",
                                                              side="BUY", trading_pair="BTC-USDT", timestamp=1, volume_traded_quote=100, amount="0.001", breakeven_price=100000,
                                                              unrealized_pnl_quote=0, realized_pnl_quote=0, cum_fees_quote=0))
    for changes in (dict(account_id="www:different"), dict(connector="okx_perpetual", account_id="www:different", expected_version=0),
                    dict(environment="live", expected_version=0), dict(exchange="other", connector="other_spot", expected_version=0)):
        with pytest.raises(ControlConflict) as error:
            save(store, actor, cipher, **{**dict(key="rejected", expected_version=1, expected_control_version=1), **changes})
        assert error.value.code == "exchange_account_mismatch"
    rotated = save(store, actor, cipher, key="rotate", expected_version=1, expected_control_version=1, secret="rotated-canary")
    assert rotated["result"]["credential_version"] == 2
    with store.engine.connect() as connection:
        assert connection.execute(select(Position.amount)).scalar_one() == Decimal("0.001")
    with pytest.raises(ControlConflict):
        BotStateStore(store.engine, "other-bot").ensure()


def test_only_current_owner_can_complete_loading_and_normal_commands_cannot_supersede_it(managed):
    store, actor, cipher = managed
    receipt = save(store, actor, cipher)
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid="owner", owner_generation=4))
    with pytest.raises(ControlConflict):
        store.store.control(expected_version=1, desired_state="stopped")
    assert not store.claim_set(receipt["id"], "old-owner", 3)
    assert store.claim_set(receipt["id"], "owner", 4)
    assert store.operation(receipt["id"])["status"] == "applying"
    assert store.operation(receipt["id"])["result"]["stage"] == "loading"
    assert not store.finish_set(receipt["id"], "old-owner", 3, versions={"okx": 1})
    assert store.finish_set(receipt["id"], "owner", 4, versions={"okx": 1})
    result = store.operation(receipt["id"])
    assert result["status"] == "succeeded" and result["result"]["stage"] == "loaded"
    assert "credential_operation" not in store.store.read()["recovery_state"]
    with store.engine.connect() as connection:
        assert len(connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.operation_id == receipt["id"])).all()) == 2
