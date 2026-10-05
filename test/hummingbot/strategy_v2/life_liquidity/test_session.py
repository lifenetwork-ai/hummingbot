"""P3 sessions persist their original anchor and absolute UTC deadline."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.config import SessionConfig
from hummingbot.strategy_v2.life_liquidity.session import SessionManager, SessionStore


class FakeClock:
    def __init__(self):
        self.wall = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        self.mono = 100.0

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def manager(tmp_path, clock):
    return SessionManager(SessionStore(tmp_path / "session.json"),
                          wall_clock=lambda: clock.wall, monotonic_clock=lambda: clock.mono,
                          max_reconciliation_age_ms=1000)


def begin(active, config=None, anchor="1"):
    return active.begin(config or SessionConfig(duration="10s"),
                        anchors={"LIFE-USDT": Decimal(anchor)},
                        model_version="relative-return-v1", config_version=1,
                        reference_mode="market", ready=True)


def test_session_is_persisted_before_permission_and_restart_keeps_anchor(tmp_path):
    clock = FakeClock()
    first = manager(tmp_path, clock)
    started = begin(first, SessionConfig(duration="4h"))
    assert (tmp_path / "session.json").exists()
    assert started.started_at == clock.wall
    assert started.expires_at == clock.wall + timedelta(hours=4)
    assert first.can_quote(reference_ready=True, all_gates_ready=True)
    clock.advance(3600)
    restarted = manager(tmp_path, clock)
    loaded = begin(restarted, SessionConfig(duration="4h"), anchor="999")
    assert loaded.session_id == started.session_id
    assert loaded.anchors["LIFE-USDT"] == Decimal("1")
    assert loaded.expires_at == started.expires_at
    assert restarted.can_quote(reference_ready=True, all_gates_ready=True)


def test_short_session_expires_at_deadline_even_before_next_tick_and_cannot_revive(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    clock.advance(10)
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True) == "EXPIRED"
    assert active.reason_code == "SESSION_DEADLINE_REACHED"
    clock.advance(1)
    restarted = manager(tmp_path, clock)
    assert restarted.tick(reference_ready=True, all_gates_ready=True) == "EXPIRED"
    assert not restarted.can_quote(reference_ready=True, all_gates_ready=True)


def test_duration_update_uses_original_start_and_shortening_into_past_expires(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    original = begin(active, SessionConfig(duration="4h"))
    clock.advance(3600)
    active.update_duration("30m", config_version=2)
    assert active.current_session.started_at == original.started_at
    assert active.current_session.expires_at == original.started_at + timedelta(minutes=30)
    assert active.state == "EXPIRED"
    with pytest.raises(ValueError, match="SESSION_ALREADY_EXPIRED"):
        active.update_duration("8h", config_version=3)
    assert active.state == "EXPIRED"
    assert active.current_session.expires_at == original.started_at + timedelta(minutes=30)
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)


def test_scheduled_start_counts_downtime_and_explicit_ready_gate(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    scheduled = clock.wall + timedelta(seconds=10)
    config = SessionConfig(duration="10s", start_policy=scheduled.isoformat())
    assert active.begin(config, anchors={"LIFE-USDT": Decimal("1")},
                        model_version="v1", config_version=1,
                        reference_mode="market", ready=True) is None
    assert active.state == "WAITING_READY"
    clock.advance(25)
    record = active.begin(config, anchors={"LIFE-USDT": Decimal("1")},
                          model_version="v1", config_version=1,
                          reference_mode="market", ready=True)
    assert record.started_at == scheduled
    assert record.expires_at == scheduled + timedelta(seconds=10)
    assert active.state == "EXPIRED"


def test_clock_rollback_pauses_and_does_not_extend_session(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    clock.advance(3)
    active.tick(reference_ready=True, all_gates_ready=True)
    clock.wall -= timedelta(seconds=2)
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True) == "PAUSED"
    assert active.reason_code == "CLOCK_ROLLBACK"
    restarted = manager(tmp_path, clock)
    assert not restarted.can_quote(reference_ready=True, all_gates_ready=True)


def test_persistence_failure_cannot_create_an_active_in_memory_session(tmp_path, monkeypatch):
    clock = FakeClock()
    active = manager(tmp_path, clock)

    def fail_save(record):
        raise OSError("disk full")

    monkeypatch.setattr(active.store, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        begin(active)
    assert active.current_session is None
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
