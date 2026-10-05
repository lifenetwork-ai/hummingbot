"""Qualified, delayed fill markouts for diagnostic risk cohorts."""

from dataclasses import dataclass
from decimal import Decimal


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


@dataclass(frozen=True)
class MarkoutSummary:
    reason_code: str
    sample_count: int
    mean_markout_quote: Decimal | None


class MarkoutTracker:
    def __init__(self, *, horizons_ms: tuple[int, ...], min_samples: int,
                 size_bucket_edges: tuple[Decimal, ...]):
        if (not horizons_ms or any(not isinstance(value, int) or value <= 0 for value in horizons_ms)
                or len(set(horizons_ms)) != len(horizons_ms)
                or not isinstance(min_samples, int) or min_samples < 1
                or len(size_bucket_edges) != 1 or not _positive(size_bucket_edges[0])):
            raise ValueError("markout requires explicit horizon, sample, and size policy")
        self.horizons_ms = horizons_ms
        self.min_samples = min_samples
        self.size_cutoff = size_bucket_edges[0]
        self._fills: dict[str, tuple[str, Decimal, Decimal, int]] = {}
        self._observations: dict[tuple[str, int], tuple[Decimal, int]] = {}

    def record_fill(self, trade_id: str, side: str, quantity: Decimal,
                    fill_price_usdt: Decimal, *, fill_at_ms: int) -> bool:
        if (not trade_id or side not in ("BUY", "SELL") or not _positive(quantity)
                or not _positive(fill_price_usdt) or not isinstance(fill_at_ms, int)
                or fill_at_ms <= 0):
            raise ValueError("markout fill invalid")
        event = (side, quantity, fill_price_usdt, fill_at_ms)
        if trade_id in self._fills:
            if self._fills[trade_id] != event:
                raise ValueError("markout fill ID conflict")
            return False
        self._fills[trade_id] = event
        return True

    def observe(self, trade_id: str, horizon_ms: int, independent_price_usdt: Decimal | None,
                *, observed_at_ms: int, source_kind: str) -> bool:
        if trade_id not in self._fills or horizon_ms not in self.horizons_ms:
            raise ValueError("markout observation scope invalid")
        _, _, _, fill_at = self._fills[trade_id]
        if (source_kind != "independent_market" or not _positive(independent_price_usdt)
                or not isinstance(observed_at_ms, int)
                or observed_at_ms < fill_at + horizon_ms):
            return False
        key = (trade_id, horizon_ms)
        if key in self._observations:
            if self._observations[key] != (independent_price_usdt, observed_at_ms):
                raise ValueError("markout observation conflict")
            return False
        self._observations[key] = (independent_price_usdt, observed_at_ms)
        return True

    def summary(self, side: str, size_bucket: str, horizon_ms: int, *, now_ms: int) -> MarkoutSummary:
        if side not in ("BUY", "SELL") or size_bucket not in ("small", "large"):
            raise ValueError("markout cohort invalid")
        values = []
        for trade_id, (fill_side, quantity, price, _) in self._fills.items():
            bucket = "small" if quantity <= self.size_cutoff else "large"
            observation = self._observations.get((trade_id, horizon_ms))
            if fill_side != side or bucket != size_bucket or observation is None:
                continue
            observed_price, observed_at = observation
            if observed_at <= now_ms:
                sign = Decimal("1") if side == "BUY" else Decimal("-1")
                values.append(sign * (observed_price - price) * quantity)
        if len(values) < self.min_samples:
            return MarkoutSummary("INSUFFICIENT_SAMPLES", len(values), None)
        return MarkoutSummary("MARKOUT_READY", len(values),
                              sum(values, Decimal("0")) / len(values))
