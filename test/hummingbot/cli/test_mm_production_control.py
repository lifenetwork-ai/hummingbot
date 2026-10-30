import pytest

from scripts.test_mm_production_control import summarize_window


def client_window():
    return dict(duration_seconds=300, requested_reads=6000, completed_reads=6000,
                queued_reads_at_generation_end=0, unexpected_errors=0,
                read_p95_ms=100, read_p99_ms=500, backlog_drain_seconds=1,
                market_updates_per_second=100, fills_per_second=1,
                command_accept_p95_ms=100, command_accept_p99_ms=200,
                control_results={'reconciliation': {'state': 'incomplete'}})


def summarize(data, controls, *, phase='running'):
    return summarize_window(data, memory={'current_bytes': 700_000_000, 'peak_bytes': 800_000_000},
                            phase=phase, setup_commands=controls, ledger={'positions_matched': True})


def test_nonburst_window_uses_actual_setup_commands_without_requiring_burst_output():
    data = client_window()
    measured = {'start': 2.1, 'switch': 3.2, 'stop': .7}
    row = summarize(data, measured)
    assert row['command_seconds'] == measured
    assert row['violations'] == []
    assert row['ledger']['positions_matched']
    assert 'command_seconds' not in data


@pytest.mark.parametrize('action,limit', [('start', 15), ('switch', 15), ('stop', 5)])
def test_fallback_does_not_waive_missing_or_slow_command_measurements(action, limit):
    measured = {'start': 2, 'switch': 3, 'stop': 1}
    del measured[action]
    assert 'command_' + action in summarize(client_window(), measured)['violations']
    measured[action] = limit + .01
    assert 'command_' + action in summarize(client_window(), measured)['violations']


def test_actual_inwindow_failure_is_not_hidden_by_healthy_setup():
    data = client_window()
    data['control_results']['command_seconds'] = {'start': 16, 'switch': 2, 'stop': 1}
    row = summarize(data, {'start': 1, 'switch': 1, 'stop': 1})
    assert 'command_start' in row['violations']


@pytest.mark.parametrize('fault', ['halt', 'errors', 'latency', 'rate'])
def test_control_retains_existing_runtime_http_latency_and_rate_gates(fault):
    data = client_window()
    phase = 'halted' if fault == 'halt' else 'running'
    if fault == 'errors':
        data['unexpected_errors'] = 1
    elif fault == 'latency':
        data['read_p99_ms'] = 1001
    elif fault == 'rate':
        data['market_updates_per_second'] = 80
    assert summarize(data, {'start': 1, 'switch': 1, 'stop': 1}, phase=phase)['violations']
