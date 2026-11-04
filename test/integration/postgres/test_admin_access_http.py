from contextlib import asynccontextmanager
from test.hummingbot.cli.test_admin_identity import signed_identity as signature_fixture
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.cli.admin_identity import GoogleTokenVerifier
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres

signed_identity = signature_fixture


@asynccontextmanager
async def http_client(pg_engine, certificates, *, basic=True, migrate=True):
    if migrate:
        bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    settings = AdminSettings(enabled=True, username="bootstrap", password="offline-password", basic_auth_enabled=basic,
                             google_auth_enabled=True, google_client_id="offline-client")
    app = web.Application(middlewares=[admin_middleware(settings)])
    api = AdminAPI(SimpleNamespace(store=state), settings)
    api.register(app)
    api._identity_service.verifier.close()
    api._identity_service.verifier = GoogleTokenVerifier("offline-client", certificates=certificates)
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.mark.asyncio
async def test_bootstrap_google_onboarding_and_disabled_basic_restart(pg_engine, signed_identity):
    token, certs = signed_identity
    basic = {"Authorization": BasicAuth("bootstrap", "offline-password").encode()}
    google = {"Authorization": "Bearer " + token()}
    async with http_client(pg_engine, certs) as client:
        response = await client.get(ADMIN_PREFIX + "/access/bootstrap", headers=basic)
        assert response.status == 200 and not (await response.json())["google_admin_ready"]
        response = await client.post(ADMIN_PREFIX + "/access/accounts", headers=basic,
                                     json={"email": "boss@gmail.com", "role": "admin"})
        assert response.status == 201
        account = await response.json()
        assert account["bound"] is False
        response = await client.post(ADMIN_PREFIX + "/access/login", headers=google)
        assert response.status == 200
        assert (await response.json())["role"] == "admin"
        ready = await (await client.get(ADMIN_PREFIX + "/access/bootstrap", headers=google)).json()
        assert ready["google_admin_ready"] is True
    async with http_client(pg_engine, certs, basic=False, migrate=False) as client:
        response = await client.get(ADMIN_PREFIX + "/access/me", headers=basic)
        assert response.status == 401 and (await response.json())["code"] == "basic_auth_disabled"
        response = await client.get(ADMIN_PREFIX + "/access/me", headers=google)
        principal = await response.json()
        assert response.status == 200 and principal["id"] == account["id"]
        response = await client.post(ADMIN_PREFIX + "/access/accounts/" + account["id"], headers=google,
                                     json={"expected_version": 2, "role": "read"})
        assert response.status == 409 and (await response.json())["code"] == "last_admin_required"
        response = await client.get(ADMIN_PREFIX + "/audit", headers=google)
        assert response.status == 200
        events = (await response.json())["items"]
        assert {"account_added", "account_bound", "login"} <= {row["action"] for row in events}
        assert all("offline-password" not in str(row) and token() not in str(row) for row in events)


@pytest.mark.asyncio
async def test_role_revocation_and_denied_mutation_have_current_actor_and_safe_audit(pg_engine, signed_identity):
    token, certs = signed_identity
    basic = {"Authorization": BasicAuth("bootstrap", "offline-password").encode()}
    google = {"Authorization": "Bearer " + token()}
    reader = {"Authorization": "Bearer " + token(email="reader@gmail.com", sub="reader-sub")}
    async with http_client(pg_engine, certs) as client:
        invalid = await client.post(ADMIN_PREFIX + "/access/accounts", headers=basic, json={"email": "invalid-email", "role": "read"})
        assert invalid.status == 422 and (await invalid.json())["fields"][0]["path"] == "email"
        await client.post(ADMIN_PREFIX + "/access/accounts", headers=basic, json={"email": "boss@gmail.com", "role": "admin"})
        assert (await client.post(ADMIN_PREFIX + "/access/login", headers=google)).status == 200
        response = await client.post(ADMIN_PREFIX + "/access/accounts", headers=google,
                                     json={"email": "reader@gmail.com", "role": "read"})
        user = await response.json()
        assert (await client.post(ADMIN_PREFIX + "/access/login", headers=reader)).status == 200
        denied = await client.post(ADMIN_PREFIX + "/access/accounts", headers=reader,
                                   json={"email": "forged@gmail.com", "role": "admin", "password": "canary-private-field"})
        assert denied.status == 403 and "canary-private-field" not in await denied.text()
        audit = await (await client.get(ADMIN_PREFIX + "/audit", headers=google,
                                        params={"actor_id": user["id"], "outcome": "denied"})).json()
        assert len(audit["items"]) == 1
        assert audit["items"][0]["reason_code"] == "insufficient_role"
        assert audit["items"][0]["actor_email"] == "reader@gmail.com"
        changed = await client.post(ADMIN_PREFIX + "/access/accounts/" + user["id"], headers=google,
                                    json={"expected_version": 2, "active": False})
        assert changed.status == 200
        denied = await client.get(ADMIN_PREFIX + "/access/me", headers=reader)
        assert denied.status == 403 and (await denied.json())["code"] == "account_disabled"


@pytest.mark.asyncio
async def test_invalid_sign_in_is_audited_without_unverified_identity(pg_engine, signed_identity):
    _, certs = signed_identity
    basic = {"Authorization": BasicAuth("bootstrap", "offline-password").encode()}
    async with http_client(pg_engine, certs) as client:
        denied = await client.post(ADMIN_PREFIX + "/access/login", headers={"Authorization": "Bearer private-invalid-token"})
        assert denied.status == 401
        audit = await (await client.get(ADMIN_PREFIX + "/audit", headers=basic,
                                        params={"action": "login", "outcome": "denied"})).json()
        assert len(audit["items"]) == 1
        assert audit["items"][0]["actor_id"] is None and audit["items"][0]["actor_email"] is None
        assert "private-invalid-token" not in str(audit)
