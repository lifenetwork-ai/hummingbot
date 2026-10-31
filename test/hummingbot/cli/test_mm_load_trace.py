import asyncio
import json
import linecache
import signal
import subprocess
import sys
from test.e2e.offline.load_trace import TraceRecorder, TraceSink, sql_metadata, stack_snapshot

import pytest


def test_stack_sampling_does_not_read_or_cache_source_lines(monkeypatch):
    calls = []
    original = linecache.getline

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(linecache, 'getline', observed)
    rows = stack_snapshot(sys._getframe(), limit=1)
    assert not calls, 'Incident sampling must not resolve source text'
    assert len(rows) == 1
    assert rows[0]['function'] == 'test_stack_sampling_does_not_read_or_cache_source_lines'
    assert set(rows[0]) == {'file', 'line', 'function'}


def test_stack_sampling_keeps_existing_oldest_to_newest_order_and_limit():
    def nested():
        return stack_snapshot(sys._getframe(), limit=2)
    assert [row['function'] for row in nested()] == [
        'test_stack_sampling_keeps_existing_oldest_to_newest_order_and_limit', 'nested']
    assert stack_snapshot(None) == []


def test_gc_callback_does_not_reenter_incident_lock_during_capture():
    # A real collection at JSON decoding/allocation inside trigger's lock.
    # Isolate the regression so a broken callback cannot deadlock pytest itself.
    program = '''
import gc,json,time
from test.e2e.offline.load_trace import DeferredGCTrace
from test.e2e.offline.load_incidents import IncidentBuffer
events=[]
capture=IncidentBuffer(events.append)
gc_trace=DeferredGCTrace(minimum_ms=0)
gc.disable()
gc.callbacks.append(gc_trace)
capture.emit({'kind':'before','at_ns':time.monotonic_ns()})
original=json.loads
def collect_inside_capture(line):
    gc.collect()
    return original(line)
json.loads=collect_inside_capture
capture.trigger({'kind':'probe_failure','at_ns':time.monotonic_ns()})
gc.callbacks.remove(gc_trace)
json.loads=original
gc_trace.drain(capture.emit)
assert any(row['kind']=='gc' for row in events)
assert capture.summary()['incidents']==1
'''
    result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_deferred_gc_trace_is_bounded_and_reports_loss(monkeypatch):
    from test.e2e.offline import load_trace
    probe = load_trace.DeferredGCTrace(limit=2, minimum_ms=0)
    ticks = iter([0, 10, 20, 30, 40, 50])
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: next(ticks))
    for n in range(3):
        probe('start', {'generation': 2})
        probe('stop', {'generation': 2, 'collected': n})
    assert probe.dropped_events == 1
    events = []
    probe.drain(events.append)
    assert [r['collected'] for r in events] == [1, 2]
    probe.drain(events.append)
    assert len(events) == 2 and not probe.started


