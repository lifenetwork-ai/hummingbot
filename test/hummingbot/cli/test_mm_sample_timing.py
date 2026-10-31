"""Causal diagnostics must expose collection intervals and heartbeat races."""
from test.e2e.offline import load_trace
from types import SimpleNamespace

from scripts.analyze_mm_load_trace import cpu_observation_bounds


def test_cpu_sample_times_actual_read_and_bounds_slow_counter_collection(monkeypatch):
    clock = SimpleNamespace(now=5_000_000_000)
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: clock.now)
    monkeypatch.setattr(load_trace.time, 'time', lambda: clock.now / 1e9 + 100)
    monkeypatch.setattr(load_trace.time, 'process_time_ns', lambda: 77)
    monkeypatch.setattr(load_trace.time, 'thread_time_ns', lambda: 11)

    class CPUFile:
        def read_text(self):
            clock.now += 800_000_000
            return 'usage_usec 900\nnr_throttled 2\n'

    row = load_trace.timed_cpu_sample(CPUFile(), process_role='engine', previous_sample_ns=4_000_000_000)
    assert row['sample_started_ns'] == row['at_ns'] == 5_000_000_000
    assert row['sample_ended_ns'] == 5_800_000_000
    assert row['collection_ms'] == 800 and row['sample_gap_ms'] == 1000
    assert row['cgroup_cpu']['usage_usec'] == 900 and row['process_cpu_ns'] == 77


def test_heartbeat_race_is_rejected_instead_of_becoming_negative_or_false_stall(monkeypatch):
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: 2_000_000_000)
    values = iter((1_000_000_000, 2_001_000_000))
    row = load_trace.sample_heartbeat(lambda: next(values))
    assert not row['consistent'] and row['lag_ms'] is None
    row = load_trace.sample_heartbeat(lambda: 1_000_000_000)
    assert row['consistent'] and row['lag_ms'] == 950


def test_cpu_delta_uses_collection_bounds_and_does_not_guess_old_sample_timing():
    before = dict(at_ns=100, sample_started_ns=100, sample_ended_ns=200,
                  cgroup_cpu={'usage_usec': 10, 'nr_throttled': 0})
    after = dict(at_ns=400, sample_started_ns=400, sample_ended_ns=600,
                 cgroup_cpu={'usage_usec': 110, 'nr_throttled': 1})
    result = cpu_observation_bounds([before, after])
    assert result['elapsed_lower_ns'] == 200 and result['elapsed_upper_ns'] == 500
    assert result['cgroup_cpu_delta'] == {'usage_usec': 100, 'nr_throttled': 1}
    legacy = cpu_observation_bounds([{'at_ns': 100, 'cgroup_cpu': {'usage_usec': 10}},
                                    {'at_ns': 400, 'cgroup_cpu': {'usage_usec': 110}}])
    assert legacy['elapsed_lower_ns'] is None and legacy['elapsed_upper_ns'] is None
    assert legacy['timing_status'] == 'legacy_unbounded'


def test_cpu_bounds_sort_streams_and_do_not_invent_a_reset_from_overlapping_reads():
    before = dict(at_ns=100, sample_started_ns=100, sample_ended_ns=300, cgroup_cpu={'usage_usec': 110})
    overlap = dict(at_ns=200, sample_started_ns=200, sample_ended_ns=400, cgroup_cpu={'usage_usec': 100})
    assert cpu_observation_bounds([overlap, before])['timing_status'] == 'counter_order_unknown'
    later = dict(at_ns=500, sample_started_ns=500, sample_ended_ns=600, cgroup_cpu={'usage_usec': 200})
    result = cpu_observation_bounds([later, before])
    assert result['cgroup_cpu_delta']['usage_usec'] == 90 and result['elapsed_upper_ns'] == 500
    invalid = {**later, 'sample_ended_ns': 400}
    assert cpu_observation_bounds([before, invalid])['timing_status'] == 'invalid_interval'


def test_future_heartbeat_cannot_be_misreported_as_a_valid_stall(monkeypatch):
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: 100)
    row = load_trace.sample_heartbeat(lambda: 101)
    assert not row['consistent'] and row['lag_ms'] is None
