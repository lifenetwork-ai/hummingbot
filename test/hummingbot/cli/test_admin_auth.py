from contextlib import asynccontextmanager

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings


@asynccontextmanager
async def client(settings):
    app = web.Application(middlewares=[admin_middleware(settings)])

    async def read(request):
        return web.json_response({"ok": True})

    for path in ("/status", "/profiles", "/openapi.json", "/docs", "/orders", "/balances"):
        app.router.add_get(ADMIN_PREFIX + path, read)
    app.router.add_get("/live", read)
    app.router.add_post(ADMIN_PREFIX + "/commands", read)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/status", "/profiles", "/openapi.json", "/docs", "/orders", "/balances"])
async def test_every_admin_read_including_schema_and_ui_requires_basic_auth(path):
    async with client(AdminSettings(True, "operator", "test-password")) as http:
        response = await http.get(ADMIN_PREFIX + path)
        assert response.status == 401
        assert response.headers["WWW-Authenticate"].startswith("Basic ")
        assert "test-password" not in await response.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["Bearer token", "Basic !!!", BasicAuth("wrong", "test-password").encode(),
                                           BasicAuth("operator", "wrong").encode()])
async def test_bad_credentials_and_malformed_headers_fail(authorization):
    async with client(AdminSettings(True, "operator", "test-password")) as http:
        response = await http.post(ADMIN_PREFIX + "/commands", headers={"Authorization": authorization}, json={})
        assert response.status == 401


@pytest.mark.asyncio
async def test_valid_unicode_password_and_public_health():
    async with client(AdminSettings(True, "operator", "test-mật-khẩu")) as http:
        assert (await http.get("/live")).status == 200
        header = BasicAuth("operator", "test-mật-khẩu", encoding="utf-8").encode()
        assert (await http.get(ADMIN_PREFIX + "/status", headers={"Authorization": header})).status == 200


@pytest.mark.asyncio
async def test_disabled_admin_api_does_not_expose_registered_routes():
    async with client(AdminSettings()) as http:
        assert (await http.get(ADMIN_PREFIX + "/status")).status == 404
        assert (await http.get("/live")).status == 200


@pytest.mark.asyncio
async def test_allowed_preflight_does_not_bypass_auth_for_actual_request():
    async with client(AdminSettings(True, "operator", "test-password", ("https://ui.example",))) as http:
        headers = {"Origin": "https://ui.example", "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "authorization, content-type, idempotency-key"}
        response = await http.options(ADMIN_PREFIX + "/commands", headers=headers)
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == "https://ui.example"
        assert (await http.post(ADMIN_PREFIX + "/commands", headers={"Origin": "https://ui.example"})).status == 401
        rejected = await http.options(ADMIN_PREFIX + "/commands", headers={**headers, "Origin": "https://untrusted.example"})
        assert rejected.status == 403


@pytest.mark.asyncio
async def test_wildcard_origin_allows_explicit_basic_header_without_browser_credentials():
    async with client(AdminSettings(True, "operator", "test-password", ("*",))) as http:
        origin = "https://any-ui.example"
        response = await http.options(ADMIN_PREFIX + "/commands", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert "Access-Control-Allow-Credentials" not in response.headers
        response = await http.get(ADMIN_PREFIX + "/status", headers={
            "Origin": origin, "Authorization": BasicAuth("operator", "test-password").encode()})
        assert response.status == 200
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert (await http.get(ADMIN_PREFIX + "/status", headers={"Origin": origin})).status == 401
