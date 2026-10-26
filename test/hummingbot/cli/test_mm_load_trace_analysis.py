import json

from scripts.analyze_mm_load_trace import analyze


def test_analyzer_correlates_request_stack_and_only_same_pid_wait_in_window(tmp_path):
    trace = tmp_path / 'trace'
    trace.mkdir()
    window = dict(window_start_ns=1000, window_end_ns=3000, duration_seconds=2,
                  runtime={}, client_trace={'dropped_records': 0})
    (tmp_path / 'result.json').write_text(json.dumps(dict(image_id='pinned', windows={'mixed': window})))
    span = dict(span_id='s1', operation='commit_ms', start_ns=1500, end_ns=1900,
                duration_ms=400, thread_cpu_ms=2, backend_pid=9)
    root = dict(kind='trace', request_id='r1', start_ns=1200, end_ns=2000,
                duration_ms=800, spans=[span], dropped_spans=0)
    stall = dict(kind='loop_stall', last_beat_ns=1400, at_ns=1700, lag_ms=250,
                 loop_thread_id=7, active_spans=[span], stacks={'7': [{'function': 'do_commit'}]})
    outside = {**root, 'request_id': 'startup', 'start_ns': 1, 'end_ns': 2, 'spans': []}
    (trace / 'server.jsonl').write_text('\n'.join(map(json.dumps, [outside, root, stall])))
    request = dict(kind='client_request', request_id='r1', scheduled_ns=1100, end_ns=2200, total_ms=1100)
    sample = dict(kind='postgres_sample', start_ns=1600, duration_ms=.00001,
                  backends=[dict(backend_pid=9, wait_event='WALSync'), dict(backend_pid=10, wait_event='Lock')])
    (trace / 'client.jsonl').write_text('\n'.join(map(json.dumps, [request, sample])))
    result = analyze(tmp_path, 'mixed')
    assert result['counts']['slow_server_roots'] == 1
    assert result['slowest_requests'][0]['server']['request_id'] == 'r1'
    assert result['slowest_requests'][0]['overlapping_stalls'][0]['loop_thread_id'] == 7
    waits = result['slowest_sync_spans'][0]['postgres_observations']
    assert [row['wait_event'] for row in waits] == ['WALSync']
    assert result['operations']['commit_ms']['thread_cpu_at_max_ms'] == 2
