"""P3 successor and benchmark-source transitions revoke old epochs."""

import json
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock

import pytest

from hummingbot.strategy_v2.life_liquidity.config import SessionConfig
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionManager, SessionStore


def manager(tmp_path, clock):
    return SessionManager(SessionStore(tmp_path / "transition.json"),
                          wall_clock=lambda: clock.wall, monotonic_clock=lambda: clock.mono,
                          max_reconciliation_age_ms=1000)


def start(active):
    return active.begin(SessionConfig(duration="10s", on_expiry="switch_to_market_reference",
                                      successor_duration="20s"),
                        anchors={"LIFE-USDT": Decimal("1"), "okx:BTC-USDT": Decimal("100000")},
                        model_version="relative-return-v1", config_version=1,
                        reference_mode="bounded_benchmark", ready=True)


def reconciled(primary, clock, **changes):
    values = dict(session_id=primary.session_id, epoch=primary.epoch,
                  observed_at=clock.wall, scope_complete=True,
                  open_order_ids=(), pending_cancel_ids=(), unknown_order_ids=(),
                  trade_events_reconciled=True)
    values.update(changes)
    return OrderReconciliation(**values)


def test_successor_requires_reconciliation_and_qualified_market_then_persists_once(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=None) == "TRANSITIONING"
    assert active.current_session.session_id == primary.session_id
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock),
                       market_reference_ready=False) == "TRANSITIONING"
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock), market_reference_ready=True,
                       market_anchor_usdt=Decimal("1.02")) == "ACTIVE"
    successor = active.current_session
    assert successor.session_id != primary.session_id
    assert successor.epoch == primary.epoch + 1
    assert successor.reference_mode == "market"
    assert successor.anchors["LIFE-USDT"] == Decimal("1.02")
    assert successor.started_at == primary.expires_at
    assert successor.expires_at == primary.expires_at + timedelta(seconds=20)
    assert active.can_quote(reference_ready=True, all_gates_ready=True,
                            market_reference_ready=True)
    restarted = manager(tmp_path, clock)
    assert restarted.current_session.session_id == successor.session_id
    restarted.tick(reference_ready=True, all_gates_ready=True,
                   reconciliation=reconciled(primary, clock), market_reference_ready=True,
                   market_anchor_usdt=Decimal("999"))
    assert restarted.current_session.session_id == successor.session_id
    assert restarted.current_session.anchors["LIFE-USDT"] == Decimal("1.02")


def test_successor_window_is_not_extended_by_downtime_and_never_chains(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(35)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock), market_reference_ready=True,
                       market_anchor_usdt=Decimal("1")) == "TRANSITIONING"
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock), market_reference_ready=True,
                       market_anchor_usdt=Decimal("1")) == "EXPIRED"
    assert active.current_session.expires_at == primary.expires_at + timedelta(seconds=20)
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    successor_id = active.current_session.session_id
    active.tick(reference_ready=True, all_gates_ready=True,
                reconciliation=reconciled(primary, clock), market_reference_ready=True,
                market_anchor_usdt=Decimal("2"))
    assert active.current_session.session_id == successor_id


def test_transition_is_durable_before_any_reconciliation_can_activate_successor(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock), market_reference_ready=True,
                       market_anchor_usdt=Decimal("1")) == "TRANSITIONING"
    restarted = manager(tmp_path, clock)
    assert restarted.state == "TRANSITIONING"
    assert not restarted.can_quote(reference_ready=True, all_gates_ready=True)
    assert restarted.tick(reference_ready=True, all_gates_ready=True,
                          reconciliation=reconciled(primary, clock), market_reference_ready=True,
                          market_anchor_usdt=Decimal("1.01")) == "ACTIVE"
    assert restarted.current_session.anchors["LIFE-USDT"] == Decimal("1.01")
    assert manager(tmp_path, clock).current_session.session_id == restarted.current_session.session_id


@pytest.mark.parametrize("changes, expected_reason", [
    ({"session_id": "wrong"}, "RECONCILIATION_SCOPE_MISMATCH"),
    ({"epoch": 99}, "RECONCILIATION_SCOPE_MISMATCH"),
    ({"scope_complete": False}, "RECONCILIATION_INCOMPLETE"),
    ({"open_order_ids": ("order-1",)}, "OLD_ORDERS_UNRESOLVED"),
    ({"pending_cancel_ids": ("order-1",)}, "OLD_ORDERS_UNRESOLVED"),
    ({"unknown_order_ids": ("order-1",)}, "OLD_ORDERS_UNRESOLVED"),
    ({"trade_events_reconciled": False}, "OLD_FILLS_UNRECONCILED"),
])
def test_unresolved_or_wrong_scope_evidence_cannot_activate_successor(tmp_path, changes, expected_reason):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    active.tick(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock, **changes),
                       market_reference_ready=True, market_anchor_usdt=Decimal("1")) == "TRANSITIONING"
    assert active.reason_code == expected_reason
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)


