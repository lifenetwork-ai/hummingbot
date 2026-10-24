"""Bounded local timings, never an authorization source or a logging callback.

No SQL, arguments, identifiers, exception messages or credentials are retained.
Each physical admission owns a trace; concurrent commands cannot change it.
"""
import asyncio
import json
import math
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
    error_type: str | None = None
    reason_code: str | None = None
    fact_age_ms: float | None = None
    fact_max_age_ms: int | None = None


def _settlement_detail(error):
    """Accept only the closed numeric DETAIL format of the known guard."""
    try:
        message = getattr(error, 'message', None)
        if (getattr(error, 'sqlstate', None) != 'P0001'
                or message not in ('LIFE_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT',
                                   'LIFE_SETTLEMENT_FACTS_UNQUALIFIED')):
            return None
        raw = getattr(error, 'detail', None)
        if type(raw) is not str or len(raw) > 512:
            return None
        value = json.loads(raw)
        expected_format = ('LIFE_SETTLEMENT_COMMIT_V1' if message == 'LIFE_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT'
                           else 'LIFE_SETTLEMENT_FACTS_PRECHECK_V1')
        if (type(value) is not dict or set(value) != {'format', 'reason', 'age_ms', 'max_fact_age_ms'}
                or value['format'] != expected_format):
            return None
        reason, age, maximum = value['reason'], value['age_ms'], value['max_fact_age_ms']
        if (type(reason) is not str or reason not in ('SETTLEMENT_FACTS_MISSING', 'SETTLEMENT_FACTS_NONFINITE',
                                                      'SETTLEMENT_FACTS_FUTURE', 'SETTLEMENT_FACTS_EXPIRED',
                                                      'SETTLEMENT_FACTS_OLDER_THAN_LATEST', 'SETTLEMENT_PROJECTION_INVALID')
                or type(maximum) is not int or not 0 < maximum <= 2147483647):
            return None
        if reason in ('SETTLEMENT_FACTS_MISSING', 'SETTLEMENT_FACTS_NONFINITE'):
            return (reason, None, maximum) if age is None else None
        if type(age) not in (int, float) or abs(age) > 10**18 or not math.isfinite(age):
            return None
        if (reason == 'SETTLEMENT_FACTS_EXPIRED' and age <= maximum
                or reason == 'SETTLEMENT_FACTS_FUTURE' and age >= 0
                or reason == 'SETTLEMENT_FACTS_OLDER_THAN_LATEST' and not 0 <= age <= maximum
                or reason == 'SETTLEMENT_PROJECTION_INVALID' and not 0 <= age <= maximum):
            return None
        return reason, float(age), maximum
    except Exception:
        # Diagnostics may never replace the original database failure.
        return None


class _Trace:
    def __init__(self, operation, safety):
        self.operation = operation if type(operation) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', operation) else 'command'
        self.safety = safety
        self.backend_pid = None
        self.started = self.changed = perf_counter()
        self.phase = 'admit'
        self.durations = {}
        self.error_phase = self.error_kind = self.sqlstate = None
        self.error_type = None
        self.reason_code = self.fact_age_ms = self.fact_max_age_ms = None
        self.pending_failure = False
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
        detail = _settlement_detail(error)
        self.reason_code, self.fact_age_ms, self.fact_max_age_ms = detail if detail is not None else (None, None, None)
        self.pending_failure = True
        self.error_kind = ('postgres' if self.sqlstate else 'timeout' if isinstance(error, TimeoutError)
                           else 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error')
        # A closed vocabulary distinguishes a Python/driver fault from a
        # timeout without serializing arbitrary exception names or messages.
        known = (TimeoutError, ConnectionError, TypeError, ValueError, KeyError, AssertionError, RuntimeError,
                 asyncio.CancelledError)
        self.error_type = next((kind.__name__ for kind in known if isinstance(error, kind)), 'OtherError')
        kind = type(error)
        if (kind.__module__ in ('asyncpg.exceptions', 'asyncpg.exceptions._base') and kind.__name__ in (
                'InterfaceError', 'ConnectionDoesNotExistError', 'ConnectionFailureError', 'QueryCanceledError',
                'SerializationError', 'DeadlockDetectedError', 'PostgresError')):
            self.error_type = kind.__name__

    def finish(self, outcome):
        now = perf_counter()
        self.durations[self.phase] = self.durations.get(self.phase, 0.0) + (now - self.changed) * 1000
        return PgDiagnostic(self.operation, self.safety, self.backend_pid, (now - self.started) * 1000,
                            tuple(self.durations.items()), outcome, self.error_phase, self.error_kind, self.sqlstate,
                            self.commit_attempted, self.commit_confirmed, self.caller_cancelled, self.error_type,
                            self.reason_code, self.fact_age_ms, self.fact_max_age_ms)


_current_trace = ContextVar('life_pg_physical_trace', default=None)


def phase(name):
    trace = _current_trace.get()
    if trace is not None:
        trace.enter(name)


def failure(error):
    trace = _current_trace.get()
    if trace is not None:
        trace.fail(error)
