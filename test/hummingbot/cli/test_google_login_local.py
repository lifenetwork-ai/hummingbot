"""Local acceptance helper must not expose trading or deployment secrets."""
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.client.config.admin_config import AdminSettings
from scripts.google_login_local import create_app


@pytest.mark.asyncio
async def test_local_page_serves_only_public_config_and_blocks_trading():
    settings = AdminSettings(enabled=True, basic_auth_enabled=True,
                             username="bootstrap", password="do-not-expose",
                             google_auth_enabled=True, google_client_id="public-client-id")
    async with TestClient(TestServer(create_app(settings, SimpleNamespace()))) as client:
        headers = {"Host": "localhost:3000"}
        config = await client.get("/config", headers=headers)
        assert await config.json() == {"client_id": "public-client-id"}
        assert config.headers["Cache-Control"] == "no-store"
        page = await client.get("/", headers=headers)
        assert page.status == 200
        assert "do-not-expose" not in await page.text()
        for method, path in [("POST", "/api/v1/admin/commands"), ("GET", "/api/v1/admin/credentials"),
                             ("GET", "/.env"), ("GET", "/api/v1/admin/status")]:
            response = await client.request(method, path, headers=headers)
            assert response.status == 404
        denied = await client.get("/config", headers={"Host": "attacker.example:3000"})
        assert denied.status == 403


@pytest.mark.asyncio
async def test_access_endpoints_use_real_authentication_middleware():
    settings = AdminSettings(enabled=True, basic_auth_enabled=True, username="bootstrap", password="password")
    async with TestClient(TestServer(create_app(settings, SimpleNamespace()))) as client:
        response = await client.post("/api/v1/admin/access/login", headers={"Host": "localhost:3000"})
        assert response.status == 401
        assert (await response.json())["code"] == "authentication_required"
