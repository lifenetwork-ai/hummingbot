"""O.5 telemetry carries units, quality and replayable bounded schemas."""

import json
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.telemetry import (
    METRIC_UNITS,
    Metric,
    TelemetryRecorder,
    liquidity_availability,
    risk_latency,
)


def test_missing_metrics_are_null_and_unknown_payload_keys_are_rejected(tmp_path):
    log = TelemetryRecorder(tmp_path / "events.jsonl", clock_ms=lambda: 100,
                            synthetic=True, max_records=100, create=True)
    log.record(stage="PERMISSION", allowed=False, reason_code="BOOK_STALE",
               session_id="session", epoch=1, config_version=1,
               gates={"market": False}, metrics={})
    row = log.records()[0]
    assert set(row["metrics"]) == set(METRIC_UNITS)
    assert row["metrics"]["net_pnl_quote"] == {
        "value": None, "unit": "USDT", "quality": "missing", "reason_code": "NOT_OBSERVED"}
    with pytest.raises(ValueError):
        log.record(stage="PERMISSION", allowed=True, reason_code="READY", metrics={
            "api_secret": Metric(Decimal("1"), "USDT", "synthetic", "TEST")})
    assert "api_secret" not in log.path.read_text()


@pytest.mark.parametrize("metric", [
    (Decimal("NaN"), "USDT", "synthetic"),
    (Decimal("1"), "USDT", "missing"),
    (None, "USDT", "verified"),
    (Decimal("1"), "USDT", "invented"),
])
def test_invalid_quality_or_nonfinite_values_cannot_be_serialized(metric):
    with pytest.raises(ValueError):
        Metric(*metric, "TEST")


def test_log_roundtrip_stale_writer_and_truncation_fail_closed(tmp_path):
    clock = [100]
    path = tmp_path / "events.jsonl"
    log = TelemetryRecorder(path, clock_ms=lambda: clock[0], synthetic=True, max_records=100, create=True)
    log.record(stage="RISK_EVENT", allowed=False, reason_code="LATENCY_EXCEEDED", event_id="risk-1")
    restored = TelemetryRecorder(path, clock_ms=lambda: clock[0], synthetic=True, max_records=100, create=False)
    clock[0] = 101
    restored.record(stage="PERMISSION", allowed=False, reason_code="LATENCY_EXCEEDED", event_id="risk-1")
    with pytest.raises(ValueError):
        log.record(stage="PERMISSION", allowed=True, reason_code="READY")
    assert not log.healthy
    with path.open("a") as handle:
        handle.write('{"schema_version":')
    with pytest.raises(ValueError):
        TelemetryRecorder(path, clock_ms=lambda: 102, synthetic=True, max_records=100, create=False)


def test_latency_is_event_to_block_request_and_account_proof_not_cancel_ack():
    budgets = {"block_ms": 5, "cancel_request_ms": 10, "confirmation_ms": 50}
    result = risk_latency(100, block_ms=103, cancel_request_ms=109, confirmed_ms=None,
                          budgets=budgets, synthetic=True)
    assert result["block_ms"]["value"] == "3"
    assert result["cancel_request_ms"]["within_budget"] is True
    assert result["confirmation_ms"]["value"] is None
    assert result["confirmation_ms"]["within_budget"] is None
    assert risk_latency(100, block_ms=103, cancel_request_ms=109, confirmed_ms=160,
                        budgets=budgets, synthetic=True)["confirmation_ms"]["within_budget"] is False
    with pytest.raises(ValueError):
        risk_latency(100, block_ms=99, cancel_request_ms=None, confirmed_ms=None,
                     budgets=budgets, synthetic=True)


def test_availability_uses_full_session_and_eligible_time_and_preserves_unknown():
    intervals = [(0, 10, True, True), (10, 20, False, False), (20, 30, True, False)]
    result = liquidity_availability(intervals, synthetic=True)
    assert result["full_session"].value == Decimal(1) / 3
    assert result["eligible"].value == Decimal("0.5")
    assert liquidity_availability([(0, 10, None, None)], synthetic=True)["eligible"].value is None
    with pytest.raises(ValueError):
        liquidity_availability([(0, 10, True, True), (9, 20, True, True)], synthetic=True)


def test_recorded_values_are_detached_and_schema_has_no_freeform_secrets(tmp_path):
    log = TelemetryRecorder(tmp_path / "events.jsonl", clock_ms=lambda: 100,
                            synthetic=True, max_records=1, create=True)
    log.record(stage="PERMISSION", allowed=True, reason_code="READY",
               metrics={"inventory_base": Metric(Decimal("2"), "LIFE", "synthetic", "TEST")})
    rows = log.records()
    rows[0]["metrics"]["inventory_base"]["value"] = "999"
    assert log.records()[0]["metrics"]["inventory_base"]["value"] == "2"
    with pytest.raises(ValueError):
        log.record(stage="PERMISSION", allowed=True, reason_code="READY")
    assert not log.healthy
    assert len(json.loads(log.path.read_text().splitlines()[0])["policy"]) == 3


def test_capture_does_not_wait_for_flush_disk_io_and_preserves_new_buffered_rows(tmp_path):
    from threading import Event, Thread
    from unittest.mock import patch

    log = TelemetryRecorder(tmp_path / "events.jsonl", clock_ms=lambda: 100,
                            synthetic=True, max_records=100, create=True)
    log.capture(stage="SNAPSHOT", allowed=None, reason_code="FIRST")
    entered, release = Event(), Event()

    def slow_fsync(_):
        entered.set()
        assert release.wait(3)

    with patch("hummingbot.strategy_v2.life_liquidity.telemetry.os.fsync", side_effect=slow_fsync):
        writer = Thread(target=log.flush)
        writer.start()
        try:
            assert entered.wait(3)
            captured = Event()
            callback = Thread(target=lambda: (log.capture(
                stage="CANCEL_REQUEST", allowed=None, reason_code="CANCEL_REQUEST"), captured.set()))
            callback.start()
            assert captured.wait(1), "cancel diagnostics waited for disk flush"
            callback.join(1)
        finally:
            release.set()
            writer.join(3)
    log.flush()
    restored = TelemetryRecorder(log.path, clock_ms=lambda: 100,
                                 synthetic=True, max_records=100, create=False)
    assert [row["reason_code"] for row in restored.records()] == ["FIRST", "CANCEL_REQUEST"]
