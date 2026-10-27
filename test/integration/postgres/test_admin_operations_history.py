import asyncio
from datetime import datetime, timezone
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.integration.postgres.test_admin_history import history  # noqa: F401
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event, text, update

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSessionRequest
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.mm_event import MMEvent


def test_operator_history_is_discoverable_pageable_and_keeps_microsecond_ties(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    admin = AdminStore(state)
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    previous = None
    identities = []
    for version in range(5):
        payload = dict(action="switch" if previous else "start", expected_version=version, **request.model_dump(exclude_none=True))
        if previous:
            payload["session_id"] = previous
        receipt = admin.command(f"history-{version}", payload, catalogue=catalogue, request=request)
        identities.append(receipt["id"])
        previous = receipt["session_id"]
    with pg_engine.begin() as connection:
        connection.execute(update(AdminCommand).values(created_at=datetime(2026, 1, 1, microsecond=123456, tzinfo=timezone.utc)))
    page = admin.commands_page(limit=2)
    seen = [row["id"] for row in page["items"]]
    while page["next_cursor"]:
        page = admin.commands_page(limit=2, cursor=page["next_cursor"])
        seen.extend(row["id"] for row in page["items"])
    assert len(seen) == len(set(seen)) == 5 and set(seen) == set(identities)
    first = admin.commands_page(limit=1)
    with pytest.raises(ValueError):
        admin.commands_page(limit=1, cursor=first["next_cursor"], action="stop")
    sessions = admin.sessions_page(limit=2)
    assert sessions["current_session_id"] == previous
    assert sessions["items"][0]["id"] == previous
    assert sessions["items"][1]["status"] == "replaced"
    second = admin.sessions_page(limit=2, cursor=sessions["next_cursor"])
    assert not {row["id"] for row in second["items"]} & {row["id"] for row in sessions["items"]}
    # A current legacy session lacking a creation event is not invented as history.
    with pg_engine.begin() as connection:
        connection.execute(MMEvent.__table__.delete().where(MMEvent.session_id == previous))
    legacy = admin.sessions_page()
    assert legacy["current_session_id"] == previous
    assert previous not in {row["id"] for row in legacy["items"]}


def test_old_reconciliation_receipt_remains_addressable_after_a_new_check(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    admin = AdminStore(state)
    first = admin.request_check("first", {"market": "spot"})
    with pg_engine.begin() as connection:
        connection.execute(update(AdminCommand).values(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    second = admin.request_check("second", {"market": "perpetual"})
    assert admin.check_by_id(first["id"])["payload"]["market"] == "spot"
    assert admin.latest_check()["id"] == second["id"]
    assert admin.check_by_id("missing") is None


def test_recorded_order_progress_is_exact_and_constant_queries(history):  # noqa: F811
    from decimal import Decimal

    from hummingbot.model.order import Order
    with history.engine.begin() as connection:
        connection.execute(update(Order).where(Order.id == "order-00").values(last_status="OrderCancelled"))
        connection.execute(update(Order).where(Order.id == "order-01").values(creation_pending=True))
    counts = []

    def count(*args):
        counts.append(1)
    event.listen(history.engine, "before_cursor_execute", count)
    try:
        small = history.orders(limit=1)
        count_small = len(counts)
        counts.clear()
        large = history.orders(limit=500)
        assert len(counts) == count_small <= 3
    finally:
        event.remove(history.engine, "before_cursor_execute", count)
    assert small["items"][0]["recorded_filled_amount"] == Decimal("1.5")
    rows = {row["id"]: row for row in large["items"]}
    assert rows["order-00"]["recorded_remaining_amount"] == Decimal("98.5")
    assert rows["order-00"]["progress_basis"] == "recorded_fills_only"
    assert rows["order-01"]["recorded_remaining_amount"] is None
    assert rows["order-01"]["progress_basis"] == "provisional_order"


@pytest.mark.asyncio
async def test_history_http_contracts_do_not_expose_storage_receipt_fields(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    settings = AdminSettings(True, "operator", "test-password")
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=state), settings, catalogue=await MMProfilesConfig.load(CATALOGUE)).register(app)
    async with TestClient(TestServer(app)) as client:
        for path in ("/commands", "/sessions", "/reconciliation/missing"):
            assert (await client.get(ADMIN_PREFIX + path)).status == 401
        client.session.headers["Authorization"] = BasicAuth("operator", "test-password").encode()
        response = await client.post(ADMIN_PREFIX + "/commands", json=dict(
            action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60),
            headers={"Idempotency-Key": "http-history"})
        receipt = await response.json()
        assert response.status == 202
        commands = await (await client.get(ADMIN_PREFIX + "/commands?action=start")).json()
        assert commands["items"][0]["id"] == receipt["id"]
        assert not {"idempotency_key", "request_hash", "bot_id"} & commands["items"][0].keys()
        sessions = await (await client.get(ADMIN_PREFIX + "/sessions?limit=1")).json()
        item = sessions["items"][0]
        assert item["id"] == receipt["session_id"] and item["created_at"].endswith("Z")
        assert (await client.get(item["configuration_url"])).status == 200
        assert (await client.get(item["events_url"])).status == 200
        assert (await client.get(ADMIN_PREFIX + "/sessions?limit=501")).status == 422
        assert (await client.get(ADMIN_PREFIX + "/commands?action=invalid")).status == 422
        check = await (await client.post(ADMIN_PREFIX + "/reconciliation/check", json={"market": "all"},
                                         headers={"Idempotency-Key": "http-check"})).json()
        found = await client.get(ADMIN_PREFIX + "/reconciliation/" + check["id"])
        assert found.status == 200 and (await found.json())["id"] == check["id"]
        assert (await client.get(ADMIN_PREFIX + "/reconciliation/" + receipt["id"])).status == 404


def test_session_discovery_does_not_scan_unrelated_guard_history(pg_engine):
    from test.integration.postgres.test_admin_load_plans import nodes

    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    with pg_engine.begin() as connection:
        connection.execute(text("""INSERT INTO mm_events
            (bot_id,session_id,event_key,event_type,source,generation,control_version,payload,created_at)
            SELECT 'hummingbot','session-' || n%10,'history-plan-' || n,
                CASE WHEN n<=10 THEN 'session_created' ELSE 'guard_changed' END,
                'scheduler',1,1,'{}'::jsonb,clock_timestamp() FROM generate_series(1,20000) n"""))
        connection.execute(text("ANALYZE mm_events"))
        plan = connection.execute(text("""EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON)
            SELECT id,session_id,payload->'configuration'->'request' FROM mm_events
            WHERE bot_id='hummingbot' AND event_type='session_created' ORDER BY id DESC LIMIT 2""")).scalar_one()[0]["Plan"]
    assert "idx_mm_events_bot_id_event_type_id" in {node.get("Index Name") for node in nodes(plan)}, plan
    assert sum(node.get("Rows Removed by Filter", 0) for node in nodes(plan)) <= 2, plan
