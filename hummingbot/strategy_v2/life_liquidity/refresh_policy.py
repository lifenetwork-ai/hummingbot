"""Offline LIFE spot refresh decision; no cancellation or replacement side effects.

The desired price and loss estimates must come from a separately qualified
snapshot. Production wiring, calibration, and exchange replay remain open.
"""

from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.slots import SlotStatus


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


@dataclass(frozen=True)
class RefreshPolicy:
    """Hard price drift in bps and normal refresh minimum age in milliseconds."""

    hard_drift_bps: Decimal
    min_refresh_age_ms: int

    def __post_init__(self):
        if (not _positive(self.hard_drift_bps)
                or not isinstance(self.min_refresh_age_ms, int)
                or isinstance(self.min_refresh_age_ms, bool)
                or self.min_refresh_age_ms <= 0):
            raise ValueError("REFRESH_POLICY_INVALID")


@dataclass(frozen=True)
class RefreshObservation:
    """Prices in USDT/LIFE, losses in USDT, latency in milliseconds.

    Expected losses and queue/API costs are external, qualified estimates;
    this object does not infer them from a public book or local fill alone.
    """

    slot: SlotStatus
    current_price_usdt: Decimal
    target_price_usdt: Decimal
    order_age_ms: int
    cancel_latency_ms: int
    stale_price_risk_quote: Decimal
    adverse_fill_risk_quote: Decimal
    queue_priority_loss_quote: Decimal
    api_cost_quote: Decimal
    latency_exposure_cost_quote_per_ms: Decimal
    cancel_capacity_ready: bool


@dataclass(frozen=True)
class RefreshDecision:
    """Advisory action and comparable USDT risk/cost amounts."""

    action: str
    reason_code: str
    drift_bps: Decimal | None
    stale_risk_quote: Decimal | None
    refresh_cost_quote: Decimal | None


def decide_quote_refresh(policy: RefreshPolicy,
                         observed: RefreshObservation) -> RefreshDecision:
    """Return an advisory action; a cancel ACK never authorizes replacement."""
    unavailable = RefreshDecision("PAUSE", "REFRESH_INPUT_UNAVAILABLE", None, None, None)
    if not isinstance(policy, RefreshPolicy) or not isinstance(observed, RefreshObservation):
        return unavailable
    slot = observed.slot
    valid_slot = (isinstance(slot, SlotStatus)
                  and slot.state in ("FREE", "PREPARED", "UNKNOWN", "OPEN",
                                     "CANCEL_PENDING", "RECONCILIATION_PENDING")
                  and (slot.intent_id is None if slot.state == "FREE"
                       else isinstance(slot.intent_id, str) and bool(slot.intent_id)))
    if (not valid_slot or not _positive(observed.current_price_usdt)
            or not _positive(observed.target_price_usdt)
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                   for value in (observed.order_age_ms, observed.cancel_latency_ms))
            or not all(_nonnegative(value) for value in (
                observed.stale_price_risk_quote, observed.adverse_fill_risk_quote,
                observed.queue_priority_loss_quote, observed.api_cost_quote,
                observed.latency_exposure_cost_quote_per_ms))
            or not isinstance(observed.cancel_capacity_ready, bool)):
        return unavailable
    drift = abs(observed.target_price_usdt / observed.current_price_usdt
                - Decimal("1")) * Decimal("10000")
    stale_risk = observed.stale_price_risk_quote + observed.adverse_fill_risk_quote
    refresh_cost = (observed.queue_priority_loss_quote + observed.api_cost_quote
                    + observed.cancel_latency_ms
                    * observed.latency_exposure_cost_quote_per_ms)
    if slot.state == "FREE":
        return RefreshDecision("NO_ORDER", "REFRESH_SLOT_FREE", drift,
                               stale_risk, refresh_cost)
    if slot.state != "OPEN":
        return RefreshDecision("WAIT_RECONCILIATION", "REFRESH_ORDER_UNRESOLVED",
                               drift, stale_risk, refresh_cost)
    if drift >= policy.hard_drift_bps:
        if not observed.cancel_capacity_ready:
            return RefreshDecision("HALT", "URGENT_CANCEL_CAPACITY_UNAVAILABLE",
                                   drift, stale_risk, refresh_cost)
        return RefreshDecision("CANCEL_URGENT", "HARD_PRICE_DRIFT",
                               drift, stale_risk, refresh_cost)
    if observed.order_age_ms < policy.min_refresh_age_ms:
        return RefreshDecision("KEEP", "REFRESH_MIN_AGE", drift,
                               stale_risk, refresh_cost)
    if stale_risk <= refresh_cost:
        return RefreshDecision("KEEP", "REFRESH_NOT_ECONOMIC", drift,
                               stale_risk, refresh_cost)
    if not observed.cancel_capacity_ready:
        return RefreshDecision("PAUSE", "CANCEL_CAPACITY_UNAVAILABLE", drift,
                               stale_risk, refresh_cost)
    return RefreshDecision("CANCEL", "REFRESH_RISK_EXCEEDS_COST", drift,
                           stale_risk, refresh_cost)
