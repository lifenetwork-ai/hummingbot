"""Replay P5.13 local order scope through real Hummingbot tracker types."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_own_depth import book
from unittest.mock import MagicMock

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.strategy_v2.life_liquidity.own_depth_runner import collect_local_own_orders, separate_local_own_depth
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def D(value):
    return Decimal(value)


def setup_order(tmp_path, *, quantity="4", ack=True, tracked=True):
    wal = IntentWAL(tmp_path / "intents.json")
    ledger = ReservationLedger(
        life_balance=D("10"), usdt_balance=D("20"),
        limits=RiskLimits(D("0"), D("30"), D("100"), D("30")))
    tracker = ClientOrderTracker(MagicMock())
    intent = SpotIntent("intent-1", "BUY", D(quantity), D("1.00"), "s1", 1)
    wal.begin("intent-1", client_order_id="wire-1", session_id="s1", epoch=1,
              reservation_id="intent-1", slot_market="LIFE-USDT",
              slot_side="BUY", slot_level=0)
    assert ledger.reserve(intent, reference_price=D("1.05")).allowed
    wal.arm_send("intent-1", client_order_id="wire-1", session_id="s1", epoch=1,
                 reservation_id="intent-1")
    if ack:
        wal.acknowledge("intent-1", "ex-1")
    order = InFlightOrder(
        client_order_id="wire-1", trading_pair="LIFE-USDT",
        order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY,
        amount=D(quantity), price=D("1.00"), creation_timestamp=1.0,
        exchange_order_id="ex-1" if ack else None,
        initial_state=OrderState.OPEN if ack else OrderState.PENDING_CREATE)
    if tracked:
        tracker.start_tracking_order(order)
    return wal, ledger, tracker, order


def test_acked_order_from_real_tracker_is_subtracted(tmp_path):
    wal, ledger, tracker, _ = setup_order(tmp_path)
    scope = collect_local_own_orders(wal, ledger, tracker)
    assert scope.complete is True
    assert scope.orders[0].wire_id == "wire-1"
    decision = separate_local_own_depth(
        book=book(), wal=wal, reservations=ledger, tracker=tracker,
        orders_observed_monotonic=2.01,
        max_observation_skew_ms=100, max_book_age_ms=500,
        max_depth_distance_bps=D("200"))
    assert decision.evidence.bid_depth_base == D("12")


def test_cancel_pending_and_partial_fill_keep_remaining_own_depth(tmp_path):
    wal, ledger, tracker, tracked = setup_order(tmp_path)
    assert ledger.record_fill("intent-1", "trade-1", D("1"), D("1.00"))
    tracked.executed_amount_base = D("1")
    wal.mark_cancel_requested("intent-1")
    ledger.request_cancel("intent-1")
    tracked.current_state = OrderState.PENDING_CANCEL
    scope = collect_local_own_orders(wal, ledger, tracker)
    assert scope.complete is True
    assert scope.orders[0].remaining_base == D("3")
    assert scope.orders[0].state == "CANCEL_PENDING"


def test_send_unknown_and_missing_tracker_order_fail_closed(tmp_path):
    wal, ledger, tracker, _ = setup_order(tmp_path, ack=False, tracked=False)
    unknown = collect_local_own_orders(wal, ledger, tracker)
    assert unknown.complete is False
    assert unknown.reason_code == "OWN_ORDER_SEND_UNKNOWN"
    wal2, ledger2, tracker2, _ = setup_order(tmp_path / "other", tracked=False)
    missing = collect_local_own_orders(wal2, ledger2, tracker2)
    assert missing.complete is False
    assert missing.reason_code == "OWN_ORDER_TRACKER_MISMATCH"
    decision = separate_local_own_depth(
        book=book(), wal=wal2, reservations=ledger2, tracker=tracker2,
        orders_observed_monotonic=2.01, max_observation_skew_ms=100,
        max_book_age_ms=500, max_depth_distance_bps=D("200"))
    assert decision.evidence is None
    assert decision.reason_code == "OWN_ORDER_TRACKER_MISMATCH"


def test_exact_current_pre_send_intent_is_excluded_only_at_final_boundary(tmp_path):
    wal, ledger, tracker, tracked = setup_order(tmp_path, ack=False, tracked=True)
    assert collect_local_own_orders(wal, ledger, tracker).complete is False
    current = collect_local_own_orders(
        wal, ledger, tracker, pre_send_intent_id="intent-1")
    assert current.complete is True and current.orders == ()
    tracked.current_state = OrderState.OPEN
    assert collect_local_own_orders(
        wal, ledger, tracker, pre_send_intent_id="intent-1").complete is False
    tracked.current_state = OrderState.PENDING_CREATE
    assert collect_local_own_orders(
        wal, ledger, tracker, pre_send_intent_id="other").complete is False


def test_tracker_quantity_mismatch_or_unscoped_order_fails_closed(tmp_path):
    wal, ledger, tracker, tracked = setup_order(tmp_path)
    tracked.executed_amount_base = D("1")
    mismatch = collect_local_own_orders(wal, ledger, tracker)
    assert mismatch.complete is False
    assert mismatch.reason_code == "OWN_ORDER_TRACKER_MISMATCH"
    tracked.executed_amount_base = D("0")
    extra = InFlightOrder(
        client_order_id="manual", trading_pair="LIFE-USDT",
        order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.SELL,
        amount=D("1"), price=D("1.10"), creation_timestamp=1.0,
        exchange_order_id="ex-manual", initial_state=OrderState.OPEN)
    tracker.start_tracking_order(extra)
    unscoped = collect_local_own_orders(wal, ledger, tracker)
    assert unscoped.complete is False
    assert unscoped.reason_code == "OWN_ORDER_TRACKER_UNSCOPED"


def test_active_reservation_without_wal_identity_fails_closed(tmp_path):
    wal, ledger, tracker, _ = setup_order(tmp_path)
    orphan = SpotIntent("orphan", "SELL", D("1"), D("1.10"), "s1", 1)
    assert ledger.reserve(orphan, reference_price=D("1.05")).allowed
    scope = collect_local_own_orders(wal, ledger, tracker)
    assert scope.complete is False
    assert scope.reason_code == "OWN_ORDER_RESERVATION_MISMATCH"
