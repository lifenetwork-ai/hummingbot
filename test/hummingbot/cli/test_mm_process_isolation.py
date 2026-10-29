"""A useful architecture fault experiment cannot silently pass a failed load."""
import json
import sys
from types import SimpleNamespace

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
