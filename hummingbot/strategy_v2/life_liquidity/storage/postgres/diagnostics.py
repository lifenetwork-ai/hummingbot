"""Bounded local timings, never an authorization source or a logging callback.

No SQL, arguments, identifiers, exception messages or credentials are retained.
Each physical admission owns a trace; concurrent commands cannot change it.
"""
import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter


@dataclass(frozen=True)
class PgDiagnostic:
    operation: str
    safety: bool
    backend_pid: int | None
    elapsed_ms: float
    phases_ms: tuple[tuple[str, float], ...]
    outcome: str
    error_phase: str | None
    error_kind: str | None
    sqlstate: str | None
    commit_attempted: bool
    commit_confirmed: bool
    caller_cancelled: bool


class _Trace:
    def __init__(self, operation, safety):
        self.operation = operation if type(operation) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', operation) else 'command'
        self.safety = safety
        self.backend_pid = None
        self.started = self.changed = perf_counter()
        self.phase = 'admit'
        self.durations = {}
        self.error_phase = self.error_kind = self.sqlstate = None
        self.error_token = None
        self.commit_attempted = self.commit_confirmed = self.caller_cancelled = False

    def enter(self, name):
        now = perf_counter()
        self.durations[self.phase] = self.durations.get(self.phase, 0.0) + (now - self.changed) * 1000
        self.phase, self.changed = name, now

    def fail(self, error):
        # Called at the raw failure before rollback/error translation. Never use
        # str/repr(error), driver context, query text or an arbitrary class name.
        try:
            state = getattr(error, 'sqlstate', None)
        except Exception:
            state = None
        self.sqlstate = state if type(state) is str and re.fullmatch(r'[0-9A-Z]{5}', state) else None
        self.error_phase = self.phase
        self.error_token = id(error)  # No exception object retained in telemetry.
        self.error_kind = ('postgres' if self.sqlstate else 'timeout' if isinstance(error, TimeoutError)
                           else 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error')

    def finish(self, outcome):
        now = perf_counter()
        self.durations[self.phase] = self.durations.get(self.phase, 0.0) + (now - self.changed) * 1000
        return PgDiagnostic(self.operation, self.safety, self.backend_pid, (now - self.started) * 1000,
                            tuple(self.durations.items()), outcome, self.error_phase, self.error_kind, self.sqlstate,
                            self.commit_attempted, self.commit_confirmed, self.caller_cancelled)


_current_trace = ContextVar('life_pg_physical_trace', default=None)


def phase(name):
    trace = _current_trace.get()
    if trace is not None:
        trace.enter(name)


def failure(error):
    trace = _current_trace.get()
    if trace is not None:
        trace.fail(error)
