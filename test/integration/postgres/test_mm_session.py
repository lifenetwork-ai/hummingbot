import asyncio
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event, text

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.order_submission_guard import SessionSubmissionBlocked
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.mm_event import session_events


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


def test_continued_random_steps_preserve_anchors_and_record_each_boundary(sessions):
    store, catalogue = sessions
    receipt = store.request_mm_session(
        action="start", expected_version=0, catalogue=catalogue,
        request=MMSessionRequest(market_mode="spot", selection="random", duration_seconds=3600,
                                 profile_ids=["follow_market", "sideways"]),
        rng=SimpleNamespace(choice=lambda _: "sideways"))
    owner = BotOwnership(store.engine, "hummingbot")
    assert owner.try_acquire()
    try:
        scope = dict(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                     session_id=receipt["session_id"], generation=0)
        anchors = {"life_spot": {"life": "0.1", "sources": {}}}
        current = store.update_mm_session(**scope, change=lambda session, now: session.ready(now=now, anchors=anchors))
        for step in (1, 2):
            boundary = current.step_expires_at + .001
            current = store.update_mm_session(**scope, change=lambda session, _: session.advance(
                now=boundary, rng=None, repeat=True))
            assert current.step_index == step
            assert current.generation == 0 and current.anchors == anchors and current.status == "running"
            assert current.permits(session_id=current.id, generation=0, now=boundary)
        history = session_events(store, current.id, event_type="step_changed")["items"]
        assert [event["payload"]["step_index"] for event in history] == [2, 1]
        assert all(event["payload"]["status"] == "running" for event in history)
        assert len({event["event_key"] for event in history}) == 2
    finally:
        owner.release()


def test_session_update_reads_database_time_after_lock_in_one_projected_query(sessions):
    store, catalogue = sessions
    receipt = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                       request=MMSessionRequest(market_mode="spot", profile_id="sideways",
                                                                duration_seconds=60))
    owner = BotOwnership(store.engine, "hummingbot")
    assert owner.try_acquire()
    reads = []
    worker_pids = []
    main_thread = threading.get_ident()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if threading.get_ident() == main_thread:
            return
        if statement.startswith(('SELECT', 'WITH')):
            reads.append(statement)
        if 'mm_session' in statement and 'FOR UPDATE' in statement:
            worker_pids.append(connection.connection.driver_connection.get_backend_pid())

    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            with store.engine.begin() as locker:
                locker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE'))
                future = worker.submit(
                    store.update_mm_session, owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                    version=1, session_id=receipt['session_id'], generation=0,
                    change=lambda session, now: session.ready(
                        now=now, anchors={"life_spot": {"life": "0.1", "sources": {}}}))
                deadline = time.monotonic() + 3
                while not (worker_pids and locker.execute(
                        text('SELECT pg_backend_pid() = ANY(pg_blocking_pids(:worker_pid))'),
                        {'worker_pid': worker_pids[0]}).scalar()):
                    assert time.monotonic() < deadline, 'Session writer did not reach the row lock'
                    time.sleep(.01)
                released_after = locker.execute(text('SELECT EXTRACT(EPOCH FROM clock_timestamp())')).scalar()
            saved = future.result(timeout=3)
        assert saved.started_at >= float(released_after)
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)
        owner.release()

    assert len(reads) == 1
    locked_read = reads[0]
    assert 'FOR UPDATE' in locked_read
    assert 'clock_timestamp()' in locked_read
    assert 'bot_state.status' not in locked_read
    assert 'bot_state.heartbeat' not in locked_read


def test_guard_policy_and_anchors_survive_pg_handover_but_recovery_delay_restarts(sessions):
    from test.hummingbot.strategy_v2.controllers.test_life_mm import make_controller

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings

    store, original = sessions
    raw = original.model_dump(mode="json")
    for market in raw["markets"].values():
        market["quote_guards"] = dict(enabled=True, max_deviation_bps="200", resume_deviation_bps="100", recovery_delay_seconds="2")
    raw["profiles"]["sideways"]["sideways_range"] = dict(
        lower_price=".09", upper_price=".11", resume_lower_price=".095", resume_upper_price=".105")
    catalogue = MMProfilesConfig.model_validate(raw)
    result = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                      request=MMSessionRequest(market_mode="both", profile_id="sideways", duration_seconds=60))
    first = BotOwnership(store.engine, "hummingbot")
    assert first.try_acquire()
    saved = store.update_mm_session(owner_uuid=first.owner_uuid, owner_generation=first.generation,
                                    version=1, session_id=result["session_id"], generation=0,
                                    change=lambda session, now: session.ready(now=now, anchors={
                                        name: {"life": ".1", "sources": {}} for name in ("life_spot", "life_perpetual")}))
    before = store.read()["mm_session"]
    first.release()
    successor = BotOwnership(store.engine, "hummingbot")
    assert successor.try_acquire()
    try:
        host = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}), asyncio.Event(), MagicMock())
        restored = MMSession.model_validate(store.read()["mm_session"])
        configs = host.mm_effective_config(restored)["controller_config_overrides"]
        from controllers.market_making.life_mm import LifeMMConfig
        for config in configs:
            controller, _ = make_controller(LifeMMConfig.model_validate(config))
            assert controller._quote_guard.blocked
            assert controller._quote_guard.good_since is None
            assert controller.reference_state() == {"life": "0.1", "sources": {}}
            assert controller.config.sideways_range.lower_price == Decimal(".09")
        assert restored.expires_at == saved.expires_at
        assert store.read()["mm_session"] == before
    finally:
        successor.release()