def test_signal_stack_dump_works_while_child_is_deadlocked_without_unlocking_it():
    program = '''
import faulthandler,signal,threading
faulthandler.register(signal.SIGUSR2,all_threads=True)
def blocked_on_lock():
    lock=threading.Lock()
    lock.acquire()
    print('ready',flush=True)
    lock.acquire()
blocked_on_lock()
'''
    child = subprocess.Popen([sys.executable, '-c', program], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        child.send_signal(signal.SIGUSR2)
        with pytest.raises(subprocess.TimeoutExpired):
            child.communicate(timeout=.2)
    finally:
        child.kill()
        _, error = child.communicate(timeout=3)
    assert 'blocked_on_lock' in error and 'thread' in error.lower()


def test_sql_trace_retains_shape_without_literal_credentials_or_parameters():
    details = sql_metadata("UPDATE bot_state SET token='secret''value', amount=123.456 WHERE bot_id=%(bot_id)s")
    assert details['sql_kind'] == 'UPDATE'
    assert 'bot_state' in details['sql_template']
    assert 'secret' not in details['sql_template'] and '123.456' not in details['sql_template']
    assert details == sql_metadata("UPDATE bot_state SET token='other', amount=999 WHERE bot_id=%(bot_id)s")
    assert 'password' not in sql_metadata('SELECT $tag$password$tag$')['sql_template']


@pytest.mark.asyncio
async def test_trace_correlates_worker_sql_with_request_and_keeps_parentage():
    records = []
    recorder = TraceRecorder(records.append, slow_ms=0)
    request = recorder.begin('http.request', request_id='request-1', route='/orders')

    def query():
        pool = recorder.begin('db.pool')
        recorder.end(pool, backend_pid=17)
        sql = recorder.begin('db.sql', **sql_metadata('SELECT * FROM orders WHERE id=7'))
        recorder.end(sql, backend_pid=17)
    await asyncio.to_thread(query)
    recorder.end(request, status=200)
    assert len(records) == 1
    trace = records[0]
    assert trace['request_id'] == 'request-1'
    assert {span['operation'] for span in trace['spans']} == {'db.pool', 'db.sql', 'http.request'}
    assert all(span['trace_id'] == trace['trace_id'] for span in trace['spans'])
    sql = next(span for span in trace['spans'] if span['operation'] == 'db.sql')
    assert sql['parent_id'] == request['span_id'] and sql['backend_pid'] == 17
    assert recorder.active_snapshot() == []


def test_finished_request_context_does_not_capture_later_background_work():
    records = []
    recorder = TraceRecorder(records.append, slow_ms=0)
    old = recorder.begin('http.request', request_id='old')
    # Simulate a task inheriting context before the request finished.
    import contextvars
    inherited = contextvars.copy_context()
    recorder.end(old)

    def later():
        span = recorder.begin('background')
        recorder.end(span)
    inherited.run(later)
    assert len(records) == 2
    assert records[1]['request_id'] is None and records[0]['trace_id'] != records[1]['trace_id']


def test_sink_is_bounded_and_reports_dropped_records(tmp_path):
    path = tmp_path / 'trace.jsonl'
    sink = TraceSink(path, max_bytes=500, queue_limit=8)
    for _ in range(100):
        sink.emit({'kind': 'sample', 'data': 'x' * 150})
    sink.close()
    assert path.stat().st_size <= 500
    assert sink.summary()['dropped_records'] > 0
    assert all(json.loads(line)['kind'] == 'sample' for line in path.read_text().splitlines())


def test_later_window_appends_traces_without_overwriting_earlier_evidence(tmp_path):
    path = tmp_path / 'trace.jsonl'
    for window in ('warmup', 'mixed'):
        sink = TraceSink(path, append=True)
        sink.emit({'window': window})
        sink.close()
    assert [json.loads(line)['window'] for line in path.read_text().splitlines()] == ['warmup', 'mixed']


def test_request_children_are_bounded_and_loss_is_explicit():
    records = []
    recorder = TraceRecorder(records.append, slow_ms=0, max_children=2)
    root = recorder.begin('request')
    for _ in range(20):
        child = recorder.begin('sql')
        recorder.end(child)
    recorder.end(root)
    assert len(records[0]['spans']) == 2
    assert records[0]['dropped_spans'] == 19
    assert recorder.active_snapshot() == []


def test_incident_sampler_bounds_active_span_snapshot_without_losing_active_state():
    recorder = TraceRecorder(lambda row: None)
    spans = [recorder.begin('sql', backend_pid=n) for n in range(20)]
    assert len(recorder.active_snapshot(limit=3)) == 3
    assert len(recorder.active_snapshot()) == 20
    for span in reversed(spans):
        recorder.end(span)
    assert recorder.active_snapshot() == []


def test_full_gc_probe_is_bounded_and_ignores_young_collections(monkeypatch):
    from test.e2e.offline import load_trace
    probe = load_trace.FullGCProbe(limit=2)
    ticks = iter([1000000, 3000000, 4000000, 7000000, 8000000, 12000000])
    monkeypatch.setattr(load_trace.time, 'monotonic_ns', lambda: next(ticks))
    probe('start', {'generation': 0})
    probe('stop', {'generation': 0, 'collected': 999})
    for collected in (1, 2, 3):
        probe('start', {'generation': 2})
        probe('stop', {'generation': 2, 'collected': collected})
    assert [row['collected'] for row in probe.events] == [2, 3]
    assert [row['duration_ms'] for row in probe.events] == [3, 4]
    assert probe.started == {}
