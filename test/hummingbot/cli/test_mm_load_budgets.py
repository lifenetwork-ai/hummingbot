from scripts.mm_load_budgets import soak_violations, violations


def healthy():
    return dict(duration_seconds=900, requested_reads=18000, completed_reads=18000, read_p95_ms=450, read_p99_ms=950,
                unexpected_errors=0, market_updates_per_second=100, fills_per_second=1, peak_rss_bytes=1_000_000_000,
                command_seconds={"start": 10, "switch": 10, "stop": 3}, backlog_drain_seconds=3,
                command_accept_p95_ms=100, command_accept_p99_ms=200, queued_reads_at_generation_end=0,
                runtime={"runtime_phase": "running"})


def test_healthy_measured_load_meets_declared_budgets():
    assert violations(healthy()) == []


def test_failures_cannot_be_hidden_by_low_achieved_rate_or_missing_measurements():
    result = {**healthy(), "completed_reads": 17000, "market_updates_per_second": 80, "fills_per_second": .8,
              "read_p95_ms": 501, "read_p99_ms": 1001, "unexpected_errors": 1,
              "peak_rss_bytes": 1_700_000_000, "backlog_drain_seconds": 11,
              "command_seconds": {"start": 16, "switch": 16, "stop": 6}}
    assert set(violations(result)) == {"read_rate", "market_rate", "fill_rate", "read_p95", "read_p99", "errors", "rss", "backlog", "command_start", "command_switch", "command_stop"}
    assert "read_p95" in violations({**healthy(), "read_p95_ms": None})
    assert "generator_backlog" in violations({**healthy(), "queued_reads_at_generation_end": 11})
    assert "runtime_halt" in violations({**healthy(), "runtime": {"runtime_phase": "halted"}})


def test_soak_requires_late_samples_and_rejects_sustained_resource_growth():
    sample = dict(memory={"VmRSS": 300_000_000}, latest={"book_backlog": 1}, distributions={"pool_checkout_ms": {"p95": 1}})
    assert soak_violations([sample] * 59) == []
    assert soak_violations([sample] * 2) == ["missing_minute_samples"]
    grown = dict(memory={"VmRSS": 500_000_000}, latest={"book_backlog": 100}, distributions={"pool_checkout_ms": {"p95": 100}})
    assert set(soak_violations([sample] * 50 + [grown] * 9)) == {"rss_growth", "book_queue_growth", "pool_wait_growth"}


def test_auxiliary_history_reads_add_load_without_weakening_original_budgets():
    extra = dict(requested_reads=1800, completed_reads=1800, read_p95_ms=450, read_p99_ms=950)
    assert violations({**healthy(), 'auxiliary': extra}) == []
    assert 'auxiliary_reads' in violations({**healthy(), 'auxiliary': {**extra, 'completed_reads': 1799}})
    assert 'auxiliary_read_p99' in violations({**healthy(), 'auxiliary': {**extra, 'read_p99_ms': 1001}})
    assert 'read_p99' in violations({**healthy(), 'read_p99_ms': 1001, 'auxiliary': extra})
