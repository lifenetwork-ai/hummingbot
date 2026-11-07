"""Incident capture preserves bounded context without changing health decisions."""
import json
import signal
import threading
from test.e2e.offline.load_incidents import IncidentBuffer, ProbeIncidentPublisher, ProbeStackSampler, capture_limit
from types import SimpleNamespace

import pytest


def test_stack_signal_requires_established_child_and_three_failed_probes():
    signals = []
    child = SimpleNamespace(send_signal=signals.append)
    sampler = ProbeStackSampler(max_dumps=1)
    for _ in range(5):
        assert sampler.observe('engine', False, child) is None
    sampler.observe('engine', True, child)
    assert sampler.observe('engine', False, child) is None
    assert sampler.observe('engine', False, child) is None
    assert sampler.observe('engine', False, child)['failed_probes'] == 3
    for _ in range(10):
        sampler.observe('engine', False, child)
    sampler.observe('engine', True, child)
    for _ in range(3):
        sampler.observe('engine', False, child)
    assert signals == [signal.SIGUSR2]


def test_replacement_child_must_become_healthy_before_stack_signal():
    old_signals, new_signals = [], []
    old = SimpleNamespace(send_signal=old_signals.append)
    new = SimpleNamespace(send_signal=new_signals.append)
    sampler = ProbeStackSampler()
    sampler.observe('engine', True, old)
    sampler.observe('engine', False, old)
    for _ in range(5):
        sampler.observe('engine', False, new)
    assert not old_signals and not new_signals
    sampler.observe('engine', True, new)
    for _ in range(3):
        sampler.observe('engine', False, new)
    assert new_signals == [signal.SIGUSR2]


@pytest.mark.parametrize('value', [1, 32, 128, '1', '32', '128'])
def test_capture_limit_accepts_only_bounded_explicit_counts(value):
    assert capture_limit(value) == int(value)


@pytest.mark.parametrize('value', [0, 129, -1, True, 1.5, '', '1.5', 'bad'])
def test_capture_limit_rejects_invalid_or_unbounded_configuration(value):
    with pytest.raises(ValueError):
        capture_limit(value)


def test_explicit_long_run_limit_preserves_incident_33_with_same_ring_bounds():
    rows = []
    capture = IncidentBuffer(rows.append, max_incidents=capture_limit('128'), tail_seconds=1)
    for n in range(33):
        capture.trigger({'kind': 'probe_failure', 'at_ns': n * 2_000_000_000})
    assert len(rows) == 33
    assert capture.summary()['incidents'] == 33
    assert capture.summary()['suppressed_incidents'] == 0
    assert capture.summary()['max_records'] == 256
    assert capture.summary()['max_bytes'] == 512 * 1024


def test_incident_flushes_preceding_records_and_keeps_recovery_tail():
    rows = []
    capture = IncidentBuffer(rows.append, history_seconds=10, tail_seconds=2)
    capture.emit({'kind': 'sample', 'at_ns': 1_000_000_000})
    capture.trigger({'kind': 'probe_failure', 'at_ns': 2_000_000_000, 'role': 'engine'})
    capture.emit({'kind': 'sql', 'at_ns': 3_000_000_000})
    capture.trigger({'kind': 'probe_recovered', 'at_ns': 4_000_000_000, 'role': 'engine'})
    capture.emit({'kind': 'sample', 'at_ns': 5_000_000_000})
    capture.emit({'kind': 'outside', 'at_ns': 7_000_000_000})
    assert [row['kind'] for row in rows] == ['sample', 'probe_failure', 'sql', 'probe_recovered', 'sample']
    assert capture.summary()['incidents'] == 1


def test_ring_is_bounded_by_bytes_records_and_age_and_copies_values():
    rows = []
    capture = IncidentBuffer(rows.append, max_records=3, max_bytes=300, history_seconds=2)
    value = {'kind': 'sample', 'at_ns': 1_000_000_000, 'nested': {'value': 'old'}}
    capture.emit(value)
    value['nested']['value'] = 'changed'
    capture.trigger({'kind': 'probe_failure', 'at_ns': 1_500_000_000})
    assert rows[0]['nested']['value'] == 'old'
    for n in range(20):
        capture.emit({'kind': 'sample', 'at_ns': (n + 2) * 1_000_000_000, 'value': 'x' * 40})
    summary = capture.summary()
    assert summary['buffered_records'] <= 3 and summary['buffered_bytes'] <= 300
    assert summary['evicted_records'] > 0
    capture.trigger({'kind': 'probe_failure', 'at_ns': 30_000_000_000})
    assert rows[-1]['kind'] == 'probe_failure'  # Old history has aged out.
    assert rows[-2]['at_ns'] < 28_000_000_000


def test_oversize_and_incident_count_limits_are_explicit():
    rows = []
    capture = IncidentBuffer(rows.append, max_bytes=100, max_incidents=1, tail_seconds=1)
    capture.emit({'kind': 'oversize', 'at_ns': 0, 'value': 'x' * 200})
    capture.trigger({'kind': 'probe_failure', 'at_ns': 1_000_000_000})
    capture.trigger({'kind': 'probe_failure', 'at_ns': 3_000_000_000})
    capture.emit({'kind': 'ignored', 'at_ns': 3_500_000_000})
    assert [row['kind'] for row in rows] == ['probe_failure']
    assert capture.summary()['oversize_records'] == 1
    assert capture.summary()['suppressed_incidents'] == 1


