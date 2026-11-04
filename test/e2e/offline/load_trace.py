"""Bounded, test-only causal traces. Never records SQL parameters or frame locals."""
import contextvars
import hashlib
import heapq
import inspect
import json
import queue
import re
import threading
import time
import uuid
from collections import deque
from functools import lru_cache
from itertools import islice
from pathlib import Path


def timed_cpu_sample(path, *, process_role, previous_sample_ns):
    """Counters are read within an interval, never at an earlier watchdog tick."""
    started = time.monotonic_ns()
    row = dict(kind='cpu_sample', sample_schema_version=2, at=time.time(), at_ns=started,
               sample_started_ns=started, process_role=process_role,
               process_cpu_ns=time.process_time_ns(), sampler_thread_cpu_ns=time.thread_time_ns(),
               sample_gap_ms=(started - previous_sample_ns) / 1e6,
               cgroup_cpu={key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())})
    row['sample_ended_ns'] = time.monotonic_ns()
    row['collection_ms'] = (row['sample_ended_ns'] - started) / 1e6
    return row


def sample_heartbeat(read_beat):
    """Reject a heartbeat changing across the sampling timestamp."""
    started = time.monotonic_ns()
    before = read_beat()
    observed = time.monotonic_ns()
    after = read_beat()
    consistent = before == after and before <= observed
    return dict(at=time.time(), at_ns=observed, sample_started_ns=started,
                sample_ended_ns=time.monotonic_ns(), last_beat_ns=after, consistent=consistent,
                lag_ms=max(0, (observed - before) / 1e6 - 50) if consistent else None)


class FullGCProbe:
    """Minimal optional measurement; never changes GC policy or retains objects."""

    def __init__(self, limit=128):
        self.events = deque(maxlen=limit)
        self.started = {}

    def __call__(self, phase, info):
        if info['generation'] != 2:
            return
        identity = threading.get_ident()
        if phase == 'start':
            self.started[identity] = time.monotonic_ns(), time.thread_time_ns()
        elif identity in self.started:
            started, cpu = self.started.pop(identity)
            self.events.append(dict(start_ns=started, at=time.time(), thread_id=identity,
                                    duration_ms=(time.monotonic_ns() - started) / 1e6,
                                    thread_cpu_ms=(time.thread_time_ns() - cpu) / 1e6,
                                    collected=info['collected']))


class DeferredGCTrace:
    """GC callbacks may interrupt any recorder lock: never emit from the callback.

    Keep only scalar events in a bounded deque. The ordinary watchdog drains it
    after collection, outside the callback and outside any recorder critical section.
    """

    def __init__(self, *, limit=128, minimum_ms=10):
        self.pending = deque(maxlen=limit)
        self.started = {}
        self.minimum_ms = minimum_ms
        self.dropped_events = 0

    def __call__(self, phase, info):
        key = threading.get_ident(), info['generation']
        if phase == 'start':
            self.started[key] = time.monotonic_ns()
        elif key in self.started:
            started = self.started.pop(key)
            duration = (time.monotonic_ns() - started) / 1e6
            if duration >= self.minimum_ms:
                if len(self.pending) == self.pending.maxlen:
                    self.dropped_events += 1
                self.pending.append(dict(kind='gc', at=time.time(), start_ns=started, duration_ms=duration,
                                         thread_id=key[0], generation=key[1], collected=info['collected']))

    def drain(self, sink):
        while self.pending:
            try:
                event = self.pending.popleft()
            except IndexError:
                break
            sink(event)

    def summary(self):
        return dict(pending_events=len(self.pending), max_events=self.pending.maxlen,
                    dropped_events=self.dropped_events)


def instrument_sync(owner, name, key, samples, totals, tracer, attributes=None, *, slow_only=False):
    """Test-only synchronous wall/thread-CPU timing, preserving method binding."""
    descriptor = inspect.getattr_static(owner, name)
    original = descriptor.__func__ if isinstance(descriptor, classmethod) else getattr(owner, name)

    def measured(*args, **kwargs):
        started, cpu = time.perf_counter_ns(), time.thread_time_ns()
        trace_started = time.monotonic_ns() if tracer and slow_only else None
        parent = tracer.current.get() if tracer and slow_only else None
        if parent and parent['_root']['finished']:
            parent = None
        span = tracer.begin(key) if tracer and not slow_only else None
        result, error = None, None
        try:
            result = original(*args, **kwargs)
            return result
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            duration, cpu_ms = (time.perf_counter_ns() - started) / 1e6, (time.thread_time_ns() - cpu) / 1e6
            samples[key].append(duration)
            samples[key.removesuffix('_ms') + '_cpu_ms'].append(cpu_ms)
            totals[key + '_count'] += 1
            if tracer and (not slow_only or duration >= tracer.slow_ms):
                details = attributes(args, result) if attributes else {}
                if slow_only:
                    tracer.completed_sync(key, trace_started, time.monotonic_ns(), cpu_ms,
                                          parent=parent, error=error, **details)
                else:
                    tracer.end(span, error=error, **details)
    setattr(owner, name, classmethod(measured) if isinstance(descriptor, classmethod) else measured)


