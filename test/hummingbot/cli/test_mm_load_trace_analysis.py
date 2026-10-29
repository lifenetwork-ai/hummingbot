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


def test_failed_request_keeps_its_server_trace_even_when_outside_slowest_limit(tmp_path):
    trace = tmp_path / 'trace'
    trace.mkdir()
    window = dict(window_start_ns=1000, window_end_ns=3000, duration_seconds=2, runtime={})
    (tmp_path / 'result.json').write_text(json.dumps(dict(image_id='pinned', windows={'mixed': window})))
    requests = [dict(kind='client_request', request_id='slow-success', scheduled_ns=1100,
                     end_ns=2500, total_ms=1400, status=200),
                dict(kind='client_request', request_id='timeout', scheduled_ns=1200,
                     end_ns=1800, total_ms=600, status=None, error='TimeoutError')]
    roots = [dict(kind='trace', request_id=row['request_id'], start_ns=row['scheduled_ns'],
                  end_ns=row['end_ns'], duration_ms=row['total_ms'], spans=[], dropped_spans=0)
             for row in requests]
    (trace / 'server.jsonl').write_text('\n'.join(map(json.dumps, roots)))
    (trace / 'client.jsonl').write_text('\n'.join(map(json.dumps, requests)))
    result = analyze(tmp_path, 'mixed', limit=1)
    assert result['slowest_requests'][0]['client']['request_id'] == 'slow-success'
    assert result['failed_requests'][0]['server']['request_id'] == 'timeout'


def test_supervised_analysis_separates_windows_workers_and_gc(tmp_path):
    trace = tmp_path / 'split/trace'
    (trace / 'healthy').mkdir(parents=True)
    (trace / 'warmup').mkdir()
    data = dict(window_start_ns=1000000, window_end_ns=3000000, duration_seconds=2)
    (tmp_path / 'result.json').write_text(json.dumps(dict(image_id='pinned', cases={'split': {'healthy': data}})))
    worker = dict(span_id='worker', operation='api_database_worker_start', queue_wait_ms=.2,
                  start_ns=1300000, end_ns=1400000, duration_ms=.1, thread_cpu_ms=.01)
    sql = dict(span_id='sql', operation='sql', backend_pid=9, start_ns=1500000,
               end_ns=1800000, duration_ms=.3, thread_cpu_ms=.02)
    root = dict(kind='trace', request_id='healthy', start_ns=1200000, end_ns=2000000,
                duration_ms=.8, spans=[worker, sql], dropped_spans=0)
    gc = dict(kind='gc', start_ns=1600000, duration_ms=.3, generation=2)
    stall = dict(kind='api_loop_stall', last_beat_ns=1400000, at_ns=1700000,
                 lag_ms=250, active_spans=[], stacks={})
    (trace / 'api-requests-14.jsonl').write_text(json.dumps(root) + '\n')
    (trace / 'api-loop-14.jsonl').write_text('\n'.join(map(json.dumps, [gc, stall])))
    client = dict(kind='client_request', request_id='healthy', scheduled_ns=1000000,
                  dispatch_ns=1100000, end_ns=2200000, total_ms=1.2)
    (trace / 'healthy/client.jsonl').write_text(json.dumps(client))
    (trace / 'warmup/client.jsonl').write_text('invalid and must not be read')
    result = analyze(tmp_path, 'healthy', process_case='split')
    request = result['slowest_requests'][0]
    assert request['stages'] == dict(client_queue_ms=.1, dispatch_to_handler_ms=.1,
                                     handler_ms=.8, handler_end_to_client_ms=.2,
                                     worker_waits_ms=[.2], sql_ms=[.3])
    assert request['overlapping_gc'] == [gc]
    assert request['overlapping_stalls'] == [stall]
    assert set(result['trace_sha256']) == {'healthy/client.jsonl', 'api-loop-14.jsonl', 'api-requests-14.jsonl'}
