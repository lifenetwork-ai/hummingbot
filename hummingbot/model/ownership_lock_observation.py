"""Correlate an unsent NOWAIT failure with observed writer transactions."""
from collections import deque
from threading import Lock
from time import monotonic_ns, time_ns

from sqlalchemy import event


class OwnershipLockObservation:
    def __init__(self):
        self._lock = Lock()
        self._in_flight = {}
        self._active = {}
        self._recent = deque(maxlen=32)

    def started(self, connection, cursor, statement, parameters, context, executemany):
        scope = context.execution_options.get('ownership_row_writer')
        if scope is not None:
            with self._lock:
                self._in_flight[id(context)] = scope, monotonic_ns()

    def acquired(self, connection, cursor, statement, parameters, context, executemany):
        scope = context.execution_options.get('ownership_row_writer')
        if scope is None:
            return
        with self._lock:
            self._in_flight.pop(id(context), None)
            if cursor.rowcount > 0:
                driver = connection.connection.driver_connection
                # Keep the earliest observed acquisition in the same transaction.
                self._active.setdefault(id(driver), (scope, monotonic_ns()))
                driver.ownership_lock_observation = self

    def failed(self, error):
        context = error.execution_context
        if context is not None and context.execution_options.get('ownership_row_writer') is not None:
            with self._lock:
                self._in_flight.pop(id(context), None)

    def finished(self, driver):
        with self._lock:
            scope, acquired = self._active.pop(id(driver))
            self._recent.append((scope, acquired, monotonic_ns()))

    def failure_evidence(self, started):
        finished = monotonic_ns()
        wall_finished = time_ns()
        with self._lock:
            in_flight = {scope for scope, issued in self._in_flight.values() if issued <= finished}
            overlaps = [(scope, finished - acquired) for scope, acquired in self._active.values()
                        if acquired <= finished]
            overlaps.extend((scope, min(ended, finished) - acquired) for scope, acquired, ended in self._recent
                            if acquired <= finished and ended >= started)
        evidence = {
            'ownership_check_started_at_ns': wall_finished - (finished - started),
            'ownership_check_finished_at_ns': wall_finished,
            'ownership_writer_overlap': ','.join(sorted({scope for scope, _ in overlaps})) or 'unobserved',
        }
        if overlaps:
            evidence['ownership_writer_observed_us'] = max(duration for _, duration in overlaps) // 1000
        if in_flight:
            evidence['ownership_writer_in_flight'] = ','.join(sorted(in_flight))
        return evidence


def observe_ownership_locks(engine):
    observation = OwnershipLockObservation()
    engine.ownership_lock_observation = observation
    event.listen(engine, 'before_cursor_execute', observation.started)
    event.listen(engine, 'after_cursor_execute', observation.acquired)
    event.listen(engine, 'handle_error', observation.failed)
