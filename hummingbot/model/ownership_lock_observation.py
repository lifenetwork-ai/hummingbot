"""Correlate an unsent NOWAIT failure with observed writer transactions."""
from collections import deque
from threading import Lock
from time import monotonic_ns, time_ns

from sqlalchemy import event


class OwnershipLockObservation:
    def __init__(self):
        self._lock = Lock()
        self._active = {}
        self._recent = deque(maxlen=32)

    def acquired(self, connection, cursor, statement, parameters, context, executemany):
        scope = context.execution_options.get('ownership_row_writer')
        if scope is None or cursor.rowcount <= 0:
            return
        driver = connection.connection.driver_connection
        with self._lock:
            # Keep the earliest observed acquisition in the same transaction.
            self._active.setdefault(id(driver), (scope, monotonic_ns()))
            driver.ownership_lock_observation = self

    def finished(self, driver):
        with self._lock:
            scope, acquired = self._active.pop(id(driver))
            self._recent.append((scope, acquired, monotonic_ns()))

    def failure_evidence(self, started):
        finished = monotonic_ns()
        wall_finished = time_ns()
        with self._lock:
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
        return evidence


def observe_ownership_locks(engine):
    observation = OwnershipLockObservation()
    engine.ownership_lock_observation = observation
    event.listen(engine, 'after_cursor_execute', observation.acquired)
