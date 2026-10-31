"""Incident capture preserves bounded context without changing health decisions."""
import json
from test.e2e.offline.load_incidents import IncidentBuffer, ProbeIncidentPublisher, capture_limit

import pytest


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
