"""Measurement must not stall the measured loop or accumulate export jobs."""
import asyncio
import json
import threading
from collections import deque
from test.e2e.offline.load_process_metrics import MetricsExporter, distribution

import pytest


def test_distributions_preserve_exact_nearest_rank_and_empty_values():
    assert distribution(()) == dict(count=0, p50=None, p95=None, p99=None, max=None)
    assert distribution((9, 1, 3, 2)) == dict(count=4, p50=2, p95=9, p99=9, max=9)


@pytest.mark.asyncio
async def test_blocked_export_cannot_block_loop_or_queue_more_jobs(tmp_path):
    entered, release = threading.Event(), threading.Event()
    recorded = []

    def writer(path, data, values):
        entered.set()
        assert release.wait(3)
        recorded.append((data, values))

    exporter = MetricsExporter(tmp_path / 'metrics.json', writer=writer)
    values = deque([1, 2], maxlen=2)
    try:
        assert exporter.submit({'at': 1}, {'sql_ms': values})
        assert await asyncio.to_thread(entered.wait, 1)
        values.append(3)
        assert not exporter.submit({'at': 2}, {'sql_ms': values})
        assert not exporter.submit({'at': 3}, {'sql_ms': values})
        # A blocked worker cannot keep even a trivial loop callback pending.
        callback = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(callback.set_result, 'alive')
        assert await asyncio.wait_for(callback, .5) == 'alive'
    finally:
        release.set()
        await asyncio.to_thread(exporter.close)
    assert recorded == [({'at': 1}, {'sql_ms': (1, 2)})]
    assert exporter.skipped == 2


def test_export_failure_is_visible_to_next_submission_and_close(tmp_path):
    def broken(*args):
        raise OSError('test output unavailable')

    exporter = MetricsExporter(tmp_path / 'metrics.json', writer=broken)
    exporter.submit({}, {})
    with pytest.raises(OSError, match='test output unavailable'):
        exporter.pending.result(timeout=1)
    with pytest.raises(OSError, match='test output unavailable'):
        exporter.submit({}, {})
    with pytest.raises(OSError, match='test output unavailable'):
        exporter.close()
    with pytest.raises(RuntimeError, match='closed'):
        exporter.submit({}, {})


def test_export_writes_same_quantiles_atomically(tmp_path, monkeypatch):
    from pathlib import Path
    from test.e2e.offline.load_process_metrics import write_metrics_snapshot
    original = Path.read_text

    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/status':
            return 'VmRSS: 12 kB\nVmHWM: 20 kB\n'
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    path = tmp_path / 'metrics.json'
    # Combined mode avoids the supervised process-discovery path in this unit.
    write_metrics_snapshot(path, {'at': 1, 'process_role': 'combined'}, {'sql_ms': (3, 1, 2)})
    row = json.loads(path.read_text())
    assert row['at'] == 1 and row['distributions']['sql_ms'] == distribution((3, 1, 2))
    assert row['memory']['VmRSS'] > 0
    assert not path.with_suffix('.new').exists()