def test_pre_expiry_or_future_reconciliation_is_rejected(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    active.tick(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock,
                                                 observed_at=primary.started_at),
                       market_reference_ready=True) == "TRANSITIONING"
    assert active.reason_code == "RECONCILIATION_STALE"
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock,
                                                 observed_at=clock.wall + timedelta(seconds=1)),
                       market_reference_ready=True) == "TRANSITIONING"
    assert active.reason_code == "RECONCILIATION_FUTURE"
    clock.advance(2)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock,
                                                 observed_at=clock.wall - timedelta(seconds=2)),
                       market_reference_ready=True) == "TRANSITIONING"
    assert active.reason_code == "RECONCILIATION_STALE"


def test_successor_rechecks_market_reference_on_every_permission_and_tick(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    active.tick(reference_ready=True, all_gates_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       reconciliation=reconciled(primary, clock),
                       market_reference_ready=True,
                       market_anchor_usdt=Decimal("1")) == "ACTIVE"
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    assert active.can_quote(reference_ready=True, all_gates_ready=True,
                            market_reference_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       market_reference_ready=False) == "PAUSED"
    assert active.reason_code == "MARKET_REFERENCE_UNAVAILABLE"
    assert not active.can_quote(reference_ready=True, all_gates_ready=True,
                                market_reference_ready=True)
    assert active.tick(reference_ready=True, all_gates_ready=True,
                       market_reference_ready=True) == "ACTIVE"
    assert active.can_quote(reference_ready=True, all_gates_ready=True,
                            market_reference_ready=True)


@pytest.mark.parametrize("corruption", ["extended_deadline", "missing_reconciliation", "wrong_epoch"])
def test_restart_rejects_corrupted_successor_journal(tmp_path, corruption):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    clock.advance(10)
    active.tick(reference_ready=True, all_gates_ready=True)
    active.tick(reference_ready=True, all_gates_ready=True,
                reconciliation=reconciled(primary, clock), market_reference_ready=True,
                market_anchor_usdt=Decimal("1"))
    path = tmp_path / "transition.json"
    data = json.loads(path.read_text())
    if corruption == "extended_deadline":
        data["successor"]["expires_at"] = (
            active.current_session.expires_at + timedelta(hours=1)).isoformat()
    elif corruption == "missing_reconciliation":
        data["reconciled_at"] = None
    else:
        data["successor"]["epoch"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="session journal state invalid"):
        manager(tmp_path, clock)


def test_benchmark_source_change_requires_pause_reconciliation_and_new_version(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    with pytest.raises(ValueError, match="SESSION_NOT_PAUSED"):
        active.transition_reference(anchors={"LIFE-USDT": Decimal("1"), "okx:AVAX-USDT": Decimal("10")},
                                    model_version="v2", config_version=2, old_orders_reconciled=True)
    active.pause("SOURCE_CHANGE_PENDING")
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    with pytest.raises(ValueError, match="OLD_ORDERS_UNRESOLVED"):
        active.transition_reference(anchors={"LIFE-USDT": Decimal("1"), "okx:AVAX-USDT": Decimal("10")},
                                    model_version="v2", config_version=2, old_orders_reconciled=False)
    changed = active.transition_reference(
        anchors={"LIFE-USDT": Decimal("1"), "okx:AVAX-USDT": Decimal("10")},
        model_version="v2", config_version=2, old_orders_reconciled=True)
    assert changed.session_id == primary.session_id
    assert changed.epoch == primary.epoch + 1
    assert changed.started_at == primary.started_at
    assert changed.expires_at == primary.expires_at
    assert changed.anchors["okx:AVAX-USDT"] == Decimal("10")
    restarted = manager(tmp_path, clock)
    assert restarted.current_session.model_version == "v2"
    assert restarted.current_session.anchors == changed.anchors


def test_source_change_cannot_extend_an_expired_paused_session(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    start(active)
    active.pause("SOURCE_CHANGE_PENDING")
    clock.advance(10)
    with pytest.raises(ValueError, match="SESSION_DEADLINE_REACHED"):
        active.transition_reference(anchors={"LIFE-USDT": Decimal("1"), "okx:AVAX-USDT": Decimal("10")},
                                    model_version="v2", config_version=2, old_orders_reconciled=True)
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
