"""Local WAL/reservation/order-tracker view for conservative own-depth replay.

This checks in-process state only. Authenticated account-wide pending orders and
an exchange-synchronized book remain separate live qualification requirements.
"""

from dataclasses import dataclass
from decimal import Decimal

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.strategy_v2.life_liquidity.market_data import MarketSnapshot
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnBookOrder, OwnDepthDecision, separate_own_depth
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@dataclass(frozen=True)
class LocalOwnOrderScope:
    orders: tuple[OwnBookOrder, ...]
    complete: bool
    reason_code: str


def _tracker_snapshot(tracker: ClientOrderTracker):
    active = dict(tracker.active_orders)
    lost = dict(tracker.lost_orders)
    cached = dict(tracker.cached_orders)
    if set(active) & set(lost) or set(active) & set(cached) or set(lost) & set(cached):
        raise ValueError("duplicate tracked order")
    fingerprint = tuple(sorted((key, id(order), order.attributes)
                               for key, order in {**active, **lost, **cached}.items()))
    return active, lost, cached, fingerprint


def collect_local_own_orders(wal: IntentWAL, reservations: ReservationLedger,
                             tracker: ClientOrderTracker, *,
                             pre_send_intent_id: str | None = None) -> LocalOwnOrderScope:
    def unavailable(reason: str) -> LocalOwnOrderScope:
        return LocalOwnOrderScope((), False, reason)

    if (not isinstance(wal, IntentWAL) or not isinstance(reservations, ReservationLedger)
            or not isinstance(tracker, ClientOrderTracker)
            or pre_send_intent_id is not None
            and (not isinstance(pre_send_intent_id, str) or not pre_send_intent_id)):
        return unavailable("OWN_ORDER_SCOPE_INCOMPLETE")
    try:
        records = wal.all_records()
        states = reservations.reservation_snapshot()
        active, lost, cached, initial_tracker = _tracker_snapshot(tracker)
        nonterminal_tracker = {**active, **lost}
        if any(order.trading_pair == "LIFE-USDT" and order.is_open
               for order in cached.values()):
            return unavailable("OWN_ORDER_TRACKER_UNSCOPED")
        if len({record.intent_id for record in records}) != len(records):
            return unavailable("OWN_ORDER_IDENTITY_UNAVAILABLE")
        known_reservation_ids = {record.reservation_id for record in records}
        if any(state.state != "TERMINAL" and reservation_id not in known_reservation_ids
               for reservation_id, state in states.items()):
            return unavailable("OWN_ORDER_RESERVATION_MISMATCH")
        seen_wire_ids = set()
        used_tracker_ids = set()
        pre_send_matched = False
        own_orders = []
        for record in records:
            state = states.get(record.reservation_id)
            if record.state in ("TERMINAL", "ABORTED_BEFORE_SEND"):
                if state is not None and state.state != "TERMINAL":
                    return unavailable("OWN_ORDER_RESERVATION_MISMATCH")
                continue
            if (not record.client_order_id or record.client_order_id in seen_wire_ids
                    or record.reservation_id != record.intent_id
                    or record.slot_market != "LIFE-USDT"
                    or record.slot_side not in ("BUY", "SELL")):
                return unavailable("OWN_ORDER_IDENTITY_UNAVAILABLE")
            seen_wire_ids.add(record.client_order_id)
            if state is None or (state.intent.intent_id, state.intent.session_id,
                                 state.intent.epoch, state.intent.side) != (
                    record.intent_id, record.session_id, record.epoch, record.slot_side):
                return unavailable("OWN_ORDER_RESERVATION_MISMATCH")
            if (record.state == "PREPARED" and record.intent_id == pre_send_intent_id
                    or record.state == "SEND_UNKNOWN"):
                tracked = nonterminal_tracker.get(record.client_order_id)
                if (record.intent_id != pre_send_intent_id or pre_send_matched
                        or record.exchange_order_id is not None or record.cancel_requested
                        or record.exchange_terminal_observed or state.state != "OPEN"
                        or state.remaining_base != state.intent.quantity_base
                        or state.filled_base != 0
                        or tracked is not None and (
                            not isinstance(tracked, InFlightOrder)
                            or tracked.client_order_id != record.client_order_id
                            or tracked.exchange_order_id is not None
                            or tracked.trading_pair != record.slot_market
                            or tracked.order_type != OrderType.LIMIT_MAKER
                            or tracked.trade_type != (TradeType.BUY if record.slot_side == "BUY"
                                                      else TradeType.SELL)
                            or tracked.price != state.intent.limit_price_usdt
                            or tracked.amount != state.intent.quantity_base
                            or tracked.executed_amount_base != 0
                            or tracked.current_state != OrderState.PENDING_CREATE)):
                    return unavailable("OWN_ORDER_SEND_UNKNOWN")
                pre_send_matched = True
                if tracked is not None:
                    used_tracker_ids.add(record.client_order_id)
                continue
            if record.state == "PREPARED":
                if state.state != "OPEN":
                    return unavailable("OWN_ORDER_RESERVATION_MISMATCH")
                if record.client_order_id in nonterminal_tracker:
                    return unavailable("OWN_ORDER_TRACKER_MISMATCH")
                continue
            if (record.state != "ACKED" or record.exchange_terminal_observed
                    or not record.exchange_order_id):
                return unavailable("OWN_ORDER_IDENTITY_UNAVAILABLE")
            if (state.state not in ("OPEN", "CANCEL_PENDING")
                    or state.remaining_base <= 0
                    or record.cancel_requested != (state.state == "CANCEL_PENDING")):
                return unavailable("OWN_ORDER_RESERVATION_MISMATCH")
            tracked = nonterminal_tracker.get(record.client_order_id)
            if (not isinstance(tracked, InFlightOrder)
                    or tracked.client_order_id != record.client_order_id
                    or tracked.exchange_order_id != record.exchange_order_id
                    or tracked.trading_pair != record.slot_market
                    or tracked.order_type != OrderType.LIMIT_MAKER
                    or tracked.trade_type != (TradeType.BUY if record.slot_side == "BUY"
                                              else TradeType.SELL)
                    or tracked.price != state.intent.limit_price_usdt
                    or tracked.amount != state.intent.quantity_base
                    or tracked.executed_amount_base != state.filled_base
                    or tracked.amount - tracked.executed_amount_base != state.remaining_base
                    or tracked.current_state not in (OrderState.OPEN,
                                                     OrderState.PARTIALLY_FILLED,
                                                     OrderState.PENDING_CANCEL)):
                return unavailable("OWN_ORDER_TRACKER_MISMATCH")
            used_tracker_ids.add(record.client_order_id)
            own_orders.append(OwnBookOrder(
                wire_id=record.client_order_id,
                exchange_order_id=record.exchange_order_id,
                instrument=record.slot_market, side=record.slot_side,
                price_usdt=state.intent.limit_price_usdt,
                remaining_base=state.remaining_base,
                state="CANCEL_PENDING" if record.cancel_requested else "OPEN"))
        if (set(nonterminal_tracker) != used_tracker_ids
                or pre_send_intent_id is not None and not pre_send_matched):
            return unavailable("OWN_ORDER_TRACKER_UNSCOPED")
        if (wal.all_records() != records
                or reservations.reservation_snapshot() != states
                or _tracker_snapshot(tracker)[3] != initial_tracker):
            return unavailable("OWN_ORDER_SCOPE_CHANGED")
        return LocalOwnOrderScope(tuple(own_orders), True, "LOCAL_OWN_ORDERS_MATCHED")
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return unavailable("OWN_ORDER_SCOPE_INCOMPLETE")


def separate_local_own_depth(*, book: MarketSnapshot, wal: IntentWAL,
                             reservations: ReservationLedger,
                             tracker: ClientOrderTracker,
                             orders_observed_monotonic: float,
                             max_observation_skew_ms: int,
                             max_book_age_ms: int,
                             max_depth_distance_bps: Decimal,
                             pre_send_intent_id: str | None = None) -> OwnDepthDecision:
    scope = collect_local_own_orders(
        wal, reservations, tracker, pre_send_intent_id=pre_send_intent_id)
    if not scope.complete:
        return OwnDepthDecision(None, scope.reason_code)
    return separate_own_depth(
        book=book, own_orders=scope.orders, order_scope_complete=True,
        orders_observed_monotonic=orders_observed_monotonic,
        max_observation_skew_ms=max_observation_skew_ms,
        max_book_age_ms=max_book_age_ms,
        max_depth_distance_bps=max_depth_distance_bps)
