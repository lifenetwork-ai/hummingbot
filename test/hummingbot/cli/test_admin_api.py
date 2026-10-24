from contextlib import asynccontextmanager
from datetime import datetime, timezone
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.mm_profiles import MMProfilesConfig

PAYLOAD = dict(action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60)


@asynccontextmanager
async def client():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    settings = AdminSettings(True, "operator", "test-password")
    storage = MagicMock()
    storage.command.return_value = dict(id="command-1", action="start", session_id="session-1", expected_version=0,
                                        effective_version=1, status="accepted", payload=PAYLOAD,
                                        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    storage.command_by_id.return_value = storage.command.return_value
    host = SimpleNamespace(settings=SimpleNamespace(bot_id="hummingbot"), phase="bootstrapping", store=MagicMock())
    host.store.read.return_value = dict(bot_id="hummingbot", desired_state="running", control_version=1,
                                        acknowledged_version=0, heartbeat_age_s=None, owner_uuid=None, status=None, mm_session=None)
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(host, settings, store=storage, catalogue=catalogue).register(app)
    async with TestClient(TestServer(app), headers={"Authorization": BasicAuth("operator", "test-password").encode()}) as http:
        yield http, storage


@pytest.mark.asyncio
async def test_http_command_is_accepted_and_exposes_pending_result():
    async with client() as (http, storage):
        response = await http.post(ADMIN_PREFIX + "/commands", headers={"Idempotency-Key": "key-1"}, json=PAYLOAD)
        assert response.status == 202
        data = await response.json()
        assert data["id"] == "command-1" and data["status"] == "accepted"
        assert storage.command.call_args.args == ("key-1", PAYLOAD)
        result = await http.get(ADMIN_PREFIX + "/commands/command-1")
        assert result.status == 200
        assert (await result.json())["status"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [{"market_mode": "margin"}, {"expected_version": True}, {"duration_seconds": -1},
                                     {"action": "pause"}, {"api_key": "not-an-accepted-field"}])
async def test_invalid_http_commands_do_not_reach_storage(updates):
    async with client() as (http, storage):
        response = await http.post(ADMIN_PREFIX + "/commands", headers={"Idempotency-Key": "key-1"}, json={**PAYLOAD, **updates})
        assert response.status == 422
        storage.command.assert_not_called()


@pytest.mark.asyncio
async def test_missing_key_bad_json_and_unknown_receipt():
    async with client() as (http, storage):
        assert (await http.post(ADMIN_PREFIX + "/commands", json=PAYLOAD)).status == 422
        assert (await http.post(ADMIN_PREFIX + "/commands", headers={"Idempotency-Key": "key-1", "Content-Type": "application/json"},
                                data="{broken")).status == 400
        storage.command_by_id.return_value = None
        assert (await http.get(ADMIN_PREFIX + "/commands/missing")).status == 404


@pytest.mark.asyncio
async def test_profiles_and_unknown_owner_status_are_structured():
    async with client() as (http, _):
        profiles = await (await http.get(ADMIN_PREFIX + "/profiles")).json()
        assert profiles["market_modes"] == ["spot", "perpetual", "both"]
        assert "follow_market" in profiles["catalogue"]["profiles"]
        status = await (await http.get(ADMIN_PREFIX + "/status")).json()
        assert not status["owner_available"] and not status["running"]
        assert status["control_version"] == 1
