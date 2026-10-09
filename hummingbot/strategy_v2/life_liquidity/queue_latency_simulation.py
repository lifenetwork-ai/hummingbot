"""Seeded offline queue/latency model; never infers fills from candle touches.

This is a conservative research fixture, not a calibrated exchange simulator.
Every accepted print must be identified as external. Its seed, assumptions,
and event sequence are returned so an offline result can be replayed.
"""

import random
from dataclasses import asdict, dataclass
from decimal import Decimal


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _millis(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class QueueFillAssumptions:
    seed: int
    queue_ahead_min_lots: int
    queue_ahead_max_lots: int
    lot_base: Decimal
    ack_latency_ms: int
    cancel_latency_ms: int
    maker_fee_rate: Decimal

    def __post_init__(self):
        if (not _millis(self.seed) or not _millis(self.queue_ahead_min_lots)
                or not _millis(self.queue_ahead_max_lots)
                or self.queue_ahead_min_lots > self.queue_ahead_max_lots
                or not _positive(self.lot_base) or not _millis(self.ack_latency_ms)
                or not _millis(self.cancel_latency_ms)
                or not _nonnegative(self.maker_fee_rate)
                or self.maker_fee_rate >= 1):
            raise ValueError("QUEUE_ASSUMPTIONS_INVALID")


@dataclass(frozen=True)
class QueueFillOrder:
    side: str
    price_quote: Decimal
    quantity_base: Decimal
    submitted_ms: int
    cancel_requested_ms: int | None

    def __post_init__(self):
        if (self.side not in ("BUY", "SELL") or not _positive(self.price_quote)
                or not _positive(self.quantity_base) or not _millis(self.submitted_ms)
                or (self.cancel_requested_ms is not None
                    and (not _millis(self.cancel_requested_ms)
                         or self.cancel_requested_ms < self.submitted_ms))):
            raise ValueError("QUEUE_ORDER_INVALID")


@dataclass(frozen=True)
class QueueFillEvent:
    observed_ms: int
    kind: str
    price_quote: Decimal
    quantity_base: Decimal
    aggressor: str | None = None
    independent: bool = True

    def __post_init__(self):
        if (not _millis(self.observed_ms) or self.kind not in ("TRADE", "CANDLE_TOUCH")
                or not _positive(self.price_quote) or not _positive(self.quantity_base)
                or self.aggressor not in (None, "BUY", "SELL")
                or not isinstance(self.independent, bool)):
            raise ValueError("QUEUE_EVENT_INVALID")


@dataclass(frozen=True)
class QueueFillResult:
    filled_base: Decimal
    remaining_base: Decimal
    fee_quote: Decimal
    queue_ahead_initial_base: Decimal
    queue_ahead_remaining_base: Decimal
    reason_code: str
    event_outcomes: tuple[str, ...]
    assumptions: dict
    order: dict
    events: tuple[dict, ...]


def _serialize(record) -> dict:
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(record).items()}


def simulate_queue_fills(assumptions: QueueFillAssumptions, order: QueueFillOrder,
                         events: list[QueueFillEvent]) -> QueueFillResult:
    if (not isinstance(assumptions, QueueFillAssumptions)
            or not isinstance(order, QueueFillOrder)
            or not isinstance(events, (list, tuple))
            or any(not isinstance(event, QueueFillEvent) for event in events)
            or any(next_event.observed_ms < event.observed_ms
                   for event, next_event in zip(events, events[1:]))):
        raise ValueError("QUEUE_REPLAY_INVALID")
    rng = random.Random(assumptions.seed)
    ahead = (Decimal(rng.randint(assumptions.queue_ahead_min_lots,
                                 assumptions.queue_ahead_max_lots))
             * assumptions.lot_base)
    initial_ahead = ahead
    filled = Decimal("0")
    outcomes = []
    eligible = False
    ack_at = order.submitted_ms + assumptions.ack_latency_ms
    cancel_at = (None if order.cancel_requested_ms is None else
                 order.cancel_requested_ms + assumptions.cancel_latency_ms)
    for event in events:
        if event.kind == "CANDLE_TOUCH":
            outcomes.append("NO_PRINT")
        elif event.observed_ms < ack_at:
            outcomes.append("AWAITING_ACK")
        elif cancel_at is not None and event.observed_ms >= cancel_at:
            outcomes.append("CANCEL_CONFIRMED")
        elif filled == order.quantity_base:
            outcomes.append("ORDER_FILLED")
        elif not event.independent or event.aggressor is None:
            outcomes.append("PRINT_UNQUALIFIED")
        elif event.aggressor != ("SELL" if order.side == "BUY" else "BUY"):
            outcomes.append("WRONG_AGGRESSOR")
        elif ((order.side == "BUY" and event.price_quote > order.price_quote)
              or (order.side == "SELL" and event.price_quote < order.price_quote)):
            outcomes.append("NOT_AT_QUOTE")
        else:
            eligible = True
            ahead_before = ahead
            ahead = max(Decimal("0"), ahead - event.quantity_base)
            fill = min(order.quantity_base - filled,
                       max(Decimal("0"), event.quantity_base - ahead_before))
            filled += fill
            outcomes.append("QUEUE_DEPLETED" if ahead_before > 0 and ahead == 0
                            else "PARTIAL_FILL" if fill > 0 else "QUEUE_AHEAD")
    reason = ("FILLED" if filled == order.quantity_base else
              "PARTIAL_FILL" if filled > 0 else
              "NO_FILL_QUEUE_AHEAD" if eligible else "NO_ELIGIBLE_EXTERNAL_PRINT")
    return QueueFillResult(
        filled_base=filled, remaining_base=order.quantity_base - filled,
        fee_quote=filled * order.price_quote * assumptions.maker_fee_rate,
        queue_ahead_initial_base=initial_ahead, queue_ahead_remaining_base=ahead,
        reason_code=reason, event_outcomes=tuple(outcomes),
        assumptions=_serialize(assumptions), order=_serialize(order),
        events=tuple(_serialize(event) for event in events))
