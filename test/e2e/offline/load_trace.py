"""Bounded, test-only causal traces. Never records SQL parameters or frame locals."""
import contextvars
import hashlib
import inspect
import json
import queue
import re
import threading
import time
import traceback
import uuid
from functools import lru_cache
from pathlib import Path


def instrument_sync(owner, name, key, samples, totals, tracer, attributes=None):
    """Test-only synchronous wall/thread-CPU timing, preserving method binding."""
    descriptor = inspect.getattr_static(owner, name)
    original = descriptor.__func__ if isinstance(descriptor, classmethod) else getattr(owner, name)

    def measured(*args, **kwargs):
        started, cpu = time.perf_counter_ns(), time.thread_time_ns()
        span = tracer.begin(key) if tracer else None
        result, error = None, None
        try:
            result = original(*args, **kwargs)
            return result
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            samples[key].append((time.perf_counter_ns() - started) / 1e6)
            samples[key.removesuffix('_ms') + '_cpu_ms'].append((time.thread_time_ns() - cpu) / 1e6)
            totals[key + '_count'] += 1
            if tracer:
                details = attributes(args, result) if attributes else {}
                tracer.end(span, error=error, **details)
    setattr(owner, name, classmethod(measured) if isinstance(descriptor, classmethod) else measured)


@lru_cache(maxsize=256)
def sql_metadata(statement):
    statement = re.sub(r'(\$[\w]*\$).*?\1', '?', statement, flags=re.S)
    template = re.sub(r"'(?:''|[^'])*'", '?', statement)
    template = re.sub(r'(?<![\w$])\d+(?:\.\d+)?(?!\w)', '?', template)
    template = ' '.join(template.split())[:2500]
    return dict(sql_kind=template.split(' ', 1)[0].upper(), sql_template=template,
                sql_fingerprint=hashlib.sha256(template.encode()).hexdigest()[:20])


class TraceSink:
    def __init__(self, path, *, max_bytes=128 * 1024**2, queue_limit=2048, append=False):
        self.path, self.max_bytes = Path(path), max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue(maxsize=queue_limit)
        self.stopping = threading.Event()
        self.append = append
        self.counts = dict(written_records=0, written_bytes=self.path.stat().st_size if append and self.path.exists() else 0,
                           dropped_records=0, writer_cpu_ms=0)
        self.worker = threading.Thread(target=self._write, name='load-trace-writer', daemon=True)
        self.worker.start()

    def emit(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.counts['dropped_records'] += 1

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
                if self.counts['written_bytes'] + size <= self.max_bytes:
                    output.write(line)
                    self.counts['written_bytes'] += size
                    self.counts['written_records'] += 1
                else:
                    self.counts['dropped_records'] += 1
                self.counts['writer_cpu_ms'] += (time.thread_time_ns() - started) / 1e6
                self.queue.task_done()
                if time.monotonic() - last_flush >= 1:
                    output.flush()
                    last_flush = time.monotonic()

    def summary(self):
        return {**self.counts, 'queue_depth': self.queue.qsize(), 'max_bytes': self.max_bytes,
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

    def begin(self, operation, **attributes):
        parent = self.current.get()
        root = parent['_root'] if parent and not parent['_root']['finished'] else None
        owns = root is None
        if owns:
            root = dict(trace_id=uuid.uuid4().hex, request_id=attributes.get('request_id'),
                        operation=operation, spans=[], finished=False, dropped_spans=0)
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
        root = span['_root']
        event = None
        with self.lock:
            self.active.pop(span['span_id'], None)
            if root['finished']:
                if public['duration_ms'] >= self.slow_ms:
                    event = dict(kind='late_span', request_id=root['request_id'], span=public)
            elif len(root['spans']) < self.max_children:
                root['spans'].append(public)
            else:
                root['dropped_spans'] += 1
                self.dropped_spans += 1
            if span['_owns']:
                root['finished'] = True
                if public['duration_ms'] >= self.slow_ms:
                    event = {key: value for key, value in root.items() if key != 'finished'}
                    event.update(kind='trace', start_ns=span['start_ns'], end_ns=ended,
                                 start_utc=span['start_utc'], duration_ms=public['duration_ms'])
        self.current.reset(span['_token'])
        if event:
            self.sink(event)

    def active_snapshot(self):
        with self.lock:
            return [{key: value for key, value in span.items() if not key.startswith('_')} for span in self.active.values()]


def stack_snapshot(frame, limit=16):
    return [dict(file=row.filename, line=row.lineno, function=row.name)
            for row in traceback.extract_stack(frame, limit=limit)] if frame else []


def backend_pid(connection):
    # Only inspect already-open driver connections; never check out another one.
    driver = getattr(connection, 'driver_connection', connection)
    getter = getattr(driver, 'get_backend_pid', None)
    try:
        return getter() if getter else None
    except Exception:
        return None  # A diagnostic must not mask the original closed-connection error.
