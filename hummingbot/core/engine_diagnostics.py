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
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

OPERATIONS = frozenset({
    'strategy.tick', 'strategy.reports', 'controller.cycle', 'controller.indicator', 'order.submit', 'order.admission', 'order.place',
    'controller.reference', 'controller.proposals', 'controller.actions', 'controller.policy',
    'order.cancel', 'order.terminal', 'order.intent', 'order.intent_batch', 'order.created', 'order.receipt_write',
    'position.checkpoint', 'owner.read',
    'db.sql', 'db.commit', 'db.ping', 'service.observe', 'executor.terminal', 'executor.archive', 'order.proof',
    'shutdown.total', 'shutdown.core', 'shutdown.strategy', 'shutdown.hold',
    'shutdown.cancel', 'shutdown.proof_before', 'shutdown.proof_after',
})
SLOW_MS = {
    'strategy.tick': 250, 'strategy.reports': 250, 'controller.cycle': 1000, 'controller.indicator': 250,
    'controller.reference': 250, 'controller.proposals': 250, 'controller.actions': 250, 'controller.policy': 100,
    'order.submit': 1000, 'order.admission': 1000, 'order.place': 1000,
    'order.intent': 100, 'order.intent_batch': 100, 'order.created': 100, 'order.receipt_write': 100,
    'order.cancel': 1000, 'order.proof': 1000,
    'order.terminal': 250, 'executor.terminal': 250, 'executor.archive': 100, 'position.checkpoint': 250,
    'owner.read': 100, 'db.sql': 100, 'db.commit': 100, 'db.ping': 100, 'service.observe': 1000,
    'shutdown.total': 1000, 'shutdown.core': 1000, 'shutdown.strategy': 1000,
    'shutdown.hold': 1000, 'shutdown.cancel': 1000,
    'shutdown.proof_before': 1000, 'shutdown.proof_after': 1000,
}
SUBMISSION_OPERATIONS = frozenset({
    'order.submit', 'order.admission', 'order.intent', 'order.intent_batch', 'order.place', 'order.created', 'order.receipt_write',
})
_SUBMISSION_TIMING_OPERATIONS = {
    'admission_ms': 'order.submit',
    'admission_slot_wait_ms': 'order.admission',
    'recorder_lock_wait_ms': 'order.admission',
    'ownership_backoff_ms': 'order.admission',
    'ownership_row_check_ms': 'order.intent',
    'final_authority_ms': 'order.place',
    'wire_headers_ms': 'order.place',
    'transport_receipt_ms': 'order.place',
    'tracker_ack_ms': 'order.place',
    'tracker_recorder_wait_ms': 'order.place',
}
_QUOTE_LIMIT = 64
_QUOTE_LINK_TTL_NS = 120_000_000_000
_QUOTE_SLOW_NS = 1_000_000_000
_QUOTE_WARNING_INTERVAL_NS = 30_000_000_000
_QUOTE_REJECTION_CODES = {
    'QuoteRefreshRequired': 'quote_refresh_required',
    'MarketReadinessBlocked': 'market_readiness_blocked',
    'SessionSubmissionBlocked': 'session_submission_blocked',
    'SubmissionBlocked': 'submission_blocked',
}
_QUOTE_HANDOFF_TIMINGS = frozenset({
    'fill_report_at_ns', 'account_wait_at_ns', 'account_ready_at_ns', 'handoff_requested_at_ns',
    'checkpoint_started_at_ns', 'checkpoint_committed_at_ns', 'release_report_at_ns', 'first_proposal_at_ns',
})
_HEARTBEAT_INTERVAL_S = .05
_HEARTBEAT_INTERVAL_NS = 50_000_000
_CPU_STAT_FIELDS = frozenset({'usage_usec', 'nr_periods', 'nr_throttled', 'throttled_usec'})
_ACTIVE = None
_PARENT = ContextVar('engine_diagnostic_parent', default=None)


