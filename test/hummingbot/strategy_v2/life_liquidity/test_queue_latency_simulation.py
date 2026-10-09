"""The offline fill model requires external prints and replays its assumptions."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.queue_latency_simulation import (
    QueueFillAssumptions,
    QueueFillEvent,
    QueueFillOrder,
    simulate_queue_fills,
)

D = Decimal


def _assumptions(**changes):
    values = dict(seed=7, queue_ahead_min_lots=1, queue_ahead_max_lots=1,
                  lot_base=D("1"), ack_latency_ms=100, cancel_latency_ms=200,
                  maker_fee_rate=D("0.001"))
    values.update(changes)
    return QueueFillAssumptions(**values)


def _order(**changes):
    values = dict(side="BUY", price_quote=D("1"), quantity_base=D("3"),
                  submitted_ms=0, cancel_requested_ms=None)
    values.update(changes)
    return QueueFillOrder(**values)


def test_candle_touch_and_print_before_ack_cannot_fill():
    events = [QueueFillEvent(50, "CANDLE_TOUCH", D("1"), D("100")),
              QueueFillEvent(90, "TRADE", D("1"), D("100"), aggressor="SELL",
                             independent=True)]
    result = simulate_queue_fills(_assumptions(), _order(), events)
    assert result.filled_base == 0
    assert result.reason_code == "NO_ELIGIBLE_EXTERNAL_PRINT"
    assert result.assumptions["seed"] == 7


def test_queue_ahead_then_partial_fill_and_fee_are_replayable():
    events = [QueueFillEvent(100, "TRADE", D("1"), D("0.5"), aggressor="SELL",
                             independent=True),
              QueueFillEvent(110, "TRADE", D("1"), D("1"), aggressor="SELL",
                             independent=True),
              QueueFillEvent(120, "TRADE", D("1"), D("2"), aggressor="SELL",
                             independent=True)]
    first = simulate_queue_fills(_assumptions(), _order(), events)
    assert first == simulate_queue_fills(_assumptions(), _order(), events)
    assert first.filled_base == D("2.5")
    assert first.fee_quote == D("0.0025")
    assert first.remaining_base == D("0.5")
    assert first.reason_code == "PARTIAL_FILL"
    assert first.event_outcomes == ("QUEUE_AHEAD", "QUEUE_DEPLETED", "PARTIAL_FILL")


def test_cancel_latency_can_fill_but_later_print_cannot():
    order = _order(cancel_requested_ms=150)
    events = [QueueFillEvent(160, "TRADE", D("1"), D("2"), aggressor="SELL",
                             independent=True),
              QueueFillEvent(350, "TRADE", D("1"), D("100"), aggressor="SELL",
                             independent=True)]
    result = simulate_queue_fills(_assumptions(), order, events)
    assert result.filled_base == D("1")
    assert result.remaining_base == D("2")
    assert result.event_outcomes == ("QUEUE_DEPLETED", "CANCEL_CONFIRMED")


def test_wrong_aggressor_nonexternal_and_stale_event_cannot_fill():
    events = [QueueFillEvent(100, "TRADE", D("1"), D("100"), aggressor="BUY",
                             independent=True),
              QueueFillEvent(110, "TRADE", D("1"), D("100"), aggressor="SELL",
                             independent=False)]
    result = simulate_queue_fills(_assumptions(), _order(), events)
    assert result.filled_base == 0
    assert result.event_outcomes == ("WRONG_AGGRESSOR", "PRINT_UNQUALIFIED")


def test_unattributed_print_is_unqualified_by_default():
    event = QueueFillEvent(100, "TRADE", D("1"), D("100"), aggressor="SELL")
    result = simulate_queue_fills(_assumptions(), _order(), [event])
    assert result.filled_base == 0
    assert result.reason_code == "NO_ELIGIBLE_EXTERNAL_PRINT"
    assert result.event_outcomes == ("PRINT_UNQUALIFIED",)


def test_seeded_queue_range_changes_only_ahead_not_hard_order_size():
    assumptions = _assumptions(queue_ahead_min_lots=0, queue_ahead_max_lots=3)
    events = [QueueFillEvent(100, "TRADE", D("1"), D("100"), aggressor="SELL",
                             independent=True)]
    result = simulate_queue_fills(assumptions, _order(), events)
    assert result.queue_ahead_initial_base in (D("0"), D("1"), D("2"), D("3"))
    assert result.filled_base == D("3")
    assert result.remaining_base == 0