def instrument_admin_queue(owner, samples, totals, tracer):
    """Test-only submit-to-physical-start wait, distinct from SQL pool checkout.

    Keep the original executor's priority, capacity and cancellation semantics.
    Only scalar timings enter bounded samples; call arguments/results never do.
    """
    original = owner.submit
    metrics_lock = threading.Lock()
    values = samples['api_worker_queue_ms']
    totals['api_worker_queue_count'] += 0

    def submit(self, call, *, control=False, heavy=False):
        started = time.perf_counter_ns()
        context = contextvars.copy_context()
        pending = self.pending
        parent = tracer.current.get() if tracer else None
        request_id = parent['_root']['request_id'] if parent else None

        def admitted():
            waited = (time.perf_counter_ns() - started) / 1e6
            with metrics_lock:
                values.append(waited)
                totals['api_worker_queue_count'] += 1
            if tracer:
                span = tracer.begin('api_database_worker_start', queue_wait_ms=waited,
                                    request_id=request_id, pending_at_submit=pending, control=control,
                                    workload='heavy' if heavy else 'operations')
                tracer.end(span)
            return call()

        return original(self, lambda: context.run(admitted), control=control, heavy=heavy)

    owner.submit = submit


@lru_cache(maxsize=256)
def sql_metadata(statement):
    statement = re.sub(r'(\$[\w]*\$).*?\1', '?', statement, flags=re.S)
    template = re.sub(r"'(?:''|[^'])*'", '?', statement)
    template = re.sub(r'(?<![\w$])\d+(?:\.\d+)?(?!\w)', '?', template)
    template = ' '.join(template.split())[:2500]
    return dict(sql_kind=template.split(' ', 1)[0].upper(), sql_template=template,
                sql_fingerprint=hashlib.sha256(template.encode()).hexdigest()[:20])


class TraceSink:
    def __init__(self, path, *, max_bytes=128 * 1024**2, queue_limit=2048, append=False,
                 reserved_bytes=0, reserved_kinds=()):
        if isinstance(reserved_bytes, bool) or not isinstance(reserved_bytes, int) or not 0 <= reserved_bytes <= max_bytes:
            raise ValueError('Reserved trace bytes must be an integer within the total byte budget')
        self.path, self.max_bytes = Path(path), max_bytes
        self.reserved_bytes, self.reserved_kinds = reserved_bytes, frozenset(reserved_kinds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue(maxsize=queue_limit)
        self.stopping = threading.Event()
        self.append = append
        size = self.path.stat().st_size if append and self.path.exists() else 0
        reserved = 0
        if size and reserved_bytes:
            with self.path.open('rb') as stream:
                for line in stream:
                    if json.loads(line).get('kind') in self.reserved_kinds:
                        reserved += len(line)
        self.counts = dict(written_records=0, written_bytes=size, ordinary_written_bytes=size - reserved,
                           reserved_written_bytes=reserved, dropped_records=0, writer_cpu_ms=0)
        self.dropped_by_kind = {}
        self.counts_lock = threading.Lock()
        self.worker = threading.Thread(target=self._write, name='load-trace-writer', daemon=True)
        self.worker.start()

    def emit(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self._drop(record)

    def _drop(self, record):
        kind = record.get('kind', 'other')
        # Diagnostic loss accounting must not create an unbounded key registry.
        if kind not in {'cpu_sample', 'trace', 'late_span', 'gc', 'loop_stall', 'api_loop_stall',
                        'loop_stall_trigger', 'probe_failure', 'probe_recovered', 'client_request'}:
            kind = 'other'
        with self.counts_lock:
            self.counts['dropped_records'] += 1
            self.dropped_by_kind[kind] = self.dropped_by_kind.get(kind, 0) + 1

    def _write(self):
        with self.path.open('a' if self.append else 'w', buffering=65536) as output:
            last_flush = time.monotonic()
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=.2)
                except queue.Empty:
                    output.flush()
                    continue
                started = time.thread_time_ns()
                line = json.dumps(record, separators=(',', ':'), default=str) + '\n'
                size = len(line.encode())
                reserved = record.get('kind') in self.reserved_kinds
                fits = self.counts['written_bytes'] + size <= self.max_bytes
                if not reserved:
                    fits = fits and self.counts['ordinary_written_bytes'] + size <= self.max_bytes - self.reserved_bytes
                if fits:
                    output.write(line)
                    with self.counts_lock:
                        self.counts['written_bytes'] += size
                        self.counts['written_records'] += 1
                        self.counts['reserved_written_bytes' if reserved else 'ordinary_written_bytes'] += size
                else:
                    self._drop(record)
                self.counts['writer_cpu_ms'] += (time.thread_time_ns() - started) / 1e6
                self.queue.task_done()
                if time.monotonic() - last_flush >= 1:
                    output.flush()
                    last_flush = time.monotonic()

    def summary(self):
        with self.counts_lock:
            counts = {**self.counts, 'dropped_by_kind': dict(self.dropped_by_kind)}
        return {**counts, 'queue_depth': self.queue.qsize(), 'max_bytes': self.max_bytes,
                'reserved_bytes': self.reserved_bytes, 'reserved_kinds': sorted(self.reserved_kinds),
                'writer_alive': self.worker.is_alive()}

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=10)