def active_shutdown_stages():
    """Bounded timeout evidence only; never copy arguments or alter cleanup."""
    tracer = _ACTIVE
    if tracer is None:
        return []
    try:
        now = tracer.clock()
        snapshot = tracer.snapshot()
        active = snapshot['active']
        current = next((row for row in active if row['id'] == _PARENT.get()), {})
        root = current.get('shutdown_id')
        if root is not None:
            stages = [dict(operation=row['operation'], phase='active',
                           elapsed_ms=round(max(0, now - row['at_ns']) / 1e6, 3))
                      for row in active if row.get('shutdown_id') == root and row['operation'].startswith('shutdown.')]
            completed = [dict(operation=row['operation'], phase='completed', elapsed_ms=round(row['duration_ms'], 3))
                         for row in snapshot.get('shutdown_recent', ()) if row.get('shutdown_id') == root]
            return (stages + completed[-max(0, 8 - len(stages)):])[:8]
        return [dict(operation=row['operation'], elapsed_ms=round(max(0, now - row['at_ns']) / 1e6, 3))
                for row in active if row['operation'].startswith('shutdown.')][:8]
    except Exception:
        return []


def _finish(tracer, token, error):
    try:
        tracer.finish(token, error=error)
    except Exception:
        pass


@contextmanager
def submission_timing(field):
    """Accumulate a fixed phase on its existing span without adding ring entries."""
    tracer = _ACTIVE
    parent = _PARENT.get() if tracer is not None else None
    started = None
    if parent is not None:
        try:
            started = tracer.clock()
        except Exception:
            pass

    def finish():
        nonlocal started
        if started is None:
            return
        try:
            elapsed_ms = (tracer.clock() - started) / 1e6
            with tracer.lock:
                row = tracer.active.get(parent)
                if row is not None and row['operation'] == _SUBMISSION_TIMING_OPERATIONS[field]:
                    row[field] = round(row.get(field, 0) + elapsed_ms, 3)
        except Exception:
            pass
        finally:
            started = None

    try:
        yield finish
    finally:
        finish()


@asynccontextmanager
async def timed_submission_lock(lock, field):
    if _ACTIVE is None:
        async with lock:
            yield
        return
    with submission_timing(field) as acquired:
        async with lock:
            acquired()
            yield


def _identity(value):
    if isinstance(value, str) and len(value) <= 256:
        return hashlib.sha256(value.encode()).hexdigest()[:16]
    return None


def _spot_sell_lane(config, *, controller_id=None):
    read = config.get if isinstance(config, dict) else lambda key: getattr(config, key, None)
    if (read('type') != 'position_executor' or read('connector_name') != 'okx'
            or read('use_configured_entry_price') is not True):
        return None
    generation = read('mm_generation')
    identifiers = (read('mm_session_id'), read('controller_id') if controller_id is None else controller_id,
                   read('trading_pair'), read('level_id'))
    if (type(generation) is not int or generation < 0
            or any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in identifiers)
            or not identifiers[-1].startswith('sell_')):
        return None
    return hashlib.sha256(json.dumps((*identifiers, generation), separators=(',', ':')).encode()).hexdigest()[:16]


def _executor_identity(config):
    return _identity(config.get('id') if isinstance(config, dict) else getattr(config, 'id', None))


def observe_spot_sell_fill(executor, order_id):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            lane = _spot_sell_lane(executor.config)
            order = _identity(order_id)
            if lane and order and executor._completed_entry_fill_timestamp() is not None:
                tracer.record_quote_marker('quote.sell_fill', order, lane=lane)
        except Exception:
            pass


