"""Opt-in bounded observations; no trading authority, raw arguments or SQL text.

The watchdog samples the engine's real loop. A heartbeat gap and a sampled stack
are observations, not proof that the sampled frame caused the whole gap.
"""
import asyncio
import functools
import hashlib
import inspect
import json
import logging
import os
import re
import sys
import threading
import time
import weakref
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

OPERATIONS = frozenset({
    'strategy.tick', 'strategy.reports', 'controller.cycle', 'order.submit',
    'order.cancel', 'order.terminal', 'position.checkpoint', 'owner.read',
    'db.sql', 'db.commit', 'service.observe', 'executor.terminal', 'order.proof',
    'shutdown.total', 'shutdown.core', 'shutdown.strategy', 'shutdown.hold',
    'shutdown.cancel', 'shutdown.proof_before', 'shutdown.proof_after',
})
SLOW_MS = {
    'strategy.tick': 250, 'strategy.reports': 250, 'controller.cycle': 1000,
    'order.submit': 1000, 'order.cancel': 1000, 'order.proof': 1000,
    'order.terminal': 250, 'executor.terminal': 250, 'position.checkpoint': 250,
    'owner.read': 100, 'db.sql': 100, 'db.commit': 100, 'service.observe': 1000,
    'shutdown.total': 1000, 'shutdown.core': 1000, 'shutdown.strategy': 1000,
    'shutdown.hold': 1000, 'shutdown.cancel': 1000,
    'shutdown.proof_before': 1000, 'shutdown.proof_after': 1000,
}
_ACTIVE = None
_PARENT = ContextVar('engine_diagnostic_parent', default=None)


def active_shutdown_stages():
    """Bounded timeout evidence only; never copy arguments or alter cleanup."""
    tracer = _ACTIVE
    if tracer is None:
        return []
    try:
        now = tracer.clock()
        active = tracer.snapshot()['active']
        return [dict(operation=row['operation'], elapsed_ms=round(max(0, now - row['at_ns']) / 1e6, 3))
                for row in active if row['operation'].startswith('shutdown.')][:8]
    except Exception:
        return []


def _finish(tracer, token, error):
    try:
        tracer.finish(token, error=error)
    except Exception:
        pass


def _identity(value):
    if isinstance(value, str) and len(value) <= 256:
        return hashlib.sha256(value.encode()).hexdigest()[:16]
    return None


def trace_runtime(operation, *, order_parameter=None):
    """Disabled path does not inspect arguments; enabled path keeps opaque IDs only."""
    if operation not in OPERATIONS:
        raise ValueError('Unknown diagnostic operation')

    def decorate(function):
        parameters = tuple(inspect.signature(function).parameters)
        index = parameters.index(order_parameter) if order_parameter else None

        def fields(args, kwargs):
            actor = args[0] if args else None
            entity = getattr(getattr(actor, 'config', None), 'id', None)
            order = kwargs.get(order_parameter) if order_parameter in kwargs else (
                args[index] if index is not None and len(args) > index else None)
            if order is not None and not isinstance(order, str):
                order = getattr(order, 'client_order_id', None)
            return dict(entity=entity, order=order)

        def token(tracer, args, kwargs):
            try:
                return tracer.begin(operation, **fields(args, kwargs))
            except Exception:
                return None  # Metadata is never permission to fail a business operation.

        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                tracer = _ACTIVE
                if tracer is None:
                    return await function(*args, **kwargs)
                span = token(tracer, args, kwargs)
                parent = _PARENT.set(span['id'] if span else None)
                error = False
                try:
                    return await function(*args, **kwargs)
                except BaseException:
                    error = True
                    raise
                finally:
                    _PARENT.reset(parent)
                    _finish(tracer, span, error)
            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            tracer = _ACTIVE
            if tracer is None:
                return function(*args, **kwargs)
            span = token(tracer, args, kwargs)
            parent = _PARENT.set(span['id'] if span else None)
            error = False
            try:
                return function(*args, **kwargs)
            except BaseException:
                error = True
                raise
            finally:
                _PARENT.reset(parent)
                _finish(tracer, span, error)
        return synchronous
    return decorate