class TraceRecorder:
    def __init__(self, sink, *, slow_ms=100, max_children=128):
        self.sink, self.slow_ms, self.max_children = sink, slow_ms, max_children
        self.current = contextvars.ContextVar('load_trace_' + uuid.uuid4().hex, default=None)
        self.lock = threading.Lock()
        self.active = {}
        self.dropped_spans = 0

    @staticmethod
    def _new_root(operation, request_id=None):
        return dict(trace_id=uuid.uuid4().hex, request_id=request_id, operation=operation,
                    spans=[], finished=False, dropped_spans=0, _children=[], _seen=0)

    def begin(self, operation, **attributes):
        parent = self.current.get()
        root = parent['_root'] if parent and not parent['_root']['finished'] else None
        owns = root is None
        if owns:
            root = self._new_root(operation, attributes.get('request_id'))
        span = dict(span_id=uuid.uuid4().hex[:16], trace_id=root['trace_id'],
                    parent_id=parent['span_id'] if not owns else None, operation=operation,
                    start_ns=time.monotonic_ns(), start_utc=time.time(), thread_id=threading.get_ident(),
                    **attributes)
        span.update(_root=root, _owns=owns, _cpu=time.thread_time_ns())
        with self.lock:
            self.active[span['span_id']] = span
        span['_token'] = self.current.set(span)
        return span

    def end(self, span, **attributes):
        ended = time.monotonic_ns()
        public = {key: value for key, value in span.items() if not key.startswith('_')}
        public.update(end_ns=ended, duration_ms=(ended - span['start_ns']) / 1e6,
                      thread_cpu_ms=(time.thread_time_ns() - span['_cpu']) / 1e6, **attributes)
        event = self._finish(span['_root'], public, owns=span['_owns'], active_id=span['span_id'])
        self.current.reset(span['_token'])
        if event:
            self.sink(event)

    def completed_sync(self, operation, started, ended, cpu_ms, *, parent, **attributes):
        """Rare completed slow leaves; all-call scalar metrics remain separate.

        No active leaf is registered. During a blocked call the watchdog still
        captures its thread stack and any enclosing instrumented operation.
        """
        root = parent['_root'] if parent else self._new_root(operation)
        public = dict(span_id=uuid.uuid4().hex[:16], trace_id=root['trace_id'],
                      parent_id=parent['span_id'] if parent else None, operation=operation,
                      start_ns=started, end_ns=ended, duration_ms=(ended - started) / 1e6,
                      thread_cpu_ms=cpu_ms, thread_id=threading.get_ident(),
                      capture_policy='completed_if_slow', **attributes)
        event = self._finish(root, public, owns=parent is None)
        if event:
            self.sink(event)

    def _finish(self, root, public, *, owns, active_id=None):
        event = None
        with self.lock:
            if active_id is not None:
                self.active.pop(active_id, None)
            if root['finished']:
                if public['duration_ms'] >= self.slow_ms:
                    event = dict(kind='late_span', request_id=root['request_id'], span=public)
            else:
                root['_seen'] += 1
                child = (public['duration_ms'], -root['_seen'], public)
                if len(root['_children']) < self.max_children:
                    heapq.heappush(root['_children'], child)
                else:
                    # Retain the longest spans, including work occurring late in
                    # a busy root. Equal durations retain the earlier observation.
                    heapq.heappushpop(root['_children'], child)
                    root['dropped_spans'] += 1
                    self.dropped_spans += 1
            if owns:
                root['finished'] = True
                root['spans'] = [row[2] for row in sorted(root.pop('_children'), key=lambda row: -row[1])]
                if public['duration_ms'] >= self.slow_ms:
                    event = {key: value for key, value in root.items() if key != 'finished' and not key.startswith('_')}
                    event.update(kind='trace', start_ns=public['start_ns'], end_ns=public['end_ns'],
                                 start_utc=public.get('start_utc'), duration_ms=public['duration_ms'])
        return event

    def active_snapshot(self, limit=None):
        with self.lock:
            return [{key: value for key, value in span.items() if not key.startswith('_')}
                    for span in islice(self.active.values(), limit)]


def stack_snapshot(frame, limit=16):
    rows = []
    while frame is not None and len(rows) < limit:
        rows.append(dict(file=frame.f_code.co_filename, line=frame.f_lineno, function=frame.f_code.co_name))
        frame = frame.f_back
    return list(reversed(rows))


def backend_pid(connection):
    # Only inspect already-open driver connections; never check out another one.
    driver = getattr(connection, 'driver_connection', connection)
    getter = getattr(driver, 'get_backend_pid', None)
    try:
        return getter() if getter else None
    except Exception:
        return None  # A diagnostic must not mask the original closed-connection error.