def observe_spot_sell_handoff(config, field, order_id=None, *, controller_id=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            lane = _spot_sell_lane(config, controller_id=controller_id)
            if lane:
                executor = _executor_identity(config) if field == 'first_proposal_at_ns' else None
                tracer.record_quote_handoff(lane, field, _identity(order_id), executor=executor)
        except Exception:
            pass


def observe_spot_sell_executor(config, stage, order_id=None, *, reason=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            executor = _executor_identity(config)
            if executor:
                tracer.record_quote_attempt(executor, stage, _identity(order_id), reason=reason)
        except Exception:
            pass


def observe_spot_sell_submit(order_id):
    if _ACTIVE is not None:
        try:
            from hummingbot.core.order_submission_guard import current_executor_scope
            observe_spot_sell_executor(current_executor_scope(), 'submitted', order_id)
        except Exception:
            pass


def observe_spot_sell_entry_stage(executor, field):
    if _ACTIVE is not None:
        try:
            info = executor.custom_info
            ids = info.get('order_ids')
            if info.get('entry_order_filled') is True and isinstance(ids, list) and ids:
                observe_spot_sell_handoff(executor.config, field, ids[0])
        except Exception:
            pass


def observe_spot_sell_report(executors, held_ids):
    if _ACTIVE is None:
        return
    try:
        for info in executors:
            field = 'release_report_at_ns' if info.id in held_ids else 'fill_report_at_ns'
            observe_spot_sell_entry_stage(info, field)
    except Exception:
        pass


def observe_spot_sell_dispatch(request):
    tracer = _ACTIVE
    if tracer is None:
        return None
    try:
        if (request.throttler_limit_id != '/api/v5/trade/order' or request.method.value != 'POST'
                or not request.is_auth_required or not isinstance(request.data, str) or len(request.data) > 2048):
            return None
        from hummingbot.core.order_submission_guard import current_executor_scope
        config = current_executor_scope()
        lane = _spot_sell_lane(config)
        if lane:
            data = json.loads(request.data)
            order = _identity(data.get('clOrdId'))
            if order and data.get('side') == 'sell' and data.get('ordType') == 'post_only':
                marker = tracer.record_quote_marker('quote.sell_send', order, lane=lane, executor=_executor_identity(config))
                if marker is not None:
                    return tracer, marker
    except Exception:
        pass
    return None


def observe_spot_sell_response(dispatch):
    if dispatch is not None:
        try:
            tracer, sent = dispatch
            tracer.record_quote_marker('quote.sell_response', sent['order'], sent=sent)
        except Exception:
            pass


def observe_spot_sell_open(connector, order):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            if (connector.name == 'okx' and order.exchange_order_id
                    and 0 <= order.executed_amount_base < order.amount):
                identity = _identity(order.client_order_id)
                if identity:
                    tracer.record_quote_marker('quote.sell_open', identity)
        except Exception:
            pass


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
                order = getattr(order, 'client_order_id', None) or getattr(order, 'order_id', None)
            return dict(entity=entity, order=order)

        def token(tracer, args, kwargs):
            try:
                metadata = fields(args, kwargs)
                if operation == 'order.submit' and metadata['order']:
                    observe_spot_sell_submit(metadata['order'])
                return tracer.begin(operation, **metadata)
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
        self._shutdown_recent = deque(maxlen=32)
        self._submission_recent = deque(maxlen=64)
        self._quote_fills, self._quote_sends = {}, {}
        self._quote_attempts = {}
        self._slow_quotes = deque(maxlen=8)
        self._dropped_slow_quotes = 0
        self._last_quote_warning_ns = -_QUOTE_WARNING_INTERVAL_NS
        self._submission_completed = self._submission_published = 0
        self._last_submission_emitted_ns = -2_000_000_000
        self.sequence = self.dropped_spans = self.sink_errors = 0
        self.lock, self.stopping = threading.Lock(), threading.Event()
        self.loop_thread_id = self.worker = self.heartbeat_task = None
        self._started = self._closed = False
        self._beat_ns = 0
        self._heartbeat_due_ns = None
        self._late_heartbeat_timer = None
        self._observed_late_timer_due_ns = None
        self._last_missed_timer_emitted_ns = -5_000_000_000
        self._loop_native_tid = None
        self._scheduler_samples = deque(maxlen=4)
        self._scheduler_baseline = None
        self._last_scheduler_sample_ns = -200_000_000
        self._last_emitted_ns = -30_000_000_000
        self._last_slow_emitted_ns = -30_000_000_000
        self._slow, self._slow_dirty = deque(maxlen=8), False
        self._episode = None
        self._sample_episode = self._sampled_heartbeat = None
        self._last_stack_sample_ns = -5_000_000_000
        self.hooks = []

    @staticmethod
    def _log(payload):
        level = (logging.INFO if payload['event'] in
                 ('order_submission_stages', 'engine_loop_sample', 'engine_loop_sample_recovered') else logging.WARNING)
        logging.getLogger(__name__).log(level, 'Engine diagnostic %s', json.dumps(payload, separators=(',', ':')),
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
                root = row['id'] if operation == 'shutdown.total' else self.active.get(row['parent'], {}).get('shutdown_id')
                if root is not None:
                    row['shutdown_id'] = root
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
                if row['operation'].startswith('shutdown.'):
                    self._shutdown_recent.append(dict(row, end_ns=now, duration_ms=duration))
                if row['operation'] in SUBMISSION_OPERATIONS and row['order'] is not None:
                    self._retain_submission(dict(row, end_ns=now, duration_ms=duration, error=bool(error)))
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

    def _retain_submission(self, row):
        self._submission_completed += 1
        row['completion_sequence'] = self._submission_completed
        self._submission_recent.append(row)

    def record_quote_handoff(self, lane, field, order, *, executor=None):
        """Retain first observations for one already sampled, filled level."""
        if field not in _QUOTE_HANDOFF_TIMINGS:
            return
        now = self.clock()
        with self.lock:
            fill = self._quote_fills.get(lane)
            if (self._closed or fill is None or not 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS
                    or not (order == fill[0] or field == 'first_proposal_at_ns' and order is None)):
                return
            timings = fill[2]
            timings.setdefault(field, now)
            if field == 'first_proposal_at_ns' and executor and executor not in self._quote_attempts:
                timings.setdefault('first_proposal_executor', executor)
                timings['proposal_count'] = timings.get('proposal_count', 0) + 1
                if len(self._quote_attempts) >= _QUOTE_LIMIT:
                    del self._quote_attempts[next(iter(self._quote_attempts))]
                self._quote_attempts[executor] = dict(lane=lane, replaces=fill[0], replacement_proposal_at_ns=now)

    def record_quote_attempt(self, executor, stage, order, *, reason=None):
        now = self.clock()
        with self.lock:
            attempt = self._quote_attempts.get(executor)
            if self._closed or attempt is None:
                return
            fill = self._quote_fills.get(attempt['lane'])
            if fill is None or fill[0] != attempt['replaces'] or not 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS:
                return
            timings = fill[2]
            if stage == 'created':
                attempt.setdefault('replacement_created_at_ns', now)
            elif stage == 'submitted' and order and order != attempt.get('order'):
                attempt.update(order=order, replacement_submit_at_ns=now)
                timings['submit_count'] = timings.get('submit_count', 0) + 1
            elif stage == 'rejected' and order and order == attempt.get('order'):
                timings['last_rejection'] = dict(executor=executor, order=order, at_ns=now,
                                                 reason_code=_QUOTE_REJECTION_CODES.get(reason, 'order_failure'))

    def record_quote_marker(self, operation, order, *, lane=None, sent=None, executor=None):
        """Retain opaque quote milestones on the existing watchdog stream."""
        now = self.clock()
        with self.lock:
            if self._closed:
                return None
            evidence = dict(lane=lane, replaces=None)
            if operation == 'quote.sell_fill':
                previous = self._quote_fills.get(lane)
                if previous and previous[0] == order:
                    return None
                if lane not in self._quote_fills and len(self._quote_fills) >= _QUOTE_LIMIT:
                    del self._quote_fills[next(iter(self._quote_fills))]
                self._quote_fills[lane] = (order, now, {})
            elif operation == 'quote.sell_send':
                fill = self._quote_fills.get(lane)
                timings = {}
                evidence.update(fill_at_ns=None, sent_at_ns=now, http_response_at_ns=None)
                if fill and 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS and fill[0] != order:
                    evidence.update(replaces=fill[0], fill_at_ns=fill[1])
                    timings = dict(fill[2])
                    attempt = self._quote_attempts.get(executor)
                    if attempt and (attempt['lane'], attempt['replaces'], attempt.get('order')) == (lane, fill[0], order):
                        timings['replacement_executor'] = executor
                        timings.update({key: value for key, value in attempt.items() if key.endswith('_at_ns')})
                if order not in self._quote_sends and len(self._quote_sends) >= _QUOTE_LIMIT:
                    del self._quote_sends[next(iter(self._quote_sends))]
                self._quote_sends[order] = {**evidence, **timings}
            elif operation == 'quote.sell_open':
                evidence = self._quote_sends.pop(order, None)
                if evidence is None or not 0 <= now - evidence['sent_at_ns'] <= _QUOTE_LINK_TTL_NS:
                    return None
                fill = self._quote_fills.get(evidence['lane'])
                if fill and fill[0] == evidence['replaces']:
                    del self._quote_fills[evidence['lane']]
            elif operation == 'quote.sell_response':
                evidence = {key: sent[key] for key in ('lane', 'replaces', 'fill_at_ns', 'sent_at_ns')}
                evidence['http_response_at_ns'] = now
                pending = self._quote_sends.get(order)
                if pending is not None and pending['sent_at_ns'] == sent['sent_at_ns']:
                    pending['http_response_at_ns'] = now
            else:
                return None
            row = dict(operation=operation, at_ns=now, order=order, **evidence)
            self._retain_submission(row)
            if (operation == 'quote.sell_open' and 'replacement_submit_at_ns' in row and 'first_proposal_at_ns' in row
                    and row['replacement_submit_at_ns'] - row['first_proposal_at_ns'] >= _QUOTE_SLOW_NS):
                if len(self._slow_quotes) == self._slow_quotes.maxlen:
                    self._dropped_slow_quotes += 1
                self._slow_quotes.append(row)
            return row

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
                        shutdown_recent=[dict(row) for row in self._shutdown_recent],
                        submission_recent=[dict(row) for row in self._submission_recent],
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
            return False
        try:
            if len(json.dumps(payload).encode()) > 24000:
                payload['recent'] = payload.get('recent', [])[-16:]
                payload['active'] = payload.get('active', [])[:8]
                payload['stacks'] = payload.get('stacks', [])[:1]
                payload['submission_recent'] = payload.get('submission_recent', [])[-8:]
                payload['truncated'] = True
            if len(json.dumps(payload).encode()) > 24000:
                payload['shutdown_recent'] = payload.get('shutdown_recent', [])[-8:]
                payload['slow'] = payload.get('slow', [])[-4:]
            if len(json.dumps(payload).encode()) <= 24000:
                self.emit(payload)
                return True
        except Exception:
            self.sink_errors += 1
        return False

    def _publish_submissions(self, now):
        # Only the watchdog calls the sink: never log on the native writer or
        # while holding its diagnostic lock. Completion sequence, not span ID,
        # keeps parents that finish after their children have been published.
        if now - self._last_submission_emitted_ns < 2_000_000_000:
            return
        with self.lock:
            first, last = self._submission_published, self._submission_completed
            completed = [dict(row) for row in self._submission_recent if row['completion_sequence'] > first]
        if first == last:
            return
        self._last_submission_emitted_ns = now
        payload = dict(event='order_submission_stages', at_ns=now, wall_time_ns=time.time_ns(), process_id=os.getpid(),
                       completed=completed, from_sequence=first + 1, to_sequence=last,
                       dropped_since_previous=max(0, last - first - len(completed)))
        if self._deliver(payload):
            with self.lock:
                self._submission_published = last

    def _publish_slow_quotes(self, now):
        if not self._slow_quotes or now - self._last_quote_warning_ns < _QUOTE_WARNING_INTERVAL_NS:
            return
        with self.lock:
            replacements = list(self._slow_quotes)
            dropped = self._dropped_slow_quotes
            self._slow_quotes.clear()
            self._dropped_slow_quotes = 0
        self._last_quote_warning_ns = now
        self._deliver(dict(event='spot_sell_replacement_slow', at_ns=now, wall_time_ns=time.time_ns(),
                           process_id=os.getpid(), replacements=replacements, dropped=dropped))

    def _read_scheduler(self):
        if self._loop_native_tid is None:
            return {}
        sample = {}
        try:
            with open('/sys/fs/cgroup/cpu.stat') as source:
                counters = {}
                for line in source:
                    key, value = line.split()
                    if key in _CPU_STAT_FIELDS:
                        counters[key] = int(value)
                if counters:
                    sample['cgroup_cpu'] = counters
        except (OSError, ValueError):
            pass
        try:
            with open(f'/proc/self/task/{self._loop_native_tid}/schedstat') as source:
                running_ns, runqueue_wait_ns, slices = map(int, source.read().split())
                sample['loop_thread'] = dict(running_ns=running_ns, runqueue_wait_ns=runqueue_wait_ns, slices=slices)
        except (OSError, ValueError):
            pass
        return sample

    def _sample_scheduler(self, now):
        if now - self._last_scheduler_sample_ns >= 200_000_000:
            self._last_scheduler_sample_ns = now
            sample = self._read_scheduler()
            if sample:
                self._scheduler_samples.append(dict(at_ns=now, **sample))

    def _loop_recovery_evidence(self, detected_at):
        evidence = {}
        late_timer = self._late_heartbeat_timer
        if late_timer is not None:
            due, callback = late_timer
            if due <= detected_at <= callback:
                self._observed_late_timer_due_ns = due
                evidence['heartbeat_timer'] = dict(due_ns=due, callback_ns=callback,
                                                   overdue_ms=round((callback - due) / 1e6, 3))
        samples = list(self._scheduler_samples)
        if self._scheduler_baseline is not None:
            samples = [self._scheduler_baseline] + [sample for sample in samples[-3:]
                                                    if sample['at_ns'] > self._scheduler_baseline['at_ns']]
        if samples:
            evidence['scheduler_samples'] = samples
        self._scheduler_baseline = None
        return evidence

    def _publish_missed_heartbeat(self, now):
        late_timer = self._late_heartbeat_timer
        if late_timer is None:
            return
        due, callback = late_timer
        if due == self._observed_late_timer_due_ns or callback > now:
            return
        self._observed_late_timer_due_ns = due
        if callback - due < 250_000_000 or now - self._last_missed_timer_emitted_ns < 5_000_000_000:
            return
        # The watchdog may also have been delayed. Preserve the actual timer
        # delay without attributing it to an unrelated stack after recovery.
        self._last_missed_timer_emitted_ns = now
        self._deliver(dict(event='engine_heartbeat_late', at_ns=now, wall_time_ns=time.time_ns(),
                           process_id=os.getpid(), scheduler_samples=list(self._scheduler_samples),
                           heartbeat_timer=dict(due_ns=due, callback_ns=callback,
                                                overdue_ms=round((callback - due) / 1e6, 3))))

    def _capture_loop(self, now, beat, gap_ms, event):
        self._last_stack_sample_ns, self._sampled_heartbeat = now, beat
        payload = dict(event=event, at_ns=now, heartbeat_ns=beat, wall_time_ns=time.time_ns(),
                       heartbeat_gap_ms=gap_ms, process_id=os.getpid(), **self.snapshot())
        due = self._heartbeat_due_ns
        if due is not None and beat < due <= now:
            payload['heartbeat_timer'] = dict(due_ns=due, overdue_ms=round((now - due) / 1e6, 3))
        if self._scheduler_samples:
            payload['scheduler_samples'] = list(self._scheduler_samples)
            self._scheduler_baseline = next(
                (row for row in reversed(self._scheduler_samples) if row['at_ns'] <= (due or beat)), None)
        payload['stacks'] = self._stacks(payload['active'])
        payload['stack_sampled_at_ns'] = self.clock()
        payload['heartbeat_advanced_during_capture'] = self._beat_ns != beat
        self._deliver(payload)

    def watch_once(self):
        if not self._started or self._closed:
            return
        beat, now = self._beat_ns, self.clock()
        self._sample_scheduler(now)
        self._publish_submissions(now)
        self._publish_slow_quotes(now)
        if beat != self._beat_ns:
            return  # The loop advanced while this diagnostic thread was descheduled.
        gap_ms = (now - beat) / 1e6
        if gap_ms < 250:
            if self._episode is not None:
                with self.lock:
                    slow = list(self._slow)
                self._deliver(dict(event='engine_loop_recovered', at_ns=now, heartbeat_ns=beat,
                                   wall_time_ns=time.time_ns(), detected_at_ns=self._episode, process_id=os.getpid(),
                                   slow=slow, **self.snapshot(), **self._loop_recovery_evidence(self._episode)))
                self._episode = None
            if self._sample_episode is not None:
                self._deliver(dict(event='engine_loop_sample_recovered', at_ns=now, heartbeat_ns=beat,
                                   wall_time_ns=time.time_ns(), detected_at_ns=self._sample_episode,
                                   process_id=os.getpid(), **self.snapshot(),
                                   **self._loop_recovery_evidence(self._sample_episode)))
                self._sample_episode = None
            self._publish_missed_heartbeat(now)
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
            if (self._episode is None and self._sampled_heartbeat != beat
                    and now - self._last_stack_sample_ns >= 5_000_000_000):
                self._sample_episode = now
                self._capture_loop(now, beat, gap_ms, 'engine_loop_sample')
            return
        self._last_emitted_ns, self._episode = now, now
        self._sample_episode = None
        self._capture_loop(now, beat, gap_ms, 'engine_loop_stall')

    async def start(self):
        global _ACTIVE
        if self._started or self._closed or _ACTIVE is not None:
            return False
        self.loop_thread_id, self._beat_ns = threading.get_ident(), self.clock()
        if sys.platform == 'linux':
            self._loop_native_tid = threading.get_native_id()
        self._started, _ACTIVE = True, self
        loop = asyncio.get_running_loop()

        def heartbeat_callback(due, ready):
            now = self.clock()
            if now - due >= 100_000_000:
                self._late_heartbeat_timer = (due, now)
            self._beat_ns = now
            if not ready.done():
                ready.set_result(None)

        async def heartbeat():
            while True:
                ready = loop.create_future()
                deadline = loop.time() + _HEARTBEAT_INTERVAL_S
                due = self.clock() + _HEARTBEAT_INTERVAL_NS
                self._heartbeat_due_ns = due
                timer = loop.call_at(deadline, heartbeat_callback, due, ready)
                try:
                    await ready
                finally:
                    timer.cancel()

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
