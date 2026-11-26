"""Opt-in bounded observations; no trading authority, raw arguments or SQL text.

The watchdog samples the engine's real loop. A heartbeat gap and a sampled stack
are observations, not proof that the sampled frame caused the whole gap.
"""
import asyncio
import functools
import gc
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
    'db.sql', 'db.commit', 'db.ping', 'service.observe', 'service.publication', 'service.checkpoint_write',
    'executor.terminal', 'executor.archive', 'order.proof', 'order.reject',
    'startup.reconcile', 'startup.reference_books', 'startup.history',
    'shutdown.total', 'shutdown.core', 'shutdown.strategy', 'shutdown.hold',
    'shutdown.cancel', 'shutdown.proof_before', 'shutdown.proof_after',
})
SLOW_MS = {
    'strategy.tick': 250, 'strategy.reports': 250, 'controller.cycle': 1000, 'controller.indicator': 250,
    'controller.reference': 250, 'controller.proposals': 250, 'controller.actions': 250, 'controller.policy': 100,
    'order.submit': 1000, 'order.admission': 1000, 'order.place': 1000,
    'order.intent': 100, 'order.intent_batch': 100, 'order.created': 100, 'order.receipt_write': 100,
    'order.cancel': 1000, 'order.proof': 1000, 'order.reject': 100,
    'order.terminal': 250, 'executor.terminal': 250, 'executor.archive': 100, 'position.checkpoint': 250,
    'owner.read': 100, 'db.sql': 100, 'db.commit': 100, 'db.ping': 100, 'service.observe': 1000, 'service.publication': 500,
    'service.checkpoint_write': 100,
    'startup.reconcile': 1000, 'startup.reference_books': 500, 'startup.history': 500,
    'shutdown.total': 1000, 'shutdown.core': 1000, 'shutdown.strategy': 1000,
    'shutdown.hold': 1000, 'shutdown.cancel': 1000,
    'shutdown.proof_before': 1000, 'shutdown.proof_after': 1000,
}
SUBMISSION_OPERATIONS = frozenset({
    'order.submit', 'order.admission', 'order.intent', 'order.intent_batch', 'order.place', 'order.created', 'order.receipt_write',
    'order.reject',
})
_SUBMISSION_TIMING_OPERATIONS = {
    'admission_ms': 'order.submit',
    'admission_slot_wait_ms': 'order.admission',
    'recorder_lock_wait_ms': 'order.admission',
    'ownership_backoff_ms': 'order.admission',
    'ownership_row_check_ms': 'order.intent',
    'final_authority_ms': 'order.place',
    'final_slot_wait_ms': 'order.place',
    'final_owner_read_ms': 'order.place',
    'final_journal_ms': 'order.place',
    'ready_to_dispatch_ms': 'order.place',
    'wire_headers_ms': 'order.place',
    'transport_receipt_ms': 'order.place',
    'receipt_persistence_ms': 'order.place',
    'tracker_ack_ms': 'order.place',
    'tracker_recorder_wait_ms': 'order.place',
}
_LATENCY_SAMPLE_LIMIT = 1024
_RECORDER_WAIT_SLOW_NS = 250_000_000
_RECORDER_WRITE_TYPES = frozenset({'intent', 'final_authority', 'transport_receipt', 'open_ack', 'tracker_update',
                                   'fill', 'position_checkpoint', 'session_update', 'other_write'})
