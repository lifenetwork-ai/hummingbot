"""Conservative own-order subtraction from an aggregated LIFE spot book.

The public book has price-level quantities, not per-order IDs. A caller must
provide a complete, independently reconciled set of this bot's open orders.
Uncertain identity, state, or timing makes the reference unavailable.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.market_data import MarketSnapshot
from hummingbot.strategy_v2.life_liquidity.reference import LifeMarketEvidence


@dataclass(frozen=True)
class OwnBookOrder:
    wire_id: str
    exchange_order_id: str
    instrument: str
    side: str
    price_usdt: Decimal
    remaining_base: Decimal
    state: str


@dataclass(frozen=True)
class OwnDepthDecision:
    evidence: LifeMarketEvidence | None
    reason_code: str


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _levels_valid(levels: tuple[tuple[Decimal, Decimal], ...], *, descending: bool) -> bool:
    if not isinstance(levels, tuple) or not levels:
        return False
    previous = None
    for row in levels:
        if (not isinstance(row, tuple) or len(row) != 2
                or not _positive(row[0]) or not _positive(row[1])):
            return False
        if previous is not None and (
                (descending and row[0] >= previous)
                or (not descending and row[0] <= previous)):
            return False
        previous = row[0]
    return True


def separate_own_depth(*, book: MarketSnapshot,
                       own_orders: tuple[OwnBookOrder, ...],
                       order_scope_complete: bool,
                       orders_observed_monotonic: float,
                       max_observation_skew_ms: int,
                       max_book_age_ms: int,
                       max_depth_distance_bps: Decimal) -> OwnDepthDecision:
    def unavailable(reason: str) -> OwnDepthDecision:
        return OwnDepthDecision(None, reason)

    if (not isinstance(book, MarketSnapshot) or book.instrument != "LIFE-USDT"
            or book.data_source != "okx_rest_books" or book.market_state != "live"
            or not isinstance(book.exchange_timestamp_ms, int)
            or isinstance(book.exchange_timestamp_ms, bool)
            or book.exchange_timestamp_ms <= 0
            or not isinstance(book.sequence_id, int)
            or isinstance(book.sequence_id, bool) or book.sequence_id < 0
            or not _levels_valid(book.bid_depth, descending=True)
            or not _levels_valid(book.ask_depth, descending=False)
            or book.bid_depth[0][0] >= book.ask_depth[0][0]
            or (book.bid, book.bid_size) != book.bid_depth[0]
            or (book.ask, book.ask_size) != book.ask_depth[0]):
        return unavailable("BOOK_SNAPSHOT_UNAVAILABLE")
    if (not isinstance(max_book_age_ms, int) or isinstance(max_book_age_ms, bool)
            or max_book_age_ms <= 0 or not isinstance(book.observed_age_ms, int)
            or isinstance(book.observed_age_ms, bool)
            or book.observed_age_ms < 0 or book.observed_age_ms > max_book_age_ms):
        return unavailable("BOOK_SNAPSHOT_STALE")
    if order_scope_complete is not True or not isinstance(own_orders, tuple):
        return unavailable("OWN_ORDER_SCOPE_INCOMPLETE")
    if (not isinstance(max_observation_skew_ms, int)
            or isinstance(max_observation_skew_ms, bool) or max_observation_skew_ms <= 0
            or not isinstance(book.received_monotonic, (int, float))
            or not isinstance(orders_observed_monotonic, (int, float))
            or isinstance(book.received_monotonic, bool)
            or isinstance(orders_observed_monotonic, bool)
            or not math.isfinite(book.received_monotonic)
            or not math.isfinite(orders_observed_monotonic)
            or book.received_monotonic < 0 or orders_observed_monotonic < 0
            or abs(book.received_monotonic - orders_observed_monotonic) * 1000
            > max_observation_skew_ms):
        return unavailable("OWN_ORDER_SNAPSHOT_STALE")
    if (not isinstance(max_depth_distance_bps, Decimal)
            or not max_depth_distance_bps.is_finite()
            or not Decimal("0") <= max_depth_distance_bps < Decimal("10000")):
        return unavailable("OWN_DEPTH_POLICY_INVALID")

    levels = {
        "BUY": {price: size for price, size in book.bid_depth},
        "SELL": {price: size for price, size in book.ask_depth},
    }
    wire_ids = set()
    exchange_ids = set()
    for order in own_orders:
        if (not isinstance(order, OwnBookOrder)
                or not isinstance(order.wire_id, str) or not order.wire_id
                or not isinstance(order.exchange_order_id, str) or not order.exchange_order_id
                or order.wire_id in wire_ids
                or order.exchange_order_id in exchange_ids
                or order.instrument != book.instrument
                or order.side not in ("BUY", "SELL")
                or order.state not in ("OPEN", "CANCEL_PENDING")
                or not _positive(order.price_usdt)
                or not _positive(order.remaining_base)):
            return unavailable("OWN_ORDER_IDENTITY_UNAVAILABLE")
        wire_ids.add(order.wire_id)
        exchange_ids.add(order.exchange_order_id)
        remaining = levels[order.side].get(order.price_usdt)
        if remaining is None or order.remaining_base > remaining:
            return unavailable("OWN_ORDER_BOOK_MISMATCH")
        levels[order.side][order.price_usdt] = remaining - order.remaining_base

    bids = [(price, levels["BUY"][price]) for price, _ in book.bid_depth
            if levels["BUY"][price] > 0]
    asks = [(price, levels["SELL"][price]) for price, _ in book.ask_depth
            if levels["SELL"][price] > 0]
    if not bids or not asks:
        return unavailable("INDEPENDENT_BOOK_SIDE_EMPTY")
    bid, ask = bids[0][0], asks[0][0]
    if bid >= ask:
        return unavailable("INDEPENDENT_BOOK_CROSSED")
    distance = max_depth_distance_bps / Decimal("10000")
    bid_floor = bid * (Decimal("1") - distance)
    ask_ceiling = ask * (Decimal("1") + distance)
    bid_depth = sum((size for price, size in bids if price >= bid_floor), Decimal("0"))
    ask_depth = sum((size for price, size in asks if price <= ask_ceiling), Decimal("0"))
    evidence = LifeMarketEvidence(
        mid_usdt=(bid + ask) / Decimal("2"), bid_depth_base=bid_depth,
        ask_depth_base=ask_depth, own_depth_separated=True,
        exchange_timestamp_ms=book.exchange_timestamp_ms,
        source=f"okx:{book.instrument}")
    return OwnDepthDecision(evidence, "OWN_DEPTH_SEPARATED")
