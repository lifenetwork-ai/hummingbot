import asyncio
import threading
from collections import defaultdict
from test.e2e.offline.load_trace import TraceRecorder, instrument_sync

import pytest


def test_sync_stage_preserves_results_exceptions_and_measures_thread_cpu():
    class Stage:
        def execute(self, value):
            if isinstance(value, Exception):
                raise value
            return value * 2
    records, samples, totals = [], defaultdict(list), defaultdict(int)
    instrument_sync(Stage, 'execute', 'stage_ms', samples, totals, TraceRecorder(records.append, slow_ms=0))
    assert Stage().execute(3) == 6
    error = ValueError('not included in trace')
    with pytest.raises(ValueError) as caught:
        Stage().execute(error)
    assert caught.value is error
    assert totals['stage_ms_count'] == 2
    assert len(samples['stage_cpu_ms']) == 2
    assert records[-1]['spans'][0]['error'] == 'ValueError'
    assert 'not included' not in str(records)


def test_inherited_classmethod_keeps_concrete_subclass_and_arguments():
    class Parent:
        @classmethod
        def build(cls, value, *, option):
            return cls, value, option

    class Child(Parent):
        pass
    samples, totals = defaultdict(list), defaultdict(int)
    instrument_sync(Parent, 'build', 'validation_ms', samples, totals, None)
    assert Child.build(7, option='x') == (Child, 7, 'x')
    assert len(samples['validation_cpu_ms']) == 1


def test_slow_only_sync_avoids_fast_span_allocation_but_preserves_slow_error_and_metrics(monkeypatch):
    from test.e2e.offline import load_trace
    clock = [0]
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: clock[0])
    monkeypatch.setattr(load_trace.time, 'perf_counter_ns', lambda: clock[0])

    class Stage:
        def advance(self, value):
            clock[0] += value * 1_000_000
            if value >= 100:
                raise ValueError('private error text')
            return value
    records, samples, totals = [], defaultdict(list), defaultdict(int)
    tracer = TraceRecorder(records.append, slow_ms=100)
    root = tracer.begin('rebuild')
    calls = []
    original = load_trace.uuid.uuid4

    def identity():
        calls.append(1)
        return original()
    monkeypatch.setattr(load_trace.uuid, 'uuid4', identity)
    instrument_sync(Stage, 'advance', 'advance_ms', samples, totals, tracer, slow_only=True)
    for _ in range(100):
        assert Stage().advance(1) == 1
    assert not calls
    with pytest.raises(ValueError, match='private error text'):
        Stage().advance(150)
    tracer.end(root)
    child = next(s for s in records[0]['spans'] if s['operation'] == 'advance_ms')
    assert child['duration_ms'] == 150 and child['error'] == 'ValueError'
    assert child['parent_id'] == root['span_id']
    assert totals['advance_ms_count'] == len(samples['advance_ms']) == 101
    assert 'private error text' not in str(records)
    assert tracer.active_snapshot() == []


@pytest.mark.asyncio
async def test_slow_only_worker_finishing_after_request_keeps_late_parentage():
    started, release = threading.Event(), threading.Event()

    class Stage:
        def execute(self):
            started.set()
            assert release.wait(3)
            return 'complete'
    rows, samples, totals = [], defaultdict(list), defaultdict(int)
    tracer = TraceRecorder(rows.append, slow_ms=0)
    instrument_sync(Stage, 'execute', 'stage_ms', samples, totals, tracer, slow_only=True)
    root = tracer.begin('request', request_id='original')
    task = asyncio.create_task(asyncio.to_thread(Stage().execute))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        tracer.end(root)
    finally:
        release.set()
    assert await task == 'complete'
    assert [r['kind'] for r in rows] == ['trace', 'late_span']
    assert rows[1]['request_id'] == 'original'
    assert rows[1]['span']['parent_id'] == root['span_id']
    assert tracer.active_snapshot() == []


def test_slow_only_standalone_call_keeps_trace_identity_without_active_context():
    class Stage:
        def execute(self):
            return 17
    rows, samples, totals = [], defaultdict(list), defaultdict(int)
    tracer = TraceRecorder(rows.append, slow_ms=0)
    instrument_sync(Stage, 'execute', 'stage_ms', samples, totals, tracer, slow_only=True)
    assert Stage().execute() == 17
    assert rows[0]['operation'] == 'stage_ms'
    assert rows[0]['trace_id'] == rows[0]['spans'][0]['trace_id']
    assert rows[0]['spans'][0]['parent_id'] is None
    assert tracer.active_snapshot() == []
