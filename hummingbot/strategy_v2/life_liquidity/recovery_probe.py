"""Sequential DEGRADED probes use durable campaign fill and pending capacity."""

from decimal import Decimal
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent


class RecoveryProbeGuard:
    def __init__(self, path: Path, reservations: ReservationLedger, *,
                 max_quote_base: Decimal, max_campaign_base: Decimal, create: bool):
        if (not isinstance(reservations, ReservationLedger) or reservations.path is None
                or any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0
                       for v in (max_quote_base, max_campaign_base))
                or max_quote_base > max_campaign_base):
            raise ValueError("RECOVERY_PROBE_POLICY_INVALID")
        self.reservations = reservations
        self.max_quote_base, self.max_campaign_base = max_quote_base, max_campaign_base
        self.journal = PolicyState(path, policy={
            "reservation_path": str(reservations.path.resolve()),
            "max_quote_base": str(max_quote_base), "max_campaign_base": str(max_campaign_base)},
            initial={}, create=create)

    def _policy_matches(self):
        return self.journal.policy == {
            "reservation_path": str(self.reservations.path.resolve()),
            "max_quote_base": str(self.max_quote_base), "max_campaign_base": str(self.max_campaign_base)}

    def capacity_available(self) -> bool:
        try:
            with self.journal.locked():
                return self._policy_matches() and self.reservations.preview().filled_base_total < self.max_campaign_base
        except Exception:
            return False

    def authorizes(self, side: str, quantity: Decimal, *, exclude_open_intent: SpotIntent | None = None) -> bool:
        if (side not in ("BUY", "SELL") or not isinstance(quantity, Decimal)
                or not quantity.is_finite() or not 0 < quantity <= self.max_quote_base):
            return False
        try:
            with self.journal.locked():
                preview = self.reservations.preview(exclude_open_intent=exclude_open_intent)
                return (self._policy_matches() and preview.unresolved_quantity_base("BUY") + preview.unresolved_quantity_base("SELL") == 0
                        and preview.filled_base_total + quantity <= self.max_campaign_base)
        except Exception:
            return False
