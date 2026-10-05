"""Benchmark qualification needs separate, time-ordered LIFE evaluation data."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.reference_evaluation import (
    EvaluationPoint,
    FrozenReferenceModel,
    evaluate_benchmark,
)

MODEL = FrozenReferenceModel(model_version="relative-return-v1", calibration_dataset_id="cal-1",
                             calibrated_until_ms=1000, min_evaluation_samples=3)


def points():
    return (
        EvaluationPoint(1500, 2000, Decimal("1.01"), Decimal("1.00"), Decimal("1.01")),
        EvaluationPoint(2500, 3000, Decimal("1.02"), Decimal("1.00"), Decimal("1.02")),
        EvaluationPoint(3500, 4000, Decimal("1.03"), Decimal("1.00"), Decimal("1.03")),
    )


def test_separate_shadow_dataset_compares_benchmark_against_market_baseline():
    result = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=points(),
                                execution_mode="shadow")
    assert result.reason_code == "EVALUATION_COMPLETE"
    assert result.benchmark_mae_bps == Decimal("0")
    assert result.market_mae_bps > 0
    assert result.benchmark_outperformed_baseline is True
    assert result.sample_count == 3


def test_missing_life_history_and_too_few_samples_are_insufficient_evidence():
    missing_life = (
        EvaluationPoint(1500, 2000, None, Decimal("1"), Decimal("1.01")),
        EvaluationPoint(2500, 3000, None, Decimal("1"), Decimal("1.02")),
    )
    result = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=missing_life,
                                execution_mode="shadow")
    sparse = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=points()[:2],
                                execution_mode="simulation")
    assert result.reason_code == "INSUFFICIENT_LIFE_EVIDENCE"
    assert sparse.reason_code == "INSUFFICIENT_LIFE_EVIDENCE"
    assert result.benchmark_outperformed_baseline is None


def test_calibration_overlap_and_nonresearch_mode_cannot_certify_benchmark():
    overlap = evaluate_benchmark(MODEL, dataset_id="cal-1", observations=points(),
                                 execution_mode="shadow")
    future_leak = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=(
        EvaluationPoint(998, 999, Decimal("1"), Decimal("1"), Decimal("1")), *points()),
        execution_mode="shadow")
    live = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=points(),
                              execution_mode="live")
    assert overlap.reason_code == "EVALUATION_DATASET_NOT_SEPARATE"
    assert future_leak.reason_code == "EVALUATION_TIME_LEAKAGE"
    assert live.reason_code == "RESEARCH_MODE_REQUIRED"


def test_unrepresentative_or_out_of_order_data_is_not_positive_evidence():
    out_of_order = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=points()[::-1],
                                      execution_mode="shadow")
    unqualified = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=tuple(
        EvaluationPoint(point.prediction_at_ms, point.timestamp_ms,
                        point.life_price_usdt, point.market_prediction_usdt,
                        point.benchmark_prediction_usdt, qualified=False) for point in points()),
        execution_mode="shadow")
    assert out_of_order.reason_code == "EVALUATION_TIME_ORDER_INVALID"
    assert unqualified.reason_code == "INSUFFICIENT_LIFE_EVIDENCE"


def test_prediction_cannot_use_outcome_timestamp_or_future_observation():
    lookahead = (EvaluationPoint(2000, 2000, Decimal("1"), Decimal("1"), Decimal("1")),
                 *points()[1:])
    result = evaluate_benchmark(MODEL, dataset_id="eval-1", observations=lookahead,
                                execution_mode="shadow")
    assert result.reason_code == "PREDICTION_LOOKAHEAD"
