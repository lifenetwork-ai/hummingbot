import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def sessions(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    return store, asyncio.run(MMProfilesConfig.load(CATALOGUE))


def test_request_persists_effective_basket_and_uses_control_cas(sessions):
    store, catalogue = sessions
    request = MMSessionRequest(market_mode="both", profile_id="follow_market", duration_seconds=3600,
                               references=[{"pair": "AVAX-USDT", "weight": "1"}])
    result = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                      request=request, rng=random.Random(1))
    row = store.read()
    assert row["control_version"] == 1
    session = MMSession.model_validate(row["mm_session"])
    assert session.id == result["session_id"]
    assert session.request.references[0].pair == "AVAX-USDT"
    assert session.started_at is None
    with pytest.raises(ControlConflict):
        store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                 request=request, rng=random.Random(1))
    assert store.read()["mm_session"] == row["mm_session"]


def test_migration_replay_preserves_pending_draw(sessions):
    store, catalogue = sessions
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                             request=MMSessionRequest(market_mode="spot", selection="random", duration_seconds=3600),
                             rng=random.Random(4))
    saved = store.read()["mm_session"]
    bootstrap_postgres(store.engine, auto_migrate=True)
    assert store.read()["mm_session"] == saved


def test_stop_revokes_session_without_waiting_for_owner(sessions):
    store, catalogue = sessions
    result = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                      request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    store.request_mm_session(action="stop", expected_version=1, current_session_id=result["session_id"])
    row = store.read()
    assert row["desired_state"] == "stopped"
    assert row["mm_session"]["status"] == "stopping"
    assert row["control_version"] == 2


def test_stale_session_id_cannot_switch_current_session(sessions):
    store, catalogue = sessions
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue, request=request)
    with pytest.raises(ControlConflict):
        store.request_mm_session(action="switch", expected_version=1, current_session_id="old-session",
                                 catalogue=catalogue, request=request)
    assert store.read()["control_version"] == 1


def test_owner_update_is_atomic_and_stale_generation_cannot_change_anchors(sessions):
    store, catalogue = sessions
    result = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                      request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    owner = BotOwnership(store.engine, "hummingbot")
    assert owner.try_acquire()
    try:
        kwargs = dict(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                      session_id=result["session_id"], generation=0)
        activated = store.update_mm_session(**kwargs, change=lambda session, now: session.ready(
            now=now, anchors={"life_spot": {"life": "0.1", "sources": {}}}))
        assert activated.expires_at - activated.started_at == 60
        with pytest.raises(ControlConflict):
            store.update_mm_session(**{**kwargs, "generation": 1}, change=lambda session, now: setattr(session, "anchors", {}))
        assert store.read()["mm_session"] == activated.model_dump(mode="json")
        store.update_mm_session(**kwargs, change=lambda session, now: session.advance(now=now + 61, rng=random.Random(1)))
        assert store.read()["desired_state"] == "stopped"
        assert store.read()["control_version"] == 1
    finally:
        owner.release()


def test_start_and_switch_never_clear_recovery_halt(sessions):
    store, catalogue = sessions
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET recovery_state = '{\"halt\": true}'::jsonb"))
    with pytest.raises(ControlConflict, match="HALT"):
        store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                 request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    assert store.read()["control_version"] == 0
    assert store.read()["recovery_state"]["halt"] is True


def test_concurrent_starts_have_exactly_one_control_winner(sessions):
    store, catalogue = sessions
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)

    def start():
        try:
            return store.request_mm_session(action="start", expected_version=0, catalogue=catalogue, request=request)
        except ControlConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert store.read()["control_version"] == 1


def test_owner_restart_preserves_anchor_deadline_and_fences_previous_owner(sessions):
    store, catalogue = sessions
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                             request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    first = BotOwnership(store.engine, "hummingbot")
    successor = BotOwnership(store.engine, "hummingbot")
    assert first.try_acquire()
    saved = MMSession.model_validate(store.read()["mm_session"])
    kwargs = dict(owner_uuid=first.owner_uuid, owner_generation=first.generation, version=1,
                  session_id=saved.id, generation=0)
    store.update_mm_session(**kwargs, change=lambda session, now: session.ready(
        now=now, anchors={"life_spot": {"life": "0.1", "sources": {}}}))
    before = store.read()["mm_session"]
    first.release()
    assert successor.try_acquire()
    try:
        assert store.read()["mm_session"] == before
        with pytest.raises(ControlConflict):
            store.update_mm_session(**kwargs, change=lambda session, now: session.ready(
                now=now + 30, anchors={"life_spot": {"life": "0.2", "sources": {}}}))
        assert store.read()["mm_session"] == before
    finally:
        successor.release()


def test_cli_stop_revokes_mm_generation_and_allows_new_admin_session_after_owner_cleanup(sessions, monkeypatch):
    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.core import order_submission_guard

    store, catalogue = sessions
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    first = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue, request=request)
    owner = BotOwnership(store.engine, "hummingbot")
    assert owner.try_acquire()
    try:
        store.control(expected_version=1, desired_state="stopped")
        pending = store.read()["mm_session"]
        assert pending["status"] == "stopping"
        assert pending["generation"] == 1
        with pytest.raises(ControlConflict, match="active"):
            store.request_mm_session(action="start", expected_version=2, catalogue=catalogue, request=request)

        monkeypatch.setattr(order_submission_guard, "install_guard", lambda owner: None)
        host = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}),
                           asyncio.Event(), MagicMock())
        host.store, host.owner = store, owner
        host.hb = SimpleNamespace(trading_core=SimpleNamespace())
        host.stop_core = AsyncMock(return_value=True)
        host.poll_reconciliation = AsyncMock()
        host.poll_funding = MagicMock()
        host.snapshot = MagicMock(return_value={"running": False, "phase": "stopped"})
        asyncio.run(host.observe_once())
        assert host.phase == "stopped"
        assert store.read()["mm_session"]["status"] == "stopped"
        assert store.read()["acknowledged_version"] == 2
        second = store.request_mm_session(action="start", expected_version=2, catalogue=catalogue, request=request)
        assert second["session_id"] != first["session_id"]
    finally:
        owner.release()


@pytest.mark.parametrize("desired", ["running", "stopped"])
def test_explicit_legacy_selection_replaces_mm_snapshot_without_leaving_admin_start_blocked(sessions, desired):
    store, catalogue = sessions
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue, request=request)
    store.control(expected_version=1, desired_state=desired,
                  config_file_path="legacy.yml", config_type="controller")
    row = store.read()
    assert row["config_type"] == "controller"
    assert row["mm_session"] is None
    store.control(expected_version=2, desired_state="stopped")
    store.request_mm_session(action="start", expected_version=3, catalogue=catalogue, request=request)


def test_cli_stop_preserves_recovery_halt(sessions):
    store, catalogue = sessions
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                             request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET recovery_state = '{\"halt\": true, \"reason\": \"review\"}'::jsonb"))
    store.control(expected_version=1, desired_state="stopped")
    assert store.read()["recovery_state"] == {"halt": True, "reason": "review"}
    assert store.read()["mm_session"]["status"] == "stopping"
