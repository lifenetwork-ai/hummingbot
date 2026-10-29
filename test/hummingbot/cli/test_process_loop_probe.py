"""Diagnostic teardown must release its callback/task/thread and omit locals."""
import asyncio
import gc
import json
import time
from pathlib import Path

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_loop_stall_probe_omits_locals_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'e2e/offline'))
    from test.e2e.offline.process_isolation import loop_probe

    records = []

    class Sink:
        closed = False

        def __init__(self, path):
            pass

        def emit(self, event):
            records.append(event)

        def close(self):
            self.closed = True

        def summary(self):
            return dict(closed=self.closed)

    sink = Sink(None)
    monkeypatch.setattr(loop_probe, 'TraceSink', lambda *args: sink)
    monkeypatch.setenv('HBOT_LOAD_TRACE_DIRECTORY', str(tmp_path))
    previous_callbacks = list(gc.callbacks)
    private_payload = 'never-record-this-local-value'
    app = web.Application()
    loop_probe.attach(app)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        await asyncio.sleep(.06)
        time.sleep(.45)  # Deliberate synchronous loop stall; watchdog is independent.
        gc.collect()
        await asyncio.sleep(.06)
    finally:
        await runner.cleanup()
    assert sink.closed and gc.callbacks == previous_callbacks
    assert not any('heartbeat' in task.get_coro().__qualname__ for task in asyncio.all_tasks())
    assert any(row['kind'] == 'api_loop_stall' for row in records)
    assert private_payload not in json.dumps(records)
    summary = json.loads(next(tmp_path.glob('*-summary.json')).read_text())
    assert summary['sink']['closed']
    assert any(row['collected'] >= 0 for row in summary['full_gc'])
