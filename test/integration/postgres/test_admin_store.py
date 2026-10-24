import asyncio
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from unittest.mock import patch

import pytest

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_session import MMSessionRequest
from hummingbot.model.admin_store import AdminStore, IdempotencyConflict
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def admin(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    return AdminStore(store), asyncio.run(MMProfilesConfig.load(CATALOGUE))


def test_matching_retry_returns_original_receipt_before_stale_control_cas(admin):
    store, catalogue = admin
    payload = dict(action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60)
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    first = store.command("retry-1", payload, catalogue=catalogue, request=request)
    retry = store.command("retry-1", payload, catalogue=catalogue, request=request)
    assert retry["id"] == first["id"]
    assert retry["session_id"] == first["session_id"]
    assert store.store.read()["control_version"] == 1
    with pytest.raises(IdempotencyConflict):
        store.command("retry-1", {**payload, "duration_seconds": 600}, catalogue=catalogue,
                      request=request.model_copy(update={"duration_seconds": 600}))


def test_receipt_and_control_rollback_together(admin):
    store, catalogue = admin
    payload = dict(action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60)
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    with patch.object(store, "_insert_command", side_effect=RuntimeError("injected receipt persistence failure"), create=True) as insert:
        with pytest.raises(RuntimeError, match="injected"):
            store.command("atomic-1", payload, catalogue=catalogue, request=request)
        insert.assert_called_once()
    assert store.store.read()["control_version"] == 0
    assert store.store.read()["mm_session"] is None


def test_command_completion_requires_actual_state_and_is_preserved_after_later_commands(admin):
    store, catalogue = admin
    payload = dict(action="start", expected_version=0, market_mode="spot", profile_id="sideways", duration_seconds=60)
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    receipt = store.command("complete-1", payload, catalogue=catalogue, request=request)
    owner = BotOwnership(store.engine, "hummingbot")
    assert owner.try_acquire()
    try:
        identity = dict(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1)
        store.store.publish(**identity, status={"running": False, "phase": "starting", "engine": {"strategy_running": False}})
        assert store.command_by_id(receipt["id"])["status"] == "applying"
        active = store.store.update_mm_session(**identity, session_id=receipt["session_id"], generation=0,
                                               change=lambda session, now: session.ready(now=now, anchors={
                                                   "life_spot": {"life": "0.1", "sources": {}}}))
        store.store.publish(**identity, status={"running": True, "phase": "running", "engine": {"strategy_running": True},
                                                "mm_session": active.model_dump(mode="json", exclude={"catalogue"})})
        assert store.command_by_id(receipt["id"])["status"] == "succeeded"
        store.command("stop-1", dict(action="stop", expected_version=1, session_id=receipt["session_id"]), catalogue=catalogue)
        store.store.publish(**{**identity, "version": 2}, status={"running": False, "phase": "stopping"})
        assert store.command_by_id(receipt["id"])["status"] == "succeeded"
    finally:
        owner.release()
