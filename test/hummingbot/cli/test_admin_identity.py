import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import jwt
import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from hummingbot.cli.admin_api import ADMIN_ENDPOINTS, ADMIN_PREFIX, admin_middleware
from hummingbot.cli.admin_identity import GoogleTokenVerifier
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.core.admin_identity import AccessDenied, Principal


@pytest.fixture
def signed_identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Offline Google verifier fixture")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    claims = dict(iss="https://accounts.google.com", sub="verified-subject", aud="offline-client",
                  email="boss@gmail.com", email_verified=True, iat=int(time.time()), exp=int(time.time()) + 600)
    certs = {"offline-key": certificate.public_bytes(serialization.Encoding.PEM).decode()}

    def token(**updates):
        return jwt.encode({**claims, **updates}, key, algorithm="RS256", headers={"kid": "offline-key"})

    return token, certs


def test_official_verifier_accepts_signed_google_identity(signed_identity):
    token, certs = signed_identity
    verifier = GoogleTokenVerifier("offline-client", certificates=certs)
    claims = verifier.verify(token())
    assert claims["sub"] == "verified-subject" and claims["email"] == "boss@gmail.com"


@pytest.mark.parametrize("updates", [dict(aud="another-client"), dict(iss="https://attacker.example"),
                                     dict(exp=int(time.time()) - 600), dict(email_verified=False), dict(sub=""),
                                     dict(email="boss@external.example"), dict(email_verified="true")])
def test_signed_but_invalid_identity_is_rejected(signed_identity, updates):
    token, certs = signed_identity
    with pytest.raises(AccessDenied):
        GoogleTokenVerifier("offline-client", certificates=certs).verify(token(**updates))


def test_workspace_identity_is_supported_but_token_tampering_is_rejected(signed_identity):
    token, certs = signed_identity
    verifier = GoogleTokenVerifier("offline-client", certificates=certs)
    assert verifier.verify(token(email="boss@company.example", hd="company.example"))["sub"] == "verified-subject"
    body = jwt.encode(dict(sub="attacker"), "attacker-secret-value-at-least-32-bytes", algorithm="HS256", headers={"kid": "offline-key"})
    with pytest.raises(AccessDenied):
        verifier.verify(body)


@asynccontextmanager
async def identity_client(principal=None, *, settings=None, resolver=None):
    settings = settings or AdminSettings(enabled=True, google_auth_enabled=True, google_client_id="offline-client")
    identity = SimpleIdentity(principal, resolver)
    app = web.Application(middlewares=[admin_middleware(settings, identity=identity)])

    async def read(request):
        actor = request["admin_principal"]
        return web.json_response(dict(id=actor.id, role=actor.role))

    for method, path, _ in ADMIN_ENDPOINTS:
        app.router.add_route(method, ADMIN_PREFIX + path, read)
    async with TestClient(TestServer(app)) as client:
        yield client, identity


class SimpleIdentity:
    def __init__(self, principal, resolver):
        self.authenticate = resolver or AsyncMock(return_value=principal)


@pytest.mark.asyncio
async def test_google_reads_use_verified_role_and_writes_are_denied_for_readers():
    actor = Principal("user-1", "google", "read", email="boss@gmail.com", account_version=1)
    async with identity_client(actor) as (client, identity):
        headers = {"Authorization": "Bearer offline-token"}
        read = await client.get(ADMIN_PREFIX + "/status", headers=headers)
        assert read.status == 200 and (await read.json())["id"] == "user-1"
        denied = await client.post(ADMIN_PREFIX + "/commands", headers=headers, json={})
        assert denied.status == 403
        body = await denied.json()
        assert body["code"] == "insufficient_role" and body["request_id"]
        assert identity.authenticate.await_count == 2


@pytest.mark.asyncio
async def test_populated_basic_password_is_rejected_when_basic_disabled():
    settings = AdminSettings(enabled=True, username="old-admin", password="old-secret", google_auth_enabled=True,
                             google_client_id="offline-client", basic_auth_enabled=False)
    async with identity_client(settings=settings) as (client, identity):
        response = await client.get(ADMIN_PREFIX + "/status", headers={
            "Authorization": BasicAuth("old-admin", "old-secret").encode()})
        assert response.status == 401 and (await response.json())["code"] == "basic_auth_disabled"
        identity.authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_removal_applies_to_existing_token_next_request():
    actor = Principal("user-1", "google", "write", account_version=1)
    resolver = AsyncMock(side_effect=[actor, AccessDenied("account_disabled", status=403)])
    async with identity_client(actor, resolver=resolver) as (client, _):
        headers = {"Authorization": "Bearer same-token"}
        assert (await client.post(ADMIN_PREFIX + "/commands", headers=headers, json={})).status == 200
        response = await client.get(ADMIN_PREFIX + "/status", headers=headers)
        assert response.status == 403 and (await response.json())["code"] == "account_disabled"


@pytest.mark.asyncio
async def test_unknown_admin_mutation_cannot_inherit_permission_from_http_method():
    actor = Principal("user-1", "google", "write", account_version=1)
    async with identity_client(actor) as (client, _):
        assert (await client.post(ADMIN_PREFIX + "/not-a-reviewed-operation", headers={"Authorization": "Bearer token"},
                                  json={})).status in (403, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,name", ADMIN_ENDPOINTS)
async def test_reader_permission_is_explicit_for_every_registered_admin_handler(method, path, name):
    actor = Principal("reader-1", "google", "read")
    path = path.format(command_id="command-1", order_id="order-1", session_id="session-1", check_id="check-1", asset="swagger.js", account_id="account-1")
    async with identity_client(actor) as (client, _):
        response = await client.request(method, ADMIN_PREFIX + path, headers={"Authorization": "Bearer reader-token"})
        restricted = {"commands", "reconciliation_check", "access_bootstrap", "access_accounts", "access_add_account", "access_change_account"}
        assert response.status == (403 if name in restricted else 200)


@pytest.mark.asyncio
async def test_database_failure_does_not_fall_back_to_populated_basic_password():
    from sqlalchemy.exc import OperationalError
    settings = AdminSettings(enabled=True, username="old-admin", password="old-secret", google_auth_enabled=True,
                             google_client_id="offline-client")
    resolver = AsyncMock(side_effect=OperationalError("private SQL", {}, RuntimeError("secret DSN")))
    async with identity_client(settings=settings, resolver=resolver) as (client, _):
        response = await client.get(ADMIN_PREFIX + "/status", headers={"Authorization": "Bearer verified-token"})
        body = await response.json()
        assert response.status == 503 and body["code"] == "database_unavailable"
        assert all(value not in str(body) for value in ("private SQL", "secret DSN", "old-secret", "verified-token"))