class EngineDiagnostics:
    def __init__(self, *, emit=None, capacity=96, max_active=32, clock=time.monotonic_ns):
        if not 1 <= capacity <= 128 or not 1 <= max_active <= 64:
            raise ValueError('Diagnostic retention exceeds its fixed bounds')
        self.emit = emit or self._log
        self.clock, self.max_active = clock, max_active
        self.recent, self.active, self.operations = deque(maxlen=capacity), {}, {}
        self.sequence = self.dropped_spans = self.sink_errors = 0
        self.lock, self.stopping = threading.Lock(), threading.Event()
        self.loop_thread_id = self.worker = self.heartbeat_task = None
        self._started = self._closed = False
        self._beat_ns = 0
        self._last_emitted_ns = -30_000_000_000
        self._last_slow_emitted_ns = -30_000_000_000
        self._slow, self._slow_dirty = deque(maxlen=8), False
        self._episode = None
        self.hooks = []

    @staticmethod
    def _log(payload):
        logging.getLogger(__name__).warning('Engine diagnostic %s', json.dumps(payload, separators=(',', ':')),
                                            extra={'event': payload['event'], 'scope': 'runtime'})

    def begin(self, operation, *, entity=None, order=None):
        try:
            if self._closed or operation not in OPERATIONS:
                return None
            now = self.clock()
            with self.lock:
                if len(self.active) >= self.max_active:
                    self.dropped_spans += 1
                    return None
                self.sequence += 1
                row = dict(id=self.sequence, operation=operation, at_ns=now, thread=threading.get_ident(),
                           parent=_PARENT.get(), entity=_identity(entity), order=_identity(order))
                self.active[row['id']] = row
                self.recent.append(dict(row, phase='start'))
                stats = self.operations.setdefault(operation, {
                    'started': 0, 'completed': 0, 'errors': 0, 'last_start_ns': 0,
                    'last_end_ns': None, 'max_duration_ms': 0, 'max_start_gap_ms': 0,
                })
                if stats['started']:
                    stats['max_start_gap_ms'] = max(stats['max_start_gap_ms'], (now - stats['last_start_ns']) / 1e6)
                stats['started'] += 1
                stats['last_start_ns'] = now
                return row
        except Exception:
            return None

    def finish(self, token, *, error=False):
        if token is None:
            return
        try:
            now = self.clock()
            with self.lock:
                row = self.active.pop(token['id'], None)
                if row is None or self._closed:
                    return
                duration = (now - row['at_ns']) / 1e6
                self.recent.append(dict(row, phase='end', end_ns=now, duration_ms=duration, error=bool(error)))
                stats = self.operations[row['operation']]
                stats['completed'] += 1
                stats['errors'] += bool(error)
                stats['last_end_ns'] = now
                stats['max_duration_ms'] = max(stats['max_duration_ms'], duration)
                if duration >= SLOW_MS[row['operation']]:
                    self._slow.append(dict(row, duration_ms=duration, end_ns=now, error=bool(error)))
                    self._slow_dirty = True
        except Exception:
            pass

    @contextmanager
    def span(self, operation, **fields):
        try:
            token = self.begin(operation, **fields)
        except Exception:
            token = None
        parent = _PARENT.set(token['id'] if token else _PARENT.get())
        error = False
        try:
            yield token
        except BaseException:
            error = True
            raise
        finally:
            _PARENT.reset(parent)
            _finish(self, token, error)

    def snapshot(self):
        with self.lock:
            return dict(recent=[dict(row) for row in self.recent], active=[dict(row) for row in self.active.values()],
                        operations={name: dict(stats) for name, stats in self.operations.items()},
                        dropped_spans=self.dropped_spans, sink_errors=self.sink_errors,
                        sql_hooks_attached=bool(self.hooks))

    def _stacks(self, active):
        # Never retain frames, source lines, locals, SQL or request objects.
        identities = [self.loop_thread_id] + sorted({row['thread'] for row in active} - {self.loop_thread_id})[:3]
        frames, result = sys._current_frames(), []
        try:
            for identity in identities:
                frame, stack = frames.get(identity), []
                for _ in range(12):
                    if frame is None:
                        break
                    filename = os.path.basename(frame.f_code.co_filename)
                    name = frame.f_code.co_name
                    stack.append(dict(file=filename if re.fullmatch(r'[\w.-]{1,100}', filename) else '?',
                                      function=name if re.fullmatch(r'[\w<>.]{1,80}', name) else '?',
                                      line=frame.f_lineno))
                    frame = frame.f_back
                result.append(dict(thread=identity, stack=stack[::-1]))
                frame = None
        finally:
            frames.clear()
        return result

    def _deliver(self, payload):
        if self._closed:
            return
        try:
            if len(json.dumps(payload).encode()) > 24000:
                payload['recent'] = payload.get('recent', [])[-16:]
                payload['active'] = payload.get('active', [])[:8]
                payload['stacks'] = payload.get('stacks', [])[:1]
                payload['truncated'] = True
            if len(json.dumps(payload).encode()) <= 24000:
                self.emit(payload)
        except Exception:
            self.sink_errors += 1

    def watch_once(self):
        if not self._started or self._closed:
            return
        beat, now = self._beat_ns, self.clock()
        if beat != self._beat_ns:
            return  # The loop advanced while this diagnostic thread was descheduled.
        gap_ms = (now - beat) / 1e6
        if gap_ms < 250:
            if self._episode is not None:
                self._deliver(dict(event='engine_loop_recovered', at_ns=now, heartbeat_ns=beat,
                                   wall_time_ns=time.time_ns(), detected_at_ns=self._episode, process_id=os.getpid()))
                self._episode = None
            if now - self._last_slow_emitted_ns >= 30_000_000_000:
                with self.lock:
                    slow = list(self._slow) if self._slow_dirty else []
                    self._slow_dirty = False
                if slow:
                    self._last_slow_emitted_ns = now
                    self._deliver(dict(event='engine_stage_slow', at_ns=now, wall_time_ns=time.time_ns(),
                                       heartbeat_ns=beat, heartbeat_gap_ms=gap_ms, slow=slow,
                                       process_id=os.getpid(), **self.snapshot()))
            return
        if now - self._last_emitted_ns < 30_000_000_000:
            return
        self._last_emitted_ns, self._episode = now, now
        payload = dict(event='engine_loop_stall', at_ns=now, heartbeat_ns=beat,
                       wall_time_ns=time.time_ns(), heartbeat_gap_ms=gap_ms, process_id=os.getpid(), **self.snapshot())
        payload['stacks'] = self._stacks(payload['active'])
        payload['stack_sampled_at_ns'] = self.clock()
        payload['heartbeat_advanced_during_capture'] = self._beat_ns != beat
        self._deliver(payload)

    async def start(self):
        global _ACTIVE
        if self._started or self._closed or _ACTIVE is not None:
            return False
        self.loop_thread_id, self._beat_ns = threading.get_ident(), self.clock()
        self._started, _ACTIVE = True, self

        async def heartbeat():
            while True:
                await asyncio.sleep(.05)
                self._beat_ns = self.clock()

        def watch():
            while not self.stopping.wait(.05):
                try:
                    self.watch_once()
                except Exception:
                    self.sink_errors += 1

        self.heartbeat_task = asyncio.create_task(heartbeat(), name='engine-diagnostic-heartbeat')
        self.worker = threading.Thread(target=watch, name='engine-diagnostic-watchdog', daemon=True)
        try:
            self.worker.start()
        except Exception:
            await self.close()
            return False
        return True

    def attach_engine(self, engine):
        """Cursor round trips only; checkpoint spans separately include COMMIT/checkout."""
        if self.hooks or self._closed:
            return
        from sqlalchemy import event
        reference = weakref.ref(self)

        def before(connection, cursor, statement, parameters, context, executemany):
            try:
                tracer = reference()
                if tracer is not None:
                    context._engine_diagnostic_token = tracer.begin('db.sql')
            except Exception:
                pass

        def after(connection, cursor, statement, parameters, context, executemany):
            try:
                token = context.__dict__.pop('_engine_diagnostic_token', None)
                tracer = reference()
                if tracer is not None:
                    tracer.finish(token)
            except Exception:
                pass

        def error(exception_context):
            try:
                context = exception_context.execution_context
                tracer = reference()
                if context is not None and tracer is not None:
                    tracer.finish(context.__dict__.pop('_engine_diagnostic_token', None), error=True)
            except Exception:
                pass

        try:
            for name, callback in (('before_cursor_execute', before), ('after_cursor_execute', after), ('handle_error', error)):
                event.listen(engine, name, callback)
                self.hooks.append((engine, name, callback))
        except Exception:
            self.detach_engine()

    def detach_engine(self):
        from sqlalchemy import event
        hooks, self.hooks = self.hooks, []
        for target, name, callback in hooks:
            try:
                event.remove(target, name, callback)
            except Exception:
                pass

    async def close(self, *, detach=True):
        global _ACTIVE
        self._closed = True
        if _ACTIVE is self:
            _ACTIVE = None
        self.stopping.set()
        if detach:
            self.detach_engine()
        else:
            # Failed engine shutdown may leave a SQL event dispatch in flight.
            # Do not mutate its listener deque. Weak callbacks are now inert and
            # disappear with the engine/process; they cannot retain this tracer.
            self.hooks.clear()
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
        if self.worker is not None and self.worker.ident is not None:
            deadline = time.monotonic() + .2
            while self.worker.is_alive() and time.monotonic() < deadline:
                await asyncio.sleep(.01)  # Never queue teardown behind a saturated DB executor.
        with self.lock:
            self.active.clear()
