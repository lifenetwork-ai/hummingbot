"""Offline benchmark comparison on a disjoint, time-ordered LIFE dataset.

This measures valuation error, not trading profit or live release eligibility.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FrozenReferenceModel:
    model_version: str
    calibration_dataset_id: str
    calibrated_until_ms: int
    min_evaluation_samples: int

    def __post_init__(self):
        if (not self.model_version or not self.calibration_dataset_id
                or not isinstance(self.calibrated_until_ms, int) or self.calibrated_until_ms <= 0
                or not isinstance(self.min_evaluation_samples, int)
                or self.min_evaluation_samples < 2):
            raise ValueError("frozen model needs a valid calibration boundary")


@dataclass(frozen=True)
class EvaluationPoint:
    prediction_at_ms: int
    timestamp_ms: int
    life_price_usdt: Decimal | None
    market_prediction_usdt: Decimal | None
    benchmark_prediction_usdt: Decimal | None
    qualified: bool = True


@dataclass(frozen=True)
class BenchmarkEvaluation:
    reason_code: str
    sample_count: int
    market_mae_bps: Decimal | None
    benchmark_mae_bps: Decimal | None
    benchmark_outperformed_baseline: bool | None
    model_version: str
    evaluation_dataset_id: str


def _positive(value: Decimal | None) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def evaluate_benchmark(model: FrozenReferenceModel, *, dataset_id: str,
                       observations: tuple[EvaluationPoint, ...],
                       execution_mode: str) -> BenchmarkEvaluation:
    def unavailable(reason: str) -> BenchmarkEvaluation:
        return BenchmarkEvaluation(reason, 0, None, None, None, model.model_version, dataset_id)

    if execution_mode not in ("shadow", "simulation"):
        return unavailable("RESEARCH_MODE_REQUIRED")
    if not dataset_id or dataset_id == model.calibration_dataset_id:
        return unavailable("EVALUATION_DATASET_NOT_SEPARATE")
    if any(not isinstance(point.timestamp_ms, int)
           or not isinstance(point.prediction_at_ms, int)
           or point.prediction_at_ms <= model.calibrated_until_ms for point in observations):
        return unavailable("EVALUATION_TIME_LEAKAGE")
    if any(point.timestamp_ms <= point.prediction_at_ms for point in observations):
        return unavailable("PREDICTION_LOOKAHEAD")
    if any(left.timestamp_ms >= right.timestamp_ms for left, right in zip(observations, observations[1:])):
        return unavailable("EVALUATION_TIME_ORDER_INVALID")
    qualified = [point for point in observations if point.qualified and _positive(point.life_price_usdt)
                 and _positive(point.market_prediction_usdt)
                 and _positive(point.benchmark_prediction_usdt)]
    if len(qualified) < model.min_evaluation_samples:
        return unavailable("INSUFFICIENT_LIFE_EVIDENCE")
    count = len(qualified)
    market_error = sum((abs(point.market_prediction_usdt - point.life_price_usdt)
                        / point.life_price_usdt * Decimal("10000") for point in qualified), Decimal("0")) / count
    benchmark_error = sum((abs(point.benchmark_prediction_usdt - point.life_price_usdt)
                           / point.life_price_usdt * Decimal("10000") for point in qualified), Decimal("0")) / count
    return BenchmarkEvaluation("EVALUATION_COMPLETE", count, market_error, benchmark_error,
                               benchmark_error < market_error, model.model_version, dataset_id)
