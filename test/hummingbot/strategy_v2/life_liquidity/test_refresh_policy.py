"""P5.11 synthetic refresh decisions preserve safety and queue/API economics."""

from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.refresh_policy import RefreshObservation, RefreshPolicy, decide_quote_refresh
from hummingbot.strategy_v2.life_liquidity.slots import SlotStatus, SpotQuoteSlots
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def D(value):
    return Decimal(value)


def policy():
    return RefreshPolicy(hard_drift_bps=D("100"), min_refresh_age_ms=1000)


def observation(**changes):
    values = dict(
        slot=SlotStatus("OPEN", "i1"), current_price_usdt=D("1"),
        target_price_usdt=D("1.005"), order_age_ms=2000,
        cancel_latency_ms=100, stale_price_risk_quote=D("0.5"),
        adverse_fill_risk_quote=D("0.1"), queue_priority_loss_quote=D("0.2"),
        api_cost_quote=D("0.05"), latency_exposure_cost_quote_per_ms=D("0.001"),
        cancel_capacity_ready=True)
    values.update(changes)
    return RefreshObservation(**values)


def test_hard_price_drift_prioritizes_cancel_despite_age_and_queue_cost():
    decision = decide_quote_refresh(policy(), observation(
        target_price_usdt=D("1.02"), order_age_ms=10,
        queue_priority_loss_quote=D("100"), api_cost_quote=D("100")))
    assert decision.action == "CANCEL_URGENT"
    assert decision.reason_code == "HARD_PRICE_DRIFT"
    assert decision.drift_bps == D("200")


def test_normal_refresh_balances_latency_queue_priority_and_api_cost():
    fast = decide_quote_refresh(policy(), observation())
    slow = decide_quote_refresh(policy(), observation(cancel_latency_ms=1000))
    expensive_api = decide_quote_refresh(policy(), observation(api_cost_quote=D("1")))
    too_young = decide_quote_refresh(policy(), observation(order_age_ms=999))
    assert fast.action == "CANCEL"
    assert slow.action == "KEEP"
    assert expensive_api.action == "KEEP"
    assert too_young.action == "KEEP"


def test_adverse_fill_selection_changes_decision_at_same_price_and_latency():
    benign = decide_quote_refresh(policy(), observation(
        stale_price_risk_quote=D("0.1"), adverse_fill_risk_quote=D("0")))
    adverse = decide_quote_refresh(policy(), observation(
        stale_price_risk_quote=D("0.1"), adverse_fill_risk_quote=D("0.5")))
    assert benign.action == "KEEP"
    assert adverse.action == "CANCEL"


def test_required_cancel_without_request_capacity_pauses_new_quote_actions():
    normal = decide_quote_refresh(policy(), observation(cancel_capacity_ready=False))
    urgent = decide_quote_refresh(policy(), observation(
        target_price_usdt=D("1.02"), cancel_capacity_ready=False))
    assert normal.action == "PAUSE"
    assert urgent.action == "HALT"
    assert urgent.reason_code == "URGENT_CANCEL_CAPACITY_UNAVAILABLE"


def test_cancel_ack_and_terminal_observation_keep_refresh_waiting_for_wal_proof(tmp_path):
    wal = IntentWAL(tmp_path / "intents.json")
    slots = SpotQuoteSlots(wal)
    slots.claim(intent_id="i1", client_order_id="wire-1", reservation_id="i1",
                session_id="s1", epoch=1, side="BUY", level=0)
    wal.arm_send("i1", client_order_id="wire-1", session_id="s1",
                 epoch=1, reservation_id="i1")
    wal.acknowledge("i1", "exchange-1")
    wal.mark_cancel_requested("i1")

    pending = decide_quote_refresh(policy(), observation(
        slot=slots.status("s1", 1, "BUY", 0)))
    assert pending.action == "WAIT_RECONCILIATION"
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        slots.claim(intent_id="i2", client_order_id="wire-2", reservation_id="i2",
                    session_id="s1", epoch=1, side="BUY", level=0)
    wal.mark_exchange_terminal_observed("i1", "exchange-1")
    observed = decide_quote_refresh(policy(), observation(
        slot=slots.status("s1", 1, "BUY", 0)))
    assert observed.action == "WAIT_RECONCILIATION"
    wal.mark_terminal("i1", "exchange-1")
    free = decide_quote_refresh(policy(), observation(
        slot=slots.status("s1", 1, "BUY", 0)))
    assert free.action == "NO_ORDER"


@pytest.mark.parametrize("bad", [
    {"cancel_latency_ms": -1},
    {"target_price_usdt": D("NaN")},
    {"queue_priority_loss_quote": D("-1")},
    {"slot": SlotStatus("OPEN", None)},
])
def test_invalid_refresh_evidence_pauses(bad):
    decision = decide_quote_refresh(policy(), observation(**bad))
    assert decision.action == "PAUSE"
    assert decision.reason_code == "REFRESH_INPUT_UNAVAILABLE"
