"""A useful architecture fault experiment cannot silently pass a failed load."""
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import test_mm_process_isolation as experiment


def test_diagnostic_returns_failure_when_healthy_load_misses_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['test_mm_process_isolation.py', '--duration', '30'])
    monkeypatch.setattr(experiment, 'docker', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps([dict(Architecture='amd64', Id='sha256:diagnostic')])))

    def case(image, args, mode, directory, result):
        result.update(completed=True, healthy=dict(violations=['read_p99'], read_p99_ms=2000),
                      stall=dict(errors=0, p99_ms=20))

    monkeypatch.setattr(experiment, 'run_case', case)
    assert experiment.main() == 1
    result = json.loads(next((tmp_path / 'build/mm-process-isolation').glob('*/result.json')).read_text())
    assert result['diagnostic'] is True and result['release_scope_complete'] is False
    assert set(result['cases']) == {'single', 'split'}
    assert all(row['healthy']['violations'] for row in result['cases'].values())


def test_diagnostic_does_not_discard_warmup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['test_mm_process_isolation.py', '--duration', '30'])
    monkeypatch.setattr(experiment, 'docker', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps([dict(Architecture='amd64', Id='sha256:diagnostic')])))

    def case(image, args, mode, directory, result):
        result.update(completed=True, warmup=dict(violations=['errors']),
                      healthy=dict(violations=[], read_p99_ms=20), stall=dict(errors=0, p99_ms=20))

    monkeypatch.setattr(experiment, 'run_case', case)
    assert experiment.main() == 1


def test_traced_warmup_and_healthy_keep_separate_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['test_mm_process_isolation.py', '--mode', 'split',
                                      '--supervision', 'restart', '--duration', '900',
                                      '--warmup-seconds', '300', '--trace'])
    monkeypatch.setattr(experiment, 'docker', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps([dict(Architecture='amd64', Id='sha256:diagnostic')])))

    def case(image, args, mode, directory, result):
        trace = directory / 'trace'
        trace.mkdir()
        options = [experiment.client_trace_options(trace, phase) for phase in ('warmup', 'healthy')]
        assert options == [['--env', 'HBOT_LOAD_TRACE_DIRECTORY=/trace-output/warmup'],
                           ['--env', 'HBOT_LOAD_TRACE_DIRECTORY=/trace-output/healthy']]
        for phase in ('warmup', 'healthy'):
            (trace / phase / 'client-summary.json').write_text(phase)
        assert (trace / 'warmup/client-summary.json').read_text() == 'warmup'
        assert experiment.client_trace_options(None, 'healthy') == []
        result.update(completed=True, healthy=dict(violations=[], read_p99_ms=20),
                      stall=dict(errors=0, p99_ms=20))

    monkeypatch.setattr(experiment, 'run_case', case)
    assert experiment.main() == 0


def test_daily_cache_comparison_records_explicit_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['test_mm_process_isolation.py', '--mode', 'split',
                                      '--duration', '300', '--daily-cache-ttl-seconds', '15'])
    monkeypatch.setattr(experiment, 'docker', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps([dict(Architecture='amd64', Id='sha256:diagnostic')])))

    def case(image, args, mode, directory, result):
        assert args.daily_cache_ttl_seconds == 15
        result.update(completed=True, healthy=dict(violations=[], read_p99_ms=20),
                      stall=dict(errors=0, p99_ms=20))

    monkeypatch.setattr(experiment, 'run_case', case)
    assert experiment.main() == 0
    result = json.loads(next((tmp_path / 'build/mm-process-isolation').glob('*/result.json')).read_text())
    assert result['daily_cache_ttl_seconds'] == 15


def test_recovery_waits_through_quote_refresh_and_accepts_partial_working_orders(monkeypatch):
    snapshots = iter([{'orders': [{'state': 'canceled'}]},
                      {'orders': [{'state': 'canceled'}, {'state': 'partially_filled'}]}])
    harness = SimpleNamespace(ledger=lambda: next(snapshots))

    def poll(predicate, *, timeout, description):
        assert timeout == 30
        assert not predicate()  # Normal refresh cancellation is not permanent loss.
        return predicate()

    monkeypatch.setattr(experiment, 'poll', poll)
    assert experiment.working_quotes_after_recovery(harness) == [{'state': 'partially_filled'}]


def test_recovery_still_fails_if_quotes_never_return(monkeypatch):
    harness = SimpleNamespace(ledger=lambda: {'orders': [{'state': 'canceled'}]})

    def poll(predicate, *, timeout, description):
        assert not predicate() and timeout == 30
        raise AssertionError('No working quotes within the recovery deadline')

    monkeypatch.setattr(experiment, 'poll', poll)
    with pytest.raises(AssertionError, match='No working quotes'):
        experiment.working_quotes_after_recovery(harness)
