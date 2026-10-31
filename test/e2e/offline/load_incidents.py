"""Test-only bounded flight recorder; no locals, SQL parameters or health changes."""
import json
import threading
import time
from collections import deque
from pathlib import Path


def capture_limit(value):
    """Explicit test-only capacity; disk and in-memory limits stay independent."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Incident capture count must be an integer within 1..128')
    count = int(value)
    if not 1 <= count <= 128:
        raise ValueError('Incident capture count must be an integer within 1..128')
    return count


class IncidentBuffer:
    def __init__(self, sink, *, max_records=256, max_bytes=512 * 1024,
                 history_seconds=10, tail_seconds=10, max_incidents=32):
        self.sink = sink
        self.max_records, self.max_bytes = max_records, max_bytes
        self.history_ns, self.tail_ns = int(history_seconds * 1e9), int(tail_seconds * 1e9)
        self.max_incidents = max_incidents
        self.history = deque()
        self.bytes = 0
        self.until = -1
        self.counts = dict(incidents=0, evicted_records=0, oversize_records=0, suppressed_incidents=0)
        self.lock = threading.Lock()

    def _expire(self, now):
        while self.history and (self.history[0][0] < now - self.history_ns or
                                len(self.history) > self.max_records or self.bytes > self.max_bytes):
            _, line, size = self.history.popleft()
            self.bytes -= size
            self.counts['evicted_records'] += 1

    def emit(self, record):
        now = record.get('at_ns', time.monotonic_ns())
        line = json.dumps({**record, 'at_ns': now}, separators=(',', ':'))
        size = len(line.encode())
        with self.lock:
            if size > self.max_bytes:
                self.counts['oversize_records'] += 1
                return
            self.history.append((now, line, size))
            self.bytes += size
            self._expire(now)
            if now <= self.until:
                self.sink(json.loads(line))

    def trigger(self, event):
        now = event['at_ns']
        with self.lock:
            self._expire(now)
            if now > self.until:
                if self.counts['incidents'] >= self.max_incidents:
                    self.counts['suppressed_incidents'] += 1
                    return
                self.counts['incidents'] += 1
                for _, line, _ in self.history:
                    self.sink(json.loads(line))
            self.until = max(self.until, now + self.tail_ns)
            self.sink({**event, 'capture_incident': self.counts['incidents']})

    def summary(self):
        with self.lock:
            return {**self.counts, 'buffered_records': len(self.history), 'buffered_bytes': self.bytes,
                    'max_records': self.max_records, 'max_bytes': self.max_bytes,
                    'history_seconds': self.history_ns / 1e9, 'tail_seconds': self.tail_ns / 1e9,
                    'max_incidents': self.max_incidents}


class ProbeIncidentPublisher:
    """Publish bounded transitions in shared /tmp from the test supervisor only."""

    def __init__(self, path, *, limit=16):
        self.path = Path(path)
        self.events = deque(maxlen=limit)
        self.states = {}
        self.sequence = 0

    def observe(self, role, healthy, details):
        previous = self.states.get(role)
        if previous is None and not healthy:
            return
        self.states[role] = healthy
        if previous is None or previous == healthy:
            return
        self.sequence += 1
        event = dict(kind='probe_recovered' if healthy else 'probe_failure', sequence=self.sequence,
                     role=role, at=time.time(), at_ns=time.monotonic_ns())
        event.update({key: details[key] for key in ('reason', 'status', 'elapsed_ms') if key in details})
        self.events.append(event)
        temporary = self.path.with_suffix('.new')
        temporary.write_text(json.dumps({'events': list(self.events)}))
        temporary.replace(self.path)
