from datetime import timezone

import pytest
from sqlalchemy import select, text

from hummingbot.core.admin_identity import AccessDenied
from hummingbot.model.admin_access import AdminAccessStore, AdminAccount, AdminAuditEvent
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def access(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    return AdminAccessStore(pg_engine, "hummingbot")


def seed_account(access, *, subject="boss-sub", role="write", active=True):
    with access.engine.begin() as connection:
        now = connection.execute(text("SELECT clock_timestamp()")).scalar()
        connection.execute(AdminAccount.__table__.insert().values(
            id="account-1", bot_id=access.bot_id, email="boss@gmail.com", issuer="https://accounts.google.com" if subject else None,
            subject=subject, role=role, active=active, version=1, created_at=now, updated_at=now))


def claims(**updates):
    return dict(iss="https://accounts.google.com", sub="boss-sub", email="boss@gmail.com", **updates)


def test_replayable_migration_preserves_accounts_and_checks_each_request(access):
    seed_account(access)
    bootstrap_postgres(access.engine, auto_migrate=True)
    assert access.resolve_google(claims()).role == "write"
    with access.engine.begin() as connection:
        connection.execute(AdminAccount.__table__.update().values(role="read", version=2))
    assert access.resolve_google(claims()).role == "read"
    with access.engine.begin() as connection:
        connection.execute(AdminAccount.__table__.update().values(active=False, version=3))
    with pytest.raises(AccessDenied, match="account_disabled"):
        access.resolve_google(claims())


def test_unknown_subject_cannot_claim_already_bound_email(access):
    seed_account(access)
    with pytest.raises(AccessDenied, match="identity_binding_mismatch"):
        access.resolve_google({**claims(), "sub": "other-subject"})


def test_pending_invitation_is_bound_only_during_verified_sign_in(access):
    seed_account(access, subject=None)
    with access.engine.begin() as connection:
        connection.execute(AdminAccount.__table__.update().values(issuer=None))
    with pytest.raises(AccessDenied, match="account_onboarding_required"):
        access.resolve_google(claims())
    principal = access.resolve_google(claims(), bind=True, request_id="login-1")
    assert principal.id == "account-1" and principal.account_version == 2
    with access.engine.connect() as connection:
        events = connection.execute(select(AdminAuditEvent)).mappings().all()
    assert len(events) == 1 and events[0]["action"] == "account_bound"
    assert events[0]["created_at"].utcoffset() == timezone.utc.utcoffset(None)


def test_audit_failure_rolls_back_first_subject_binding(access, monkeypatch):
    seed_account(access, subject=None)
    with access.engine.begin() as connection:
        connection.execute(AdminAccount.__table__.update().values(issuer=None))
    monkeypatch.setattr(access, "audit", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected audit failure")))
    with pytest.raises(RuntimeError, match="injected"):
        access.resolve_google(claims(), bind=True, request_id="login-1")
    with access.engine.connect() as connection:
        assert connection.execute(select(AdminAccount.subject)).scalar() is None


def test_mutation_rechecks_role_after_http_authorization(access):
    from hummingbot.model.admin_store import AdminStore
    seed_account(access)
    principal = access.resolve_google(claims())
    with access.engine.begin() as connection:
        connection.execute(AdminAccount.__table__.update().values(role="read", version=2))
    state = BotStateStore(access.engine, access.bot_id)
    with pytest.raises(AccessDenied, match="insufficient_role"):
        AdminStore(state).command("stale-role", {"action": "start"}, catalogue=None, principal=principal)
    assert state.read()["control_version"] == 0


def test_trading_receipt_has_verified_actor_and_atomic_audit(access):
    import asyncio
    from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

    from hummingbot.client.config.mm_profiles import MMProfilesConfig
    from hummingbot.core.mm_session import MMSessionRequest
    from hummingbot.model.admin_store import AdminStore
    from hummingbot.model.mm_event import MMEvent
    seed_account(access)
    principal = access.resolve_google(claims())
    state = BotStateStore(access.engine, access.bot_id)
    store = AdminStore(state)
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    payload = dict(action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60)
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    receipt = store.command("identity-control", payload, catalogue=catalogue, request=request,
                            principal=principal, audit_request_id="http-request-1")
    retry = store.command("identity-control", payload, catalogue=catalogue, request=request,
                          principal=principal, audit_request_id="http-request-2")
    assert retry["id"] == receipt["id"]
    with access.engine.connect() as connection:
        assert connection.execute(select(MMEvent.principal).limit(1)).scalar() == principal.id
        audit = connection.execute(select(AdminAuditEvent)).mappings().all()
    assert len(audit) == 1 and audit[0]["actor_id"] == principal.id
    assert audit[0]["operation_id"] == receipt["id"] and audit[0]["request_id"] == "http-request-1"
