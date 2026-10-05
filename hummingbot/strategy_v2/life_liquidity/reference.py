"""Qualified LIFE market reference and a shadow-only relative-return model.

The benchmark signal is dimensionless: normalized source returns multiply an
independently qualified LIFE anchor. No source token's absolute price is a LIFE
valuation, and this module never creates fills or order intents.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


@dataclass(frozen=True)
class ReferencePolicy:
    max_age_ms: int
    min_life_depth_base: Decimal
    min_source_depth_quote: Decimal
    max_deviation_bps: Decimal
    max_influence: Decimal
    min_correlation: Decimal
    min_correlation_samples: int

    def __post_init__(self):
        if (not isinstance(self.max_age_ms, int) or isinstance(self.max_age_ms, bool)
                or self.max_age_ms <= 0 or not _positive(self.min_life_depth_base)
                or not _positive(self.min_source_depth_quote)
                or not _positive(self.max_deviation_bps)
                or not isinstance(self.max_influence, Decimal)
                or not self.max_influence.is_finite()
                or not Decimal("0") <= self.max_influence <= Decimal("1")
                or not isinstance(self.min_correlation, Decimal)
                or not self.min_correlation.is_finite()
                or not Decimal("-1") <= self.min_correlation <= Decimal("1")
                or not isinstance(self.min_correlation_samples, int)
                or self.min_correlation_samples < 3):
            raise ValueError("reference policy requires explicit finite offline thresholds")


@dataclass(frozen=True)
class LifeMarketEvidence:
    mid_usdt: Decimal
    bid_depth_base: Decimal
    ask_depth_base: Decimal
    own_depth_separated: bool
    exchange_timestamp_ms: int
    source: str


@dataclass(frozen=True)
class BenchmarkComponent:
    source_id: str
    weight: Decimal
    current_price: Decimal
    anchor_price_usdt: Decimal
    quote_currency: str
    quote_to_usdt: Decimal | None
    independent_depth_quote: Decimal
    exchange_timestamp_ms: int

    def with_conversion(self, quote_to_usdt: Decimal | None) -> "BenchmarkComponent":
        return replace(self, quote_to_usdt=quote_to_usdt)


@dataclass(frozen=True)
class CorrelationEvidence:
    value: Decimal
    sample_count: int
    observed_until_ms: int


@dataclass(frozen=True)
class ReferenceDecision:
    price_usdt: Decimal | None
    benchmark_implied_usdt: Decimal | None
    influence_applied: Decimal
    reason_code: str
    quality: str
    model_version: str
    synthetic_fills: int = 0
    synthetic_volume_base: Decimal = Decimal("0")
    synthetic_candles: int = 0


class ReferenceEngine:
    def __init__(self, policy: ReferencePolicy, *, life_anchor_usdt: Decimal, model_version: str,
                 life_source_id: str, source_anchors_usdt: Mapping[str, Decimal]):
        if not _positive(life_anchor_usdt) or not model_version or not life_source_id:
            raise ValueError("qualified LIFE anchor and model version are required")
        if (not isinstance(source_anchors_usdt, Mapping)
                or any(not isinstance(key, str) or not key or not _positive(value)
                       for key, value in source_anchors_usdt.items())):
            raise ValueError("source anchors require qualified USDT prices")
        self.policy = policy
        self.life_anchor_usdt = life_anchor_usdt
        self.life_source_id = life_source_id
        self.model_version = model_version
        self.source_anchors_usdt = MappingProxyType(dict(source_anchors_usdt))

    def _decision(self, price: Decimal | None, reason: str, *,
                  benchmark: Decimal | None = None, influence: Decimal = Decimal("0"),
                  quality: str = "QUALIFIED") -> ReferenceDecision:
        return ReferenceDecision(price, benchmark, influence, reason,
                                 quality if price is not None else "UNAVAILABLE", self.model_version)

    def _market_price(self, market: LifeMarketEvidence | None, now_ms: int) -> tuple[Decimal | None, str]:
        if market is None:
            return None, "LIFE_MARKET_UNAVAILABLE"
        if market.source != self.life_source_id:
            return None, "LIFE_SOURCE_MISMATCH"
        if not market.own_depth_separated:
            return None, "OWN_DEPTH_UNSEPARABLE"
        if not _positive(market.mid_usdt):
            return None, "LIFE_PRICE_INVALID"
        if (not _positive(market.bid_depth_base) or not _positive(market.ask_depth_base)
                or min(market.bid_depth_base, market.ask_depth_base) < self.policy.min_life_depth_base):
            return None, "LIFE_DEPTH_INSUFFICIENT"
        if (not isinstance(market.exchange_timestamp_ms, int) or market.exchange_timestamp_ms <= 0
                or not isinstance(now_ms, int) or now_ms < market.exchange_timestamp_ms
                or now_ms - market.exchange_timestamp_ms > self.policy.max_age_ms):
            return None, "LIFE_REFERENCE_STALE"
        return market.mid_usdt, "LIFE_MARKET_READY"

    def _benchmark_return(self, components: tuple[BenchmarkComponent, ...],
                          now_ms: int) -> tuple[Decimal | None, str]:
        if not components or any(not _positive(component.weight) for component in components):
            return None, "BENCHMARK_MODEL_INVALID"
        if sum((component.weight for component in components), Decimal("0")) != Decimal("1"):
            return None, "BENCHMARK_MODEL_INVALID"
        seen = set()
        weighted_return = Decimal("0")
        for component in components:
            if not component.source_id or component.source_id in seen:
                return None, "BENCHMARK_MODEL_INVALID"
            if component.source_id == self.life_source_id:
                return None, "BENCHMARK_SELF_REFERENCE"
            seen.add(component.source_id)
            if self.source_anchors_usdt.get(component.source_id) != component.anchor_price_usdt:
                return None, "BENCHMARK_ANCHOR_MISMATCH"
            if (not isinstance(component.exchange_timestamp_ms, int)
                    or component.exchange_timestamp_ms <= 0 or now_ms < component.exchange_timestamp_ms
                    or now_ms - component.exchange_timestamp_ms > self.policy.max_age_ms):
                return None, "BENCHMARK_STALE"
            if (not _positive(component.independent_depth_quote)
                    or component.independent_depth_quote < self.policy.min_source_depth_quote):
                return None, "BENCHMARK_LIQUIDITY_INSUFFICIENT"
            if not _positive(component.current_price) or not _positive(component.anchor_price_usdt):
                return None, "BENCHMARK_PRICE_INVALID"
            if component.quote_currency == "USDT":
                conversion = Decimal("1")
            elif not _positive(component.quote_to_usdt):
                return None, "BENCHMARK_CONVERSION_UNAVAILABLE"
            else:
                conversion = component.quote_to_usdt
            current_usdt = component.current_price * conversion
            weighted_return += component.weight * current_usdt / component.anchor_price_usdt
        return weighted_return, "BENCHMARK_READY"

    def evaluate(self, mode: str, *, market: LifeMarketEvidence | None,
                 exchange_now_ms: int, components: tuple[BenchmarkComponent, ...] = (),
                 influence: Decimal = Decimal("0"), correlation: CorrelationEvidence | None = None,
                 execution_mode: str | None = None) -> ReferenceDecision:
        market_price, market_reason = self._market_price(market, exchange_now_ms)
        if mode == "bootstrap_simulation":
            if execution_mode != "simulation":
                return self._decision(None, "HYPOTHETICAL_ANCHOR_FORBIDDEN")
            if market_price is not None:
                return self._decision(market_price, "LIFE_MARKET_READY")
            return self._decision(self.life_anchor_usdt, "HYPOTHETICAL_SIMULATION_ANCHOR",
                                  quality="HYPOTHETICAL")
        if mode not in ("market", "bounded_benchmark"):
            return self._decision(None, "REFERENCE_MODE_INVALID")
        if mode == "bounded_benchmark" and execution_mode not in ("simulation", "shadow"):
            return self._decision(None, "BENCHMARK_RESEARCH_ONLY")
        if market_price is None:
            return self._decision(None, market_reason)
        if mode == "market":
            return self._decision(market_price, "LIFE_MARKET_READY")
        if (not isinstance(influence, Decimal) or not influence.is_finite()
                or not 0 <= influence <= self.policy.max_influence):
            return self._decision(None, "BENCHMARK_INFLUENCE_INVALID")
        if influence == 0:
            return self._decision(market_price, "ZERO_BENCHMARK_INFLUENCE")
        benchmark_return, benchmark_reason = self._benchmark_return(components, exchange_now_ms)
        if benchmark_return is None:
            if benchmark_reason in ("BENCHMARK_ANCHOR_MISMATCH", "BENCHMARK_CONVERSION_UNAVAILABLE",
                                    "BENCHMARK_SELF_REFERENCE"):
                return self._decision(None, benchmark_reason)
            return self._decision(market_price, benchmark_reason)
        if (not isinstance(correlation, CorrelationEvidence)
                or not isinstance(correlation.value, Decimal) or not correlation.value.is_finite()
                or not Decimal("-1") <= correlation.value <= Decimal("1")
                or not isinstance(correlation.sample_count, int)
                or correlation.sample_count < self.policy.min_correlation_samples):
            return self._decision(market_price, "BENCHMARK_CORRELATION_UNAVAILABLE")
        if (not isinstance(correlation.observed_until_ms, int)
                or correlation.observed_until_ms <= 0
                or correlation.observed_until_ms > exchange_now_ms
                or exchange_now_ms - correlation.observed_until_ms > self.policy.max_age_ms):
            return self._decision(market_price, "BENCHMARK_CORRELATION_STALE")
        if correlation.value < self.policy.min_correlation:
            return self._decision(market_price, "BENCHMARK_CORRELATION_FAILED")
        if market_price < self.life_anchor_usdt and benchmark_return > 1:
            return self._decision(market_price, "LIFE_BENCHMARK_DIVERGENCE")
        implied = self.life_anchor_usdt * benchmark_return
        candidate = market_price * (Decimal("1") - influence) + implied * influence
        deviation_bps = abs(candidate - market_price) / market_price * Decimal("10000")
        if deviation_bps > self.policy.max_deviation_bps:
            return self._decision(None, "BENCHMARK_DEVIATION_EXCEEDED", benchmark=implied)
        return self._decision(candidate, "BOUNDED_BENCHMARK_READY", benchmark=implied,
                              influence=influence)
