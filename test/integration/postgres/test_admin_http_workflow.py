from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_real_owner_process_serves_daily_and_observational_check_without_starting_strategy(pg_engine, tmp_path):
    import base64
    import json
    from test.integration.postgres.test_service import eventually, health, start_service, terminate
    from urllib.request import Request, urlopen
    process, port, log = start_service(tmp_path / "admin-revision", admin_enabled=True)

    def call(path, payload=None):
        headers = {"Authorization": "Basic " + base64.b64encode(b"operator:offline-admin").decode()}
        if payload is not None:
            headers.update({"Content-Type": "application/json", "Idempotency-Key": "read-only-check"})
        request = Request(f"http://127.0.0.1:{port}{ADMIN_PREFIX}{path}",
                          data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)
    try:
        eventually(lambda: health(port, "/startup")[0] == 200)
        store = BotStateStore(pg_engine, "hummingbot")
        eventually(lambda: bool((store.read() or {}).get("status")))
        status, daily = call("/statistics/daily?market=all")
        assert status == 200 and len(daily["markets"]) == 2 and daily["balances"] == []
        status, receipt = call("/reconciliation/check", {"market": "all"})
        assert status == 202 and receipt["status"] == "accepted"
        result = eventually(lambda: (row if (row := call("/reconciliation")[1])["report"]["state"] == "unavailable" else None))
        assert result["check"]["id"] == receipt["id"]
        assert len(result["report"]["markets"]) == 2
        row = store.read()
        assert row["control_version"] == 0 and row["desired_state"] == "stopped"
        assert not row["status"]["engine"]["strategy_running"]
    finally:
        terminate(process)
        log.close()


@pytest.mark.asyncio
async def test_real_http_control_receipts_retry_conflicts_failures_and_market_modes(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    settings = AdminSettings(True, "operator", "test-password")
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=state), settings, catalogue=catalogue).register(app)
    try:
        async with TestClient(TestServer(app), headers={"Authorization": BasicAuth("operator", "test-password").encode()}) as client:
            version = 0
            for mode in ("spot", "perpetual", "both"):
                payload = dict(action="start", expected_version=version, market_mode=mode, selection="fixed", profile_id="sideways", duration_seconds=60)
                key = "start-" + mode
                response = await client.post(ADMIN_PREFIX + "/commands", json=payload, headers={"Idempotency-Key": key})
                assert response.status == 202
                receipt = await response.json()
                retry = await client.post(ADMIN_PREFIX + "/commands", json=payload, headers={"Idempotency-Key": key})
                assert (await retry.json())["id"] == receipt["id"]
                assert (await client.post(ADMIN_PREFIX + "/commands", json={**payload, "duration_seconds": 120}, headers={"Idempotency-Key": key})).status == 409
                version += 1
                owner.store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=version,
                                    status={"running": True, "phase": "starting", "engine": {"strategy_running": True}})
                pending = await (await client.get(ADMIN_PREFIX + "/commands/" + receipt["id"])).json()
                assert pending["status"] == "applying"
                # A newer stop supersedes the still-applying start; it must not
                # claim success merely from its acknowledged control version.
                stop = dict(action="stop", expected_version=version, session_id=receipt["session_id"])
                response = await client.post(ADMIN_PREFIX + "/commands", json=stop, headers={"Idempotency-Key": "stop-" + mode})
                stopped = await response.json()
                version += 1
                owner.store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=version,
                                    status={"running": False, "phase": "stopped", "engine": {"strategy_running": False, "clock_running": False}})
                # Complete the original stop policy's persisted current session.
                state.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=version,
                                        session_id=receipt["session_id"], generation=1, change=lambda session, now: setattr(session, "status", "stopped"))
                assert (await (await client.get(ADMIN_PREFIX + "/commands/" + receipt["id"])).json())["status"] == "superseded"
                assert (await (await client.get(ADMIN_PREFIX + "/commands/" + stopped["id"])).json())["status"] == "succeeded"
            bad = dict(action="start", expected_version=version, market_mode="spot", profile_id="follow_market", duration_seconds=60,
                       references=[{"pair": "BTC-USDT", "weight": "0.6"}])
            assert (await client.post(ADMIN_PREFIX + "/commands", json=bad, headers={"Idempotency-Key": "invalid-basket"})).status == 422
            assert state.read()["control_version"] == version
            response = await client.post(ADMIN_PREFIX + "/commands", json=dict(action="start", expected_version=version,
                                                                               market_mode="both", profile_id="sideways", duration_seconds=60), headers={"Idempotency-Key": "before-switch"})
            first = await response.json()
            assert response.status == 202
            switch = dict(action="switch", expected_version=version, session_id=first["session_id"], market_mode="perpetual",
                          selection="random", profile_ids=["sideways"], step_duration_seconds=60, duration_seconds=120)
            assert (await client.post(ADMIN_PREFIX + "/commands", json=switch, headers={"Idempotency-Key": "stale-switch"})).status == 409
            switch["expected_version"] = version + 1
            response = await client.post(ADMIN_PREFIX + "/commands", json=switch, headers={"Idempotency-Key": "valid-switch"})
            switched = await response.json()
            assert response.status == 202 and switched["session_id"] != first["session_id"]
            owner.store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=version + 2,
                                status={"running": False, "phase": "halted", "error": "StrategyStartFailed", "engine": {"strategy_running": False}})
            assert (await (await client.get(ADMIN_PREFIX + "/commands/" + switched["id"])).json())["status"] == "failed"
            assert (await (await client.get(ADMIN_PREFIX + "/commands/" + first["id"])).json())["status"] == "superseded"
            with pg_engine.connect() as connection:
                assert len(connection.execute(select(AdminCommand)).all()) == 8
    finally:
        owner.release()
