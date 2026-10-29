"""Summarize bounded offline traces without treating temporal overlap as causation."""
import argparse
import hashlib
import json
from collections import Counter
from itertools import chain
from pathlib import Path


def records(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def overlaps(start, end, low, high):
    return start <= high and end >= low


def analyze(directory, window, *, limit=20, process_case=None):
    result = json.loads((directory / 'result.json').read_text())
    data = result['cases'][process_case][window] if process_case else result['windows'][window]
    low, high = data['window_start_ns'], data['window_end_ns']
    trace = directory / process_case / 'trace' if process_case else directory / 'trace'
    client_path = trace / window / 'client.jsonl' if process_case else trace / 'client.jsonl'
    server_paths = (sorted([*trace.glob('api-requests-*.jsonl'), *trace.glob('api-loop-*.jsonl')]) if process_case else
                    [path for path in (trace / 'server.jsonl', trace / 'server-api.jsonl') if path.exists()])
    counts = Counter()
    clients, failures = [], []
    for row in records(client_path):
        if row['kind'] == 'client_request' and low <= row['scheduled_ns'] <= high:
            counts['slow_or_failed_client_requests'] += 1
            clients.append(row)
            clients = sorted(clients, key=lambda item: item['total_ms'], reverse=True)[:limit]
            if row.get('error') or row.get('status', 200) not in (200, 202):
                failures.append(row)
                failures = sorted(failures, key=lambda item: item['total_ms'], reverse=True)[:limit]
    selected = {row['request_id']: row for row in (*clients, *failures)}
    wanted = set(selected)
    roots, spans, stalls, cpu, gcs = {}, [], [], [], []
    gc_intervals = []
    operations = {}
    for row in chain.from_iterable(records(path) for path in server_paths):
        kind = row['kind']
        if kind == 'trace':
            if row.get('request_id') in wanted:
                roots[row['request_id']] = row
            if not overlaps(row['start_ns'], row['end_ns'], low, high):
                continue
            counts['slow_server_roots'] += 1
            counts['dropped_spans_in_slow_roots'] += row['dropped_spans']
            children = row['spans']
        elif kind == 'late_span' and overlaps(row['span']['start_ns'], row['span']['end_ns'], low, high):
            children = [row['span']]
        else:
            children = []
        for span in children:
            if not overlaps(span['start_ns'], span['end_ns'], low, high):
                continue
            operation = span['operation']
            aggregate = operations.setdefault(operation, dict(count=0, max_ms=0, thread_cpu_at_max_ms=0))
            aggregate['count'] += 1
            if operation == 'api_database_worker_start':
                aggregate['max_queue_wait_ms'] = max(aggregate.get('max_queue_wait_ms', 0), span['queue_wait_ms'])
            if span['duration_ms'] > aggregate['max_ms']:
                aggregate.update(max_ms=span['duration_ms'], thread_cpu_at_max_ms=span['thread_cpu_ms'])
            if operation != 'http.request' and not operation.startswith('service.'):
                spans.append(span)
                spans = sorted(spans, key=lambda item: item['duration_ms'], reverse=True)[:limit]
        if kind in ('loop_stall', 'api_loop_stall') and low <= row['at_ns'] <= high:
            if row['lag_ms'] < 0:
                counts['invalid_loop_stall_samples'] += 1
                continue
            counts['loop_stall_samples'] += 1
            stalls.append(row)
        if kind == 'cpu_sample' and low <= row['at_ns'] <= high:
            cpu.append(row)
        if kind == 'gc' and low <= row['start_ns'] <= high:
            gc_intervals.append(row)
            gcs.append(row)
            gcs = sorted(gcs, key=lambda item: item['duration_ms'], reverse=True)[:limit]
    # Only retain samples intersecting a top request/span. Match DB waits by PID
    # and sampling interval; another backend's lock is not evidence for this span.
    targets = {span['span_id']: {**span, 'postgres_observations': []} for span in spans}
    for root in roots.values():
        for span in root['spans']:
            if span.get('backend_pid'):
                targets.setdefault(span['span_id'], {**span, 'postgres_observations': []})
    for stall in stalls:
        for span in stall['active_spans']:
            if span.get('backend_pid'):
                targets.setdefault(span['span_id'], {**span, 'end_ns': stall['at_ns'], 'postgres_observations': []})
    for row in records(client_path):
        kind = row['kind']
        if kind == 'postgres_sampler_error':
            counts['postgres_sampler_errors_all_windows'] += 1
        if kind != 'postgres_sample' or not low <= row['start_ns'] <= high:
            continue
        counts['postgres_samples'] += 1
        for span in targets.values():
            if not overlaps(row['start_ns'], row['start_ns'] + row['duration_ms'] * 1e6,
                            span['start_ns'], span['end_ns']):
                continue
            for backend in row['backends']:
                if backend['backend_pid'] == span.get('backend_pid') and len(span['postgres_observations']) < 8:
                    span['postgres_observations'].append(dict(sample_ns=row['start_ns'], **backend))
    requests = []
    for client in selected.values():
        root = roots.get(client['request_id'])
        blocking = [row for row in stalls if overlaps(row['last_beat_ns'], row['at_ns'],
                                                      client['scheduled_ns'], client['end_ns'])]
        requests.append(dict(client=client, server=root, overlapping_stalls=sorted(
            blocking, key=lambda item: item['lag_ms'], reverse=True)[:3],
            stages=request_stages(client, root),
            overlapping_gc=sorted([row for row in gc_intervals if overlaps(
                row['start_ns'], row['start_ns'] + row['duration_ms'] * 1e6,
                client['scheduled_ns'], client['end_ns'])], key=lambda row: row['duration_ms'], reverse=True)[:3],
            database_spans=[targets[span['span_id']] for span in root['spans'] if span['span_id'] in targets] if root else []))
    delta = {key: cpu[-1]['cgroup_cpu'][key] - cpu[0]['cgroup_cpu'][key] for key in cpu[0]['cgroup_cpu']} if len(cpu) > 1 else None
    return dict(window=window, duration_seconds=data['duration_seconds'],
                window_start_ns=low, window_end_ns=high, image_id=result['image_id'],
                counts=dict(counts), operations=operations,
                slowest_requests=sorted(requests, key=lambda item: item['client']['total_ms'], reverse=True)[:limit],
                failed_requests=[item for item in requests if item['client']['request_id'] in
                                 {row['request_id'] for row in failures}],
                slowest_sync_spans=[targets[row['span_id']] for row in spans],
                worst_stalls=sorted(stalls, key=lambda item: item['lag_ms'], reverse=True)[:limit],
                cpu_samples=len(cpu), cgroup_cpu_delta=delta, longest_gc=gcs,
                client_trace=data.get('client_trace'), server_trace=data.get('runtime', {}).get('trace'),
                process_case=process_case,
                trace_sha256={str(path.relative_to(trace)): hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
                              for path in (*server_paths, client_path)},
                interpretation='Overlap is observational; thread CPU is inclusive. Missing PG waits between 250ms samples do not exclude DB waiting. Bounded traces may drop records; inspect loss counters.')


def request_stages(client, root):
    """Separate scheduled-client delay from measured server work; no inferred cause."""
    if root is None:
        return None
    dispatch = client.get('dispatch_ns', client['scheduled_ns'])
    return dict(client_queue_ms=(dispatch - client['scheduled_ns']) / 1e6,
                dispatch_to_handler_ms=(root['start_ns'] - dispatch) / 1e6,
                handler_ms=root['duration_ms'],
                handler_end_to_client_ms=(client['end_ns'] - root['end_ns']) / 1e6,
                worker_waits_ms=[span['queue_wait_ms'] for span in root['spans']
                                 if span['operation'] == 'api_database_worker_start'],
                sql_ms=[span['duration_ms'] for span in root['spans'] if span['operation'] == 'sql'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--window', required=True)
    parser.add_argument('--process-case', choices=('single', 'split'), help='Read the supervised diagnostic trace layout')
    args = parser.parse_args()
    output = analyze(args.directory, args.window, process_case=args.process_case)
    path = args.directory / (args.window + '-trace-analysis.json')
    path.write_text(json.dumps(output, indent=2) + '\n')
    print(path)


if __name__ == '__main__':
    main()
