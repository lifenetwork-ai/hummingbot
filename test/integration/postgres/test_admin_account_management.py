from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import select, update

from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.model.admin_access import AdminAccessStore, AdminAccount, AdminAuditEvent
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def accounts(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    store = AdminAccessStore(pg_engine, "hummingbot")
    bootstrap = Principal("basic:bootstrap", "basic", "admin")
    admin = store.add_account(bootstrap, email="boss@gmail.com", role="admin", request_id="invite-admin")
    actor = store.resolve_google(dict(email="boss@gmail.com", sub="boss-sub"), bind=True,
                                 bootstrap_enabled=True, request_id="bind-admin")
    return store, actor, admin


def test_admin_can_invite_and_disable_write_account(accounts):
    store, actor, _ = accounts
    user = store.add_account(actor, email="operator@gmail.com", role="write", request_id="invite-write")
    assert user["subject"] is None and user["version"] == 1
    user = store.change_account(actor, user["id"], expected_version=1, active=False, request_id="disable-write")
    assert not user["active"] and user["version"] == 2
    with pytest.raises(AccessDenied, match="account_disabled"):
        store.resolve_google(dict(email="operator@gmail.com", sub="operator-sub"), bind=True, bootstrap_enabled=False)


def test_last_bound_admin_is_protected_and_invitation_does_not_count(accounts):
    store, actor, _ = accounts
    store.add_account(actor, email="pending@gmail.com", role="admin", request_id="pending-admin")
    with pytest.raises(ControlConflict, match="last_admin_required"):
        store.change_account(actor, actor.id, expected_version=actor.account_version, role="read", request_id="remove-admin")
    assert store.bootstrap_status()["google_admin_ready"]


def test_role_changes_serialize_so_two_admins_cannot_remove_each_other(accounts):
    store, first, _ = accounts
    store.add_account(first, email="second@gmail.com", role="admin", request_id="invite-second")
    second = store.resolve_google(dict(email="second@gmail.com", sub="second-sub"), bind=True,
                                  bootstrap_enabled=False, request_id="bind-second")
    barrier = Barrier(2)

    def demote(actor):
        barrier.wait(3)
        try:
            store.change_account(actor, actor.id, expected_version=actor.account_version, role="read", request_id=actor.id)
            return "changed"
        except ControlConflict as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, (first, second)))
    assert sorted(results) == ["changed", "last_admin_required"]


def test_read_role_cannot_gain_admin_by_supplied_principal(accounts):
    store, actor, _ = accounts
    user = store.add_account(actor, email="reader@gmail.com", role="read", request_id="reader")
    forged = Principal(user["id"], "google", "admin", email=user["email"], account_version=1)
    with pytest.raises(AccessDenied, match="insufficient_role"):
        store.add_account(forged, email="other@gmail.com", role="admin", request_id="forged")


def test_same_subject_cannot_bind_a_second_invited_email(accounts):
    store, actor, _ = accounts
    store.add_account(actor, email="other@gmail.com", role="read", request_id="other-email")
    with pytest.raises(AccessDenied, match="identity_binding_mismatch"):
        store.resolve_google(dict(email="other@gmail.com", sub="boss-sub"), bind=True,
                             bootstrap_enabled=False, request_id="duplicate-binding")


def test_role_and_audit_rollback_together(accounts, monkeypatch):
    store, actor, _ = accounts
    user = store.add_account(actor, email="operator@gmail.com", role="write", request_id="operator")
    monkeypatch.setattr(store, "audit", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        store.change_account(actor, user["id"], expected_version=1, role="read", request_id="change")
    with store.engine.connect() as connection:
        assert connection.execute(select(AdminAccount.role).where(AdminAccount.id == user["id"])).scalar() == "write"


def test_audit_cursor_retains_microsecond_ties_and_actor_snapshot(accounts):
    store, actor, _ = accounts
    with store.engine.begin() as connection:
        connection.execute(update(AdminAuditEvent).values(created_at=datetime(2026, 11, 5, tzinfo=timezone.utc)))
    first = store.audit_page(limit=1)
    second = store.audit_page(limit=1, cursor=first["next_cursor"])
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["items"][0]["actor_email"] == "boss@gmail.com"
    with pytest.raises(ValueError, match="cursor"):
        store.audit_page(limit=1, cursor=first["next_cursor"], action="different_filter")


def test_google_only_bootstrap_requires_an_already_bound_admin(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    store = AdminAccessStore(pg_engine, "hummingbot")
    store.add_account(Principal("basic:bootstrap", "basic", "admin"), email="pending@gmail.com", role="admin", request_id="pending")
    with pytest.raises(AccessDenied, match="bootstrap_required"):
        store.resolve_google(dict(email="pending@gmail.com", sub="pending-sub"), bind=True, bootstrap_enabled=False)
