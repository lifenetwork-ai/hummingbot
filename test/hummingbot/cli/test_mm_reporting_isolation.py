"""The diagnostic must wait for a running observation before injecting a fault."""
from scripts import test_mm_reporting_isolation as isolation


def test_running_metrics_waits_through_missing_and_startup_snapshots(monkeypatch):
    running = {'runtime_running': True, 'process_id': 42}
    values = iter([None, {'runtime_running': False, 'runtime_phase': 'starting'}, running])
    seen = []

    def read(harness, bot):
        value = next(values)
        seen.append(value)
        return value

    monkeypatch.setattr(isolation, 'read_metrics', read)
    assert isolation.wait_running_metrics(object(), 'bot') is running
    assert len(seen) == 3