def test_stop_revokes_session_without_waiting_for_owner(sessions):
    store, catalogue = sessions
    result = store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                                      request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    store.request_mm_session(action="stop", expected_version=1, current_session_id=result["session_id"])
    row = store.read()
    assert row["desired_state"] == "stopped"
    assert row["mm_session"]["status"] == "stopping"
    assert row["control_version"] == 2


def test_cli_mm_stop_model_loading_does_not_block_owner_heartbeat(sessions, monkeypatch):
    import builtins

    store, catalogue = sessions
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue,
                             request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET owner_uuid='review-owner', owner_generation=1"))
    original_import = builtins.__import__
    heartbeats = []

    def cold_model_import(name, *args, **kwargs):
        if name == "hummingbot.core.mm_session":
            # A cold CLI process can spend seconds loading model dependencies.
            # A separate owner connection must still publish during that work.
            heartbeats.append(store.publish(owner_uuid="review-owner", owner_generation=1,
                                            version=1, status={"running": True}))
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", cold_model_import)
    assert store.control(expected_version=1, desired_state="stopped") == 2
    assert heartbeats and all(heartbeats)
    assert store.read()["mm_session"]["status"] == "stopping"


def test_stale_session_id_cannot_switch_current_session(sessions):
    store, catalogue = sessions
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    store.request_mm_session(action="start", expected_version=0, catalogue=catalogue, request=request)
    with pytest.raises(ControlConflict):
        store.request_mm_session(action="switch", expected_version=1, current_session_id="old-session",
                                 catalogue=catalogue, request=request)
    assert store.read()["control_version"] == 1


@pytest.mark.parametrize('outcome', ['promote', 'stop', 'failed'])
def test_same_market_admin_switch_keeps_old_gate_until_owned_promotion(sessions, outcome):
    store, catalogue = sessions
    admin = AdminStore(store)
    request = MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=60)
    original = store.request_mm_session(action='start', expected_version=0, catalogue=catalogue, request=request)
    owner = BotOwnership(store.engine, 'hummingbot')
    assert owner.try_acquire()
    try:
        old_id = original['session_id']
        old_scope = (old_id, 0)
        store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                                version=1, session_id=old_id, generation=0,
                                change=lambda session, now: session.ready(now=now, anchors={
                                    'life_spot': {'life': '0.1', 'sources': {}}}))
        owner.prepare(1, 'life_mm_profiles.yaml')
        owner.allow_new_orders, owner.mm_ready_check = True, lambda: True
        payload = dict(action='switch', expected_version=1, session_id=old_id, profile_id='follow_token')
        replacement = request.model_copy(update={'profile_id': 'follow_token'})
        receipt = admin.command('switch-key', payload, catalogue=catalogue, request=replacement)
        assert admin.command('switch-key', payload, catalogue=catalogue, request=replacement)['id'] == receipt['id']
        assert receipt['effective_version'] == 2
        new_id = receipt['session_id']
        before = owner.check_owner(new_risk=True)
        assert before['mm_session']['id'] == old_id and before['control_version'] == 1
        owner._check_mm_authority(before, old_scope)
        with pytest.raises(SessionSubmissionBlocked):
            owner._check_mm_authority(before, (new_id, 0))
        with pytest.raises(ControlConflict, match='finish preparing'):
            admin.command('another-key', payload, catalogue=catalogue, request=replacement)
        assert store.read(publication=True)['recovery_summary']['profile_switch_pending']
        assert not store.finish_profile_switch(owner_uuid='other', owner_generation=owner.generation, target_id=new_id)
        if outcome == 'stop':
            stopped = admin.command('stop-key', dict(action='stop', expected_version=1, session_id=old_id), catalogue=catalogue)
            assert stopped['effective_version'] == 2
            assert admin.command_by_id(receipt['id'])['status'] == 'superseded'
        error = 'profile_preparation_unavailable' if outcome == 'failed' else None
        assert store.finish_profile_switch(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                                           target_id=new_id, error=error) is (outcome == 'promote')
        row = store.read()
        assert not (row['recovery_state'] or {}).get('mm_profile_switch')
        if outcome == 'failed':
            assert admin.command_by_id(receipt['id'])['error'] == error
            owner._check_mm_authority(owner.check_owner(new_risk=True), old_scope)
        else:
            with pytest.raises(SessionSubmissionBlocked):
                owner.check_owner(new_risk=True)
        if outcome == 'promote':
            assert row['mm_session']['id'] == new_id and row['mm_session']['status'] == 'preparing'
            with pytest.raises(SessionSubmissionBlocked):
                owner._check_mm_authority(owner.check_owner(), (new_id, 0))
    finally:
        owner.release()


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
