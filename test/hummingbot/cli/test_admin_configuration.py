from contextlib import asynccontextmanager
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.mm_profiles import MMProfilesConfig


@asynccontextmanager
async def api(catalogue, row):
    settings = AdminSettings(True, "operator", "test-password")
    store = MagicMock()
    store.read.return_value = row
    host = SimpleNamespace(store=store, revision="new-revision")
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(host, settings, catalogue=catalogue).register(app)
    async with TestClient(TestServer(app), headers={"Authorization": BasicAuth("operator", "test-password").encode()}) as http:
        yield http


def state(**changes):
    return dict(bot_id="hummingbot", desired_state="stopped", control_version=0, acknowledged_version=0,
                owner_uuid="owner", heartbeat_age_s=1, database_now=100, revision="old-revision", mm_session=None,
                status={"phase": "stopped", "identity": {"trading_environment": "demo", "revision": "old-revision"}},
                recovery_summary={"halted": False, "pending_mutations": False}, **changes)


@pytest.mark.asyncio
async def test_profiles_advertise_yaml_ownership_and_only_configured_market_modes():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    catalogue.markets.pop("perpetual")
    async with api(catalogue, state()) as http:
        response = await (await http.get(ADMIN_PREFIX + "/profiles")).json()
        assert response["market_modes"] == ["spot"]
        assert response["configuration_policy"]["profile_settings"] == "yaml_only"
        assert response["configuration_policy"]["restart_behavior"] == "restore_persisted_session"
        assert "references" not in response["configuration_policy"]["session_controls"]
        assert len(response["catalogue_hash"]) == 64


@pytest.mark.asyncio
async def test_status_distinguishes_owner_from_responding_revision_and_keeps_stop_available(monkeypatch):
    monkeypatch.setenv("OKX_TRADING_ENV", "live")
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    async with api(catalogue, state()) as http:
        response = await (await http.get(ADMIN_PREFIX + "/status")).json()
        assert response["process_identity"]["trading_environment"] == "live"
        assert response["owner_identity"]["trading_environment"] == "demo"
        assert response["owner_identity"]["revision"] == "old-revision"
        assert response["operator"]["state"] == "needs_attention"
        assert not response["actions"]["start"]["allowed"]
        assert response["actions"]["start"]["reason_code"] == "environment_mismatch"


@pytest.mark.asyncio
async def test_stale_owner_invalidates_nested_readiness_and_halt_is_actionable():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    row = state()
    row.update(heartbeat_age_s=20, recovery_summary={"halted": True, "pending_mutations": False})
    market = dict(market="spot", connector="okx", pair="LIFE-USDT", ready=True, admitted=True, reference_price=".1", reason=None)
    row["status"]["mm_markets"] = [market]
    async with api(catalogue, row) as http:
        response = await (await http.get(ADMIN_PREFIX + "/status")).json()
        assert not response["markets"][0]["ready"] and not response["markets"][0]["admitted"]
        assert response["observation"]["stale"]
        assert response["recovery"]["halted"]
        assert not response["actions"]["start"]["allowed"]
        assert response["recovery"]["next_action"] == "contact_support"


@pytest.mark.asyncio
async def test_switching_marks_observations_old_and_stop_is_available():
    from random import Random

    from hummingbot.core.mm_session import MMSession, MMSessionRequest
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    row = state()
    requested = MMSession.create(catalogue, MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60), rng=Random(1))
    row.update(desired_state="running", mm_session=requested.model_dump(mode="json", exclude={"catalogue"}))
    row["status"].update(phase="starting", identity=None)
    async with api(catalogue, row) as http:
        response = await (await http.get(ADMIN_PREFIX + "/status")).json()
        assert response["operator"]["state"] == "switching"
        assert response["observation"]["stale"] and not response["observation"]["session_matches"]
        assert response["actions"]["stop"]["allowed"]
        assert requested.id in response["session_configuration_url"]