def test_supervisor_publishes_transitions_after_first_healthy_probe_only(tmp_path):
    path = tmp_path / 'trigger.json'
    publisher = ProbeIncidentPublisher(path, limit=3)
    publisher.observe('engine', False, {'reason': 'timeout'})
    assert not path.exists()  # Startup is not an established-service failure.
    publisher.observe('engine', True, {'reason': 'healthy'})
    publisher.observe('engine', False, {'reason': 'timeout', 'elapsed_ms': 301, 'secret': 'never-write'})
    publisher.observe('engine', False, {'reason': 'timeout'})
    publisher.observe('api', True, {})
    publisher.observe('api', False, {'reason': 'http_status', 'status': 503})
    publisher.observe('engine', True, {'reason': 'healthy', 'elapsed_ms': 2})
    body = json.loads(path.read_text())
    assert [event['kind'] for event in body['events']] == ['probe_failure', 'probe_failure', 'probe_recovered']
    assert [event['sequence'] for event in body['events']] == [1, 2, 3]
    assert 'secret' not in path.read_text()
    publisher.observe('api', True, {})
    assert len(json.loads(path.read_text())['events']) == 3


def test_loop_stall_and_probe_failure_share_one_incident_without_duplicate_history():
    rows = []
    capture = IncidentBuffer(rows.append)
    capture.emit({'kind': 'sample', 'at_ns': 0})
    capture.trigger({'kind': 'loop_stall_trigger', 'at_ns': 1_000_000_000})
    capture.trigger({'kind': 'probe_failure', 'at_ns': 2_000_000_000})
    assert [row['kind'] for row in rows] == ['sample', 'loop_stall_trigger', 'probe_failure']
    assert capture.summary()['incidents'] == 1


@pytest.mark.parametrize('path,blocked_stage', [
    ('history', 'decode'), ('history', 'sink'), ('tail', 'decode'), ('tail', 'sink'), ('marker', 'sink'),
])
def test_slow_incident_output_does_not_hold_the_producer_lock(monkeypatch, path, blocked_stage):
    """A watchdog output stall must not make an engine producer wait on its lock."""
    entered, release, progressed = threading.Event(), threading.Event(), threading.Event()
    rows, errors = [], []
    original_loads = json.loads

    def block(record, stage):
        if record['kind'] == 'blocked' and stage == blocked_stage:
            entered.set()
            assert release.wait(5), 'Test did not release the output barrier'
        return record

    def sink(record):
        rows.append(block(record, 'sink'))

    capture = IncidentBuffer(sink)
    if path == 'history':
        capture.emit({'kind': 'blocked', 'at_ns': 0})
    elif path == 'tail':
        capture.trigger({'kind': 'start', 'at_ns': 0})
    monkeypatch.setattr(json, 'loads', lambda line: block(original_loads(line), 'decode'))

    def output():
        if path == 'history':
            capture.trigger({'kind': 'probe_failure', 'at_ns': 1_000_000_000})
        elif path == 'tail':
            capture.emit({'kind': 'blocked', 'at_ns': 1_000_000_000})
        else:
            capture.trigger({'kind': 'blocked', 'at_ns': 1_000_000_000})

    def producer():
        capture.summary()
        capture.emit({'kind': 'concurrent', 'at_ns': 1_500_000_000})
        progressed.set()

    def guarded(call):
        try:
            call()
        except BaseException as error:
            errors.append(error)

    output_thread = threading.Thread(target=guarded, args=(output,))
    producer_thread = threading.Thread(target=guarded, args=(producer,))
    output_thread.start()
    try:
        assert entered.wait(2), 'Output did not reach the barrier'
        producer_thread.start()
        assert progressed.wait(1), 'Incident output holds the lock needed by the engine producer'
    finally:
        release.set()
        output_thread.join(3)
        if producer_thread.ident is not None:
            producer_thread.join(3)
    assert not output_thread.is_alive() and not producer_thread.is_alive()
    assert not errors
    assert [row['kind'] for row in rows].count('blocked') == 1
    assert [row['kind'] for row in rows].count('concurrent') == 1


def test_incident_history_snapshot_survives_concurrent_eviction_and_recovery():
    entered, release, progressed = threading.Event(), threading.Event(), threading.Event()
    rows, errors = [], []

    def sink(record):
        if record['kind'] == 'first':
            entered.set()
            assert release.wait(5)
        rows.append(record)

    capture = IncidentBuffer(sink, max_records=2)
    capture.emit({'kind': 'first', 'at_ns': 0})
    capture.emit({'kind': 'second', 'at_ns': 1_000_000_000})

    def flush():
        capture.trigger({'kind': 'probe_failure', 'at_ns': 2_000_000_000})

    def producer():
        capture.emit({'kind': 'live_one', 'at_ns': 3_000_000_000})
        capture.emit({'kind': 'live_two', 'at_ns': 4_000_000_000})
        capture.trigger({'kind': 'probe_recovered', 'at_ns': 5_000_000_000})
        progressed.set()

    def guarded(call):
        try:
            call()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=guarded, args=(call,)) for call in (flush, producer)]
    threads[0].start()
    try:
        assert entered.wait(2)
        threads[1].start()
        assert progressed.wait(1), 'History flush prevents concurrent eviction/recovery'
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    # Concurrent publication order is not event order; consumers use at_ns.
    assert [row['kind'] for row in sorted(rows, key=lambda row: row['at_ns'])] == [
        'first', 'second', 'probe_failure', 'live_one', 'live_two', 'probe_recovered']
    assert [row['capture_incident'] for row in rows if 'capture_incident' in row] == [1, 1]
    assert capture.summary()['incidents'] == 1
    assert capture.summary()['buffered_records'] == 2
    assert capture.summary()['evicted_records'] == 2