_QUOTE_LIMIT = 64
_QUOTE_LINK_TTL_NS = 120_000_000_000
_QUOTE_SLOW_NS = 1_000_000_000
_QUOTE_PENDING_WARNING_NS = 5_000_000_000
_QUOTE_WARNING_INTERVAL_NS = 30_000_000_000
_QUOTE_SIDES = ('buy', 'sell')
_QUOTE_REJECTION_CODES = {
    'QuoteRefreshRequired': 'quote_refresh_required',
    'MarketReadinessBlocked': 'market_readiness_blocked',
    'SpotOutflowBlocked': 'spot_outflow_limit',
    'SpotOutflowPending': 'spot_outflow_pending',
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
def submission_timing(field, *, finish_on_exit=True):
    """Accumulate a fixed phase on its existing span without adding ring entries."""
    tracer = _ACTIVE
    parent = _PARENT.get() if tracer is not None else None
    started = None
    if parent is not None or (tracer is not None and field == 'ready_to_dispatch_ms'):
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
                if field == 'ready_to_dispatch_ms':
                    tracer._order_dispatch_samples.append(round(elapsed_ms, 3))
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
        if finish_on_exit:
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


class RecorderWriteTiming:
    """Owner-loop history of actual holds on one recorder's write slot."""

    def __init__(self):
        self._tracer = None
        self._holder = None
        self._turns = deque(maxlen=64)
        self._discarded_until = None

    def acquired(self, operation):
        tracer = _ACTIVE
        if tracer is None:
            return
        try:
            if self._tracer is None or self._tracer() is not tracer:
                self._tracer = weakref.ref(tracer)
                self._turns.clear()
                self._discarded_until = None
            self._holder = (tracer.clock(), operation if operation in _RECORDER_WRITE_TYPES else 'other_write')
        except Exception:
            pass

    def released(self):
        holder, self._holder = self._holder, None
        tracer = _ACTIVE
        if holder is None or tracer is None or self._tracer() is not tracer:
            return
        try:
            end = tracer.clock()
            if len(self._turns) == self._turns.maxlen:
                self._discarded_until = self._turns[0][1]
            self._turns.append((holder[0], end, holder[1]))
        except Exception:
            pass

    def begin_wait(self):
        tracer = _ACTIVE
        if tracer is not None and (parent := _PARENT.get()) is not None:
            try:
                return tracer, parent, tracer.clock()
            except Exception:
                pass
        return None

    def report_wait(self, wait):
        if wait is None or self._holder is None:
            return
        tracer, parent, start = wait
        end = self._holder[0]
        if tracer is not _ACTIVE or end - start < _RECORDER_WAIT_SLOW_NS:
            return
        try:
            writers, attributed_ns = {}, 0
            for began, finished, operation in reversed(self._turns):
                if finished <= start:
                    break
                overlap_ns = min(end, finished) - max(start, began)
                if overlap_ns <= 0:
                    continue
                stats = writers.setdefault(operation, [0, 0, 0, 0])
                stats[0] += 1
                stats[1] += overlap_ns
                stats[2] = max(stats[2], overlap_ns)
                stats[3] = max(stats[3], finished - began)
                attributed_ns += overlap_ns
            wait_ms = round((end - start) / 1e6, 3)
            evidence = dict(wait_ms=wait_ms,
                            writers={operation: dict(turns=count, total_ms=round(total / 1e6, 3),
                                                     max_overlap_ms=round(overlap / 1e6, 3), max_hold_ms=round(hold / 1e6, 3))
                                     for operation, (count, total, overlap, hold) in writers.items()},
                            unattributed_ms=round((end - start - attributed_ns) / 1e6, 3),
                            truncated=self._discarded_until is not None and self._discarded_until > start)
            with tracer.lock:
                row = tracer.active.get(parent)
                if row is not None and row['operation'] == 'order.place':
                    # A checkpoint retry can reacquire the slot on the same request.
                    if wait_ms > row.get('final_slot_wait_evidence', {}).get('wait_ms', 0):
                        row['final_slot_wait_evidence'] = evidence
        except Exception:
            pass


def _identity(value):
    if isinstance(value, str) and len(value) <= 256:
        return hashlib.sha256(value.encode()).hexdigest()[:16]
    return None


def _recent_latency(values):
    samples = sorted(values)
    count = len(samples)
    return dict(samples=count, max_ms=samples[-1] if count else None,
                p95_ms=samples[(count * 95 + 99) // 100 - 1] if count else None,
                p99_ms=samples[(count * 99 + 99) // 100 - 1] if count else None)


def _spot_quote_scope(config, *, controller_id=None):
    read = config.get if isinstance(config, dict) else lambda key: getattr(config, key, None)
    if (read('type') != 'position_executor' or read('connector_name') != 'okx'
            or read('use_configured_entry_price') is not True):
        return None
    generation = read('mm_generation')
    identifiers = (read('mm_session_id'), read('controller_id') if controller_id is None else controller_id,
                   read('trading_pair'), read('level_id'))
    if (type(generation) is not int or generation < 0
            or any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in identifiers)):
        return None
    side, separator, _ = identifiers[-1].partition('_')
    if not separator or side not in _QUOTE_SIDES:
        return None
    return (*identifiers[:-1], generation), identifiers[-1], side


def _quote_level_key(controller, level):
    return hashlib.sha256(json.dumps((*controller[:-1], level, controller[-1]), separators=(',', ':')).encode()).hexdigest()[:16]


def _spot_quote_lane(config, *, controller_id=None):
    scope = _spot_quote_scope(config, controller_id=controller_id)
    if scope is not None:
        controller, level, side = scope
        return _quote_level_key(controller, level), side
    return None


def _executor_identity(config):
    return _identity(config.get('id') if isinstance(config, dict) else getattr(config, 'id', None))


def observe_spot_quote_fill(executor, order_id):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            scope = _spot_quote_scope(executor.config)
            order = _identity(order_id)
            if scope and order and executor._completed_entry_fill_timestamp() is not None:
                controller, level, side = scope
                tracer.record_quote_marker(f'quote.{side}_fill', order, lane=_quote_level_key(controller, level),
                                           controller=controller)
        except Exception:
            pass


def observe_spot_quote_handoff(config, field, order_id=None, *, controller_id=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            scope = _spot_quote_lane(config, controller_id=controller_id)
            if scope:
                executor = _executor_identity(config) if field == 'first_proposal_at_ns' else None
                tracer.record_quote_handoff(scope[0], field, _identity(order_id), executor=executor)
        except Exception:
            pass


def observe_spot_quote_controller(config, stage, *, level_id=None, reason=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            if not tracer._quote_fills or config.connector_name != 'okx' or not config.runtime_config_managed:
                return
            controller = (config.mm_session_id, config.id, config.trading_pair, config.mm_generation)
            lane = _quote_level_key(controller, level_id) if level_id is not None else None
            tracer.record_quote_controller(controller, stage, lane=lane, reason=reason)
        except Exception:
            pass


def observe_spot_buy_outflow(config, controller_id, reason, *, used=None, cap=None, required=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            if not tracer._quote_fills:
                return
            scope = _spot_quote_lane(config, controller_id=controller_id)
            if scope and scope[1] == 'buy':
                tracer.record_spot_buy_outflow(scope[0], reason, used=used, cap=cap, required=required)
        except Exception:
            pass


def observe_spot_quote_executor(config, stage, order_id=None, *, reason=None):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            executor = _executor_identity(config)
            if executor:
                tracer.record_quote_attempt(executor, stage, _identity(order_id), reason=reason)
        except Exception:
            pass


def observe_spot_quote_submit(order_id):
    if _ACTIVE is not None:
        try:
            from hummingbot.core.order_submission_guard import current_executor_scope
            observe_spot_quote_executor(current_executor_scope(), 'submitted', order_id)
        except Exception:
            pass


def observe_spot_quote_entry_stage(executor, field):
    if _ACTIVE is not None:
        try:
            info = executor.custom_info
            ids = info.get('order_ids')
            if info.get('entry_order_filled') is True and isinstance(ids, list) and ids:
                observe_spot_quote_handoff(executor.config, field, ids[0])
        except Exception:
            pass


def observe_spot_quote_report(executors, held_ids):
    if _ACTIVE is None:
        return
    try:
        for info in executors:
            field = 'release_report_at_ns' if info.id in held_ids else 'fill_report_at_ns'
            observe_spot_quote_entry_stage(info, field)
    except Exception:
        pass


def observe_spot_quote_dispatch(request):
    tracer = _ACTIVE
    if tracer is None:
        return None
    try:
        if (request.throttler_limit_id != '/api/v5/trade/order' or request.method.value != 'POST'
                or not request.is_auth_required or not isinstance(request.data, str) or len(request.data) > 2048):
            return None
        from hummingbot.core.order_submission_guard import current_executor_scope
        config = current_executor_scope()
        scope = _spot_quote_lane(config)
        if scope:
            lane, side = scope
            data = json.loads(request.data)
            order = _identity(data.get('clOrdId'))
            if order and data.get('side') == side and data.get('ordType') == 'post_only':
                marker = tracer.record_quote_marker(f'quote.{side}_send', order, lane=lane, executor=_executor_identity(config))
                if marker is not None:
                    return tracer, marker
    except Exception:
        pass
    return None


def observe_spot_quote_response(dispatch):
    if dispatch is not None:
        try:
            tracer, sent = dispatch
            tracer.record_quote_marker(f"quote.{sent['side']}_response", sent['order'], sent=sent)
        except Exception:
            pass


def observe_spot_quote_open(connector, order):
    tracer = _ACTIVE
    if tracer is not None:
        try:
            if (connector.name == 'okx' and order.exchange_order_id
                    and 0 <= order.executed_amount_base < order.amount):
                identity = _identity(order.client_order_id)
                if identity:
                    tracer.record_quote_marker('quote.open', identity)
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
                    observe_spot_quote_submit(metadata['order'])
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
        self._order_dispatch_samples = deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        self._quote_fills, self._quote_sends = {}, {}
        self._quote_attempts = {}
        self._quote_samples = {side: deque(maxlen=_LATENCY_SAMPLE_LIMIT) for side in _QUOTE_SIDES}
        self._quote_counts = {side: dict(completed=0, expired=0, superseded=0, evicted=0, unlinked_open=0)
                              for side in _QUOTE_SIDES}
        self._slow_quotes = {side: deque(maxlen=8) for side in _QUOTE_SIDES}
        self._dropped_slow_quotes = dict.fromkeys(_QUOTE_SIDES, 0)
        self._last_quote_warning_ns = dict.fromkeys(_QUOTE_SIDES, -_QUOTE_WARNING_INTERVAL_NS)
        self._last_outflow_blocked_ns = -_QUOTE_WARNING_INTERVAL_NS
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
        self._full_gc_started = None
        self._full_gc_pauses = deque(maxlen=4)
        self._loop_native_tid = None
        self._scheduler_samples = deque(maxlen=4)
        self._scheduler_baseline = None
        self._last_scheduler_sample_ns = -200_000_000
        self._last_emitted_ns = -30_000_000_000
        self._last_slow_emitted_ns = -30_000_000_000
        self._slow, self._unpublished_slow = deque(maxlen=8), 0
        self._episode = None
        self._sample_episode = self._sampled_heartbeat = None
        self._last_stack_sample_ns = -5_000_000_000
        self.hooks = []

    @staticmethod
    def _log(payload):
        level = (logging.INFO if payload['event'] in
                 ('order_submission_stages', 'engine_loop_sample', 'engine_loop_sample_recovered',
                  'spot_buy_replacement_blocked') else logging.WARNING)
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
                if duration >= SLOW_MS[row['operation']] or 'final_slot_wait_evidence' in row:
                    self._slow.append(dict(row, duration_ms=duration, end_ns=now, error=bool(error)))
                    self._unpublished_slow = min(self._unpublished_slow + 1, len(self._slow))
        except Exception:
            pass

    def _retain_submission(self, row):
        self._submission_completed += 1
        row['completion_sequence'] = self._submission_completed
        self._submission_recent.append(row)

    @staticmethod
    def _record_proposal_wait(timings, now, reason):
        history = timings.get('proposal_waits', ())
        if history and history[-1]['reason_code'] == reason:
            timings['proposal_waits'] = (*history[:-1], dict(history[-1], last_observed_at_ns=now))
            return
        if len(history) == 4:
            history = (*history[:1], *history[-2:])
            timings['proposal_waits_dropped'] = timings.get('proposal_waits_dropped', 0) + 1
        timings['proposal_waits'] = (*history, dict(at_ns=now, reason_code=reason))

    def record_quote_controller(self, controller, stage, *, lane=None, reason=None):
        if reason is not None and not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', reason):
            reason = 'proposal_evaluation_unavailable'
        now = self.clock()
        with self.lock:
            if self._closed:
                return
            fills = self._quote_fills.values() if lane is None else (self._quote_fills.get(lane),)
            for fill in fills:
                if fill is None or fill[3] != controller or not 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS:
                    continue
                timings = fill[2]
                released = timings.get('release_report_at_ns')
                if released is None or 'first_proposal_at_ns' in timings:
                    continue
                if stage == 'cycle':
                    previous = timings.get('last_controller_cycle_at_ns', released)
                    timings.setdefault('first_controller_cycle_at_ns', now)
                    timings['last_controller_cycle_at_ns'] = now
                    timings['max_controller_cycle_gap_ms'] = max(timings.get('max_controller_cycle_gap_ms', 0), (now - previous) / 1e6)
                elif stage == 'evaluate':
                    timings.setdefault('first_proposal_evaluation_at_ns', now)
                    timings['last_proposal_evaluation_at_ns'] = now
                elif reason is not None:
                    self._record_proposal_wait(timings, now, reason)

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
            if field == 'first_proposal_at_ns' and 'first_proposal_at_ns' not in timings and timings.get('proposal_waits'):
                self._record_proposal_wait(timings, now, 'proposed')
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

    def record_spot_buy_outflow(self, lane, reason, *, used=None, cap=None, required=None):
        if reason is not None and not re.fullmatch(r'spot_outflow_[a-z0-9_]{1,51}', reason):
            reason = 'spot_outflow_blocked'
        now = self.clock()
        with self.lock:
            fill = self._quote_fills.get(lane)
            if self._closed or fill is None or not 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS:
                return
            timings = fill[2]
            history = timings.get('outflow_preview', ())
            if (history[-1]['reason_code'] if history else None) == reason:
                return
            row = dict(at_ns=now, reason_code=reason)
            if required is not None:
                remaining_quote, required_quote = str(max(0, cap - used)), str(required)
                if max(len(remaining_quote), len(required_quote)) <= 48:
                    row.update(remaining_quote=remaining_quote, required_quote=required_quote)
            # Keep the first denial and the most recent transitions. Assign a
            # new tuple so send/OPEN evidence keeps the snapshot it consumed.
            if len(history) == 4:
                history = (*history[:1], *history[-2:])
                timings['outflow_preview_dropped'] = timings.get('outflow_preview_dropped', 0) + 1
            timings['outflow_preview'] = (*history, row)

    def record_quote_marker(self, operation, order, *, lane=None, sent=None, executor=None, controller=None):
        """Retain opaque quote milestones on the existing watchdog stream."""
        now = self.clock()
        with self.lock:
            if self._closed:
                return None
            side, _, stage = operation.removeprefix('quote.').partition('_')
            evidence = dict(lane=lane, side=side, replaces=None)
            if stage == 'fill':
                previous = self._quote_fills.get(lane)
                if previous and previous[0] == order:
                    return None
                if previous:
                    count = 'expired' if now - previous[1] > _QUOTE_LINK_TTL_NS else 'superseded'
                    self._quote_counts[side][count] += 1
                if lane not in self._quote_fills and len(self._quote_fills) >= _QUOTE_LIMIT:
                    evicted = self._quote_fills.pop(next(iter(self._quote_fills)))
                    self._quote_counts[evicted[2]['side']]['evicted'] += 1
                self._quote_fills[lane] = (order, now, {'side': side}, controller)
            elif stage == 'send':
                fill = self._quote_fills.get(lane)
                timings = {}
                evidence.update(fill_at_ns=None, sent_at_ns=now, http_response_at_ns=None)
                if fill and 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS and fill[0] != order:
                    evidence.update(replaces=fill[0], fill_at_ns=fill[1])
                    fill[2]['last_sent_at_ns'] = now
                    timings = dict(fill[2])
                    attempt = self._quote_attempts.get(executor)
                    if attempt and (attempt['lane'], attempt['replaces'], attempt.get('order')) == (lane, fill[0], order):
                        timings['replacement_executor'] = executor
                        timings.update({key: value for key, value in attempt.items() if key.endswith('_at_ns')})
                if order not in self._quote_sends and len(self._quote_sends) >= _QUOTE_LIMIT:
                    del self._quote_sends[next(iter(self._quote_sends))]
                self._quote_sends[order] = {**evidence, **timings}
            elif stage == 'open' or operation == 'quote.open':
                evidence = self._quote_sends.pop(order, None)
                if evidence is None or not 0 <= now - evidence['sent_at_ns'] <= _QUOTE_LINK_TTL_NS:
                    return None
                side = evidence['side']
                operation = f'quote.{side}_open'
                fill = self._quote_fills.get(evidence['lane'])
                if fill and fill[0] == evidence['replaces'] and 0 <= now - fill[1] <= _QUOTE_LINK_TTL_NS:
                    del self._quote_fills[evidence['lane']]
                    duration = (now - fill[1]) / 1e6
                    evidence['fill_to_open_ms'] = duration
                    self._quote_samples[side].append(duration)
                    self._quote_counts[side]['completed'] += 1
                else:
                    self._quote_counts[side]['unlinked_open'] += 1
            elif stage == 'response':
                evidence = {key: sent[key] for key in ('lane', 'side', 'replaces', 'fill_at_ns', 'sent_at_ns')}
                evidence['http_response_at_ns'] = now
                pending = self._quote_sends.get(order)
                if pending is not None and pending['sent_at_ns'] == sent['sent_at_ns']:
                    pending['http_response_at_ns'] = now
            else:
                return None
            row = dict(operation=operation, at_ns=now, order=order, **evidence)
            self._retain_submission(row)
            if row.get('fill_to_open_ms', 0) >= _QUOTE_SLOW_NS / 1e6:
                quotes = self._slow_quotes[side]
                if len(quotes) == quotes.maxlen:
                    self._dropped_slow_quotes[side] += 1
                quotes.append(row)
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
                        order_dispatch=self._order_dispatch_summary(),
                        quote_replacement=self._quote_replacement_summary(),
                        operations={name: dict(stats) for name, stats in self.operations.items()},
                        dropped_spans=self.dropped_spans, sink_errors=self.sink_errors,
                        sql_hooks_attached=bool(self.hooks))

    def _order_dispatch_summary(self):
        return _recent_latency(self._order_dispatch_samples)

    def _quote_replacement_summary(self):
        return {side: {**self._quote_counts[side], **_recent_latency(values)}
                for side, values in self._quote_samples.items()}

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
                if (payload['event'] in ('spot_buy_replacement_slow', 'spot_sell_replacement_slow')
                        and payload.get('replacements') and payload.get('pending')):
                    # Each quote list is bounded independently. Keep both when
                    # their combined timing evidence exceeds the log limit.
                    replacements = self._deliver(dict(payload, pending=[], pending_count=0))
                    pending = self._deliver(dict(payload, replacements=[], dropped=0))
                    return replacements and pending
                payload['recent'] = payload.get('recent', [])[-16:]
                payload['active'] = payload.get('active', [])[:8]
                payload['stacks'] = payload.get('stacks', [])[:1]
                payload['submission_recent'] = payload.get('submission_recent', [])[-8:]
                payload['truncated'] = True
            if len(json.dumps(payload).encode()) > 24000:
                payload['shutdown_recent'] = payload.get('shutdown_recent', [])[-8:]
                payload['slow'] = payload.get('slow', [])[-4:]
                if payload['event'] == 'order_submission_stages':
                    completed = payload['completed']
                    payload['completed'] = completed[-8:]
                    payload['dropped_since_previous'] += len(completed) - len(payload['completed'])
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
            if first == last:
                return
            completed = [dict(row) for row in self._submission_recent if row['completion_sequence'] > first]
            # Batch writes have no single order ID, so they only appear in the counters.
            operations = {name: {'completed': stats['completed'], 'errors': stats['errors']}
                          for name, stats in self.operations.items() if name in SUBMISSION_OPERATIONS}
            dispatch = self._order_dispatch_summary()
            quote_replacement = self._quote_replacement_summary()
        self._last_submission_emitted_ns = now
        payload = dict(event='order_submission_stages', at_ns=now, wall_time_ns=time.time_ns(), process_id=os.getpid(),
                       operations=operations, completed=completed, order_dispatch=dispatch, quote_replacement=quote_replacement,
                       from_sequence=first + 1, to_sequence=last,
                       dropped_since_previous=max(0, last - first - len(completed)))
        if self._deliver(payload):
            with self.lock:
                self._submission_published = last

    @staticmethod
    def _waiting_on_spot_outflow(timings, filled_at, now):
        waits, previews = timings.get('proposal_waits'), timings.get('outflow_preview')
        return (waits and previews and waits[-1]['reason_code'] == 'spot_outflow_blocked'
                and previews[-1]['reason_code'] == 'spot_outflow_limit'
                and waits[-1]['at_ns'] - filled_at < _QUOTE_SLOW_NS
                and now - waits[-1].get('last_observed_at_ns', waits[-1]['at_ns']) < _QUOTE_PENDING_WARNING_NS)

    def _publish_slow_quotes(self, now):
        sides = {side for side, last in self._last_quote_warning_ns.items() if now - last >= _QUOTE_WARNING_INTERVAL_NS}
        report_blocked = now - self._last_outflow_blocked_ns >= _QUOTE_WARNING_INTERVAL_NS
        if (not sides and not report_blocked) or (not self._quote_fills and not any(self._slow_quotes.values())):
            return
        pending = {side: [] for side in sides}
        blocked = []
        with self.lock:
            for lane, (order, filled_at, timings, _) in list(self._quote_fills.items()):
                side = timings['side']
                age = now - filled_at
                if age > _QUOTE_LINK_TTL_NS:
                    del self._quote_fills[lane]
                    self._quote_counts[side]['expired'] += 1
                if age >= _QUOTE_PENDING_WARNING_NS:
                    waiting_for = ('open' if 'last_sent_at_ns' in timings else
                                   'dispatch' if 'first_proposal_at_ns' in timings else 'proposal')
                    if waiting_for == 'proposal' and self._waiting_on_spot_outflow(timings, filled_at, now):
                        destination = blocked if report_blocked else None
                    else:
                        destination = pending.get(side)
                    if destination is not None:
                        destination.append(dict(lane=lane, order=order, fill_at_ns=filled_at, age_ms=age / 1e6,
                                                waiting_for=waiting_for, expired=age > _QUOTE_LINK_TTL_NS, **timings))
            batches = []
            for side in sides:
                if self._slow_quotes[side] or pending[side]:
                    items = sorted(pending[side], key=lambda row: row['age_ms'], reverse=True)
                    batches.append(dict(event=f'spot_{side}_replacement_slow', at_ns=now, wall_time_ns=time.time_ns(),
                                        process_id=os.getpid(), replacements=list(self._slow_quotes[side]),
                                        pending=items[:8], pending_count=len(items), dropped=self._dropped_slow_quotes[side]))
                    self._last_quote_warning_ns[side] = now
                    self._slow_quotes[side].clear()
                    self._dropped_slow_quotes[side] = 0
            if blocked:
                items = sorted(blocked, key=lambda row: row['age_ms'], reverse=True)
                batches.append(dict(event='spot_buy_replacement_blocked', at_ns=now, wall_time_ns=time.time_ns(),
                                    process_id=os.getpid(), pending=items[:8], pending_count=len(items)))
                self._last_outflow_blocked_ns = now
        for payload in batches:
            self._deliver(payload)

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

    def _record_full_gc(self, phase, info):
        if info['generation'] != 2:
            return
        if phase == 'start':
            self._full_gc_started = self.clock(), time.thread_time_ns()
        elif self._full_gc_started is not None:
            started, cpu = self._full_gc_started
            self._full_gc_started = None
            ended = self.clock()
            if ended - started >= 100_000_000:
                # GC may run while the collector owns a diagnostic lock.
                # Save timing only and let the watchdog do the reporting.
                self._full_gc_pauses.append(dict(start_ns=started, end_ns=ended,
                                                 thread_cpu_ms=round((time.thread_time_ns() - cpu) / 1e6, 3),
                                                 on_loop_thread=threading.get_ident() == self.loop_thread_id,
                                                 collected=info['collected']))

    def _full_gc_evidence(self, due, callback):
        pauses = [pause for pause in tuple(self._full_gc_pauses)
                  if pause['start_ns'] < callback and due < pause['end_ns']]
        return {'full_gc_pauses': pauses} if pauses else {}

    def _loop_recovery_evidence(self, detected_at):
        evidence = {}
        late_timer = self._late_heartbeat_timer
        if late_timer is not None:
            due, callback = late_timer
            if due <= detected_at <= callback:
                self._observed_late_timer_due_ns = due
                evidence['heartbeat_timer'] = dict(due_ns=due, callback_ns=callback,
                                                   overdue_ms=round((callback - due) / 1e6, 3))
                evidence.update(self._full_gc_evidence(due, callback))
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
                                                overdue_ms=round((callback - due) / 1e6, 3)),
                           **self._full_gc_evidence(due, callback)))

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
                    # Keep recent history for loop recovery while reporting each
                    # retained completion only once in periodic slow warnings.
                    slow = list(self._slow)[-self._unpublished_slow:] if self._unpublished_slow else []
                    self._unpublished_slow = 0
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
        gc.callbacks.append(self._record_full_gc)
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
        if self._record_full_gc in gc.callbacks:
            gc.callbacks.remove(self._record_full_gc)
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
