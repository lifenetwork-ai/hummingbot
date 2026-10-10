"""Opt-in durable rolling capacity and reservation-derived spot stress."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from hummingbot.strategy_v2.life_liquidity.config import parse_duration_seconds
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.risk import ReservationPreview, SpotIntent
from hummingbot.strategy_v2.life_liquidity.stress import StressInputs, evaluate_stress


@dataclass(frozen=True)
class SpotStressObservation:
    value_at_ms: int
    observed_at_ms: int
    source_kind: str
    reference_usdt: Decimal
    stressed_exit_usdt: Decimal
    stressed_high_usdt: Decimal
    exit_depth_base: Decimal
    collateral_quote: Decimal
    maker_fee_rate: Decimal
    exit_fee_rate: Decimal


class SpotRiskBinding:
    def __init__(self, path: Path, *, attributor: ReconciledFillAttributor,
                 utc_clock_ms: Callable[[], int], stress_observation: Callable[[], SpotStressObservation],
                 window_ms: int, max_filled_base: Decimal, target_inventory_base: Decimal,
                 max_stress_loss_quote: Decimal, max_observation_age_ms: int, create: bool):
        if (not isinstance(attributor, ReconciledFillAttributor) or not callable(utc_clock_ms)
                or not callable(stress_observation) or type(window_ms) is not int or window_ms <= 0
                or type(max_observation_age_ms) is not int or max_observation_age_ms <= 0
                or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
                       for v in (max_filled_base, target_inventory_base, max_stress_loss_quote))
                or max_filled_base <= 0 or max_stress_loss_quote <= 0):
            raise ValueError("SPOT_RISK_POLICY_INVALID")
        self.attributor = attributor
        self.utc_clock_ms = utc_clock_ms
        self.stress_observation = stress_observation
        self.window_ms = window_ms
        self.max_filled_base = max_filled_base
        self.target_inventory_base = target_inventory_base
        self.max_stress_loss_quote = max_stress_loss_quote
        self.max_age_ms = max_observation_age_ms
        self.reason_code = "SPOT_RISK_REVALIDATION"
        self.journal = PolicyState(path, policy={
            "attribution_path": str(attributor.path.resolve()), "attribution_policy": attributor._policy(),
            "window_ms": window_ms, "max_filled_base": str(max_filled_base),
            "target_inventory_base": str(target_inventory_base),
            "max_stress_loss_quote": str(max_stress_loss_quote), "max_age_ms": max_observation_age_ms},
            initial={"checked_at_ms": None}, create=create)

    def matches_config(self, config) -> bool:
        try:
            risk = config.strategy.risk
            return (parse_duration_seconds(risk.rolling_fill_window) * 1000 == self.window_ms
                    and risk.max_filled_base_per_window == self.max_filled_base
                    and risk.stress_loss_budget_quote == self.max_stress_loss_quote)
        except (ValueError, TypeError, AttributeError):
            return False

    def check(self, candidate: SpotIntent | None, preview: ReservationPreview) -> str | None:
        try:
            with self.journal.locked() as state:
                if self.journal.policy != {
                    "attribution_path": str(self.attributor.path.resolve()),
                    "attribution_policy": self.attributor._policy(), "window_ms": self.window_ms,
                    "max_filled_base": str(self.max_filled_base),
                    "target_inventory_base": str(self.target_inventory_base),
                        "max_stress_loss_quote": str(self.max_stress_loss_quote), "max_age_ms": self.max_age_ms}:
                    raise ValueError("SPOT_RISK_POLICY_CHANGED")
                now, observed = self.utc_clock_ms(), self.stress_observation()
                fills = self.attributor.verified_fills()
                last = state["checked_at_ms"]
                if (type(now) is not int or now <= 0 or last is not None and (type(last) is not int or now < last)
                        or fills is None or any(fill.fill_at_ms > now for fill in fills)
                        or not isinstance(observed, SpotStressObservation)
                        or observed.source_kind != "independent_market"
                        or type(observed.value_at_ms) is not int or observed.value_at_ms <= 0
                        or type(observed.observed_at_ms) is not int
                        or not observed.value_at_ms <= observed.observed_at_ms <= now
                        or now - observed.value_at_ms > self.max_age_ms
                        or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in (
                            observed.exit_depth_base, observed.collateral_quote,
                            observed.maker_fee_rate, observed.exit_fee_rate))):
                    raise ValueError("SPOT_RISK_UNAVAILABLE")
                self.journal.commit({"checked_at_ms": now})
                pending = preview.pending_orders()
                if candidate is not None:
                    risk_increasing = (preview.projected_inventory_after_buys + candidate.quantity_base
                                       > self.target_inventory_base if candidate.side == "BUY" else
                                       preview.projected_inventory_after_sells - candidate.quantity_base
                                       < self.target_inventory_base)
                    recent = sum((fill.quantity_base for fill in fills if fill.side == candidate.side
                                  and 0 <= now - fill.fill_at_ms < self.window_ms), Decimal("0"))
                    if (risk_increasing and recent + preview.unresolved_quantity_base(candidate.side)
                            + candidate.quantity_base > self.max_filled_base):
                        self.reason_code = "ROLLING_FILL_CAPACITY_EXHAUSTED"
                        return self.reason_code
                    pending += ((candidate.side, candidate.quantity_base, candidate.limit_price_usdt),)
                buys = sum((qty for side, qty, _ in pending if side == "BUY"), Decimal("0"))
                sells = sum((qty for side, qty, _ in pending if side == "SELL"), Decimal("0"))
                if preview.life_balance + buys > observed.exit_depth_base:
                    self.reason_code = "STRESS_EXIT_DEPTH_UNAVAILABLE"
                    return self.reason_code
                buy_price = max((price for side, _, price in pending if side == "BUY"),
                                default=observed.reference_usdt)
                sell_price = min((price for side, _, price in pending if side == "SELL"),
                                 default=observed.reference_usdt)
                fees = (max(buys * buy_price, sells * sell_price) * observed.maker_fee_rate
                        + (preview.life_balance + buys) * observed.stressed_exit_usdt * observed.exit_fee_rate)
                decision = evaluate_stress(StressInputs(
                    buys, sells, preview.life_balance, observed.reference_usdt,
                    observed.stressed_exit_usdt, observed.stressed_high_usdt, buy_price, sell_price,
                    fees, Decimal("0"), False, False,
                    min(observed.collateral_quote, preview.usdt_balance), self.max_stress_loss_quote))
                self.reason_code = decision.reason_code
                return None if decision.allowed else decision.reason_code
        except Exception:
            self.reason_code = "SPOT_RISK_UNAVAILABLE"
            return self.reason_code
