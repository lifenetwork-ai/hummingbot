"""Opt-in offline MM activity measurements; never changes trading decisions."""
import asyncio
import functools
import os
import time
import weakref
from collections import deque

OPERATIONS = ('strategy.tick', 'controller.cycle', 'order.submit', 'order.cancel',
              'executor.shutdown', 'controller.stop_proposal')


class ActivityRecorder:
    def __init__(self, emit, *, limit=256, clock=time.monotonic_ns):
        if not 1 <= limit <= 12000:
            raise ValueError('Activity capacity must be within 1..12000')
        self.emit, self.clock = emit, clock
        self.recent = deque(maxlen=limit)
        self.instances = weakref.WeakKeyDictionary()
        self.sequence = self.next_instance = self.evicted = self.sink_errors = 0
        self.metadata_errors = 0
        self.operations = {}

    def _emit(self, row):
        self.evicted += len(self.recent) == self.recent.maxlen
        self.recent.append(row)
        try:
            self.emit(dict(row))
        except Exception:
            self.sink_errors += 1  # Diagnostic I/O cannot change trading results.

    def start(self, actor, operation, **details):
        if operation not in OPERATIONS:
            raise ValueError('Unknown activity operation')
        if actor not in self.instances:
            self.next_instance += 1
            self.instances[actor] = dict(identity=self.next_instance, previous={})
        instance = self.instances[actor]
        now = self.clock()
        previous = instance['previous'].get(operation)
        instance['previous'][operation] = now
        self.sequence += 1
        gap = (now - previous) / 1e6 if previous is not None else None
        stats = self.operations.setdefault(operation, dict(started=0, completed=0, errors=0, returned_false=0,
                                                           max_duration_ms=0, max_start_gap_ms=0))
        stats['started'] += 1
        if gap is not None:
            stats['max_start_gap_ms'] = max(stats['max_start_gap_ms'], gap)
        row = dict(kind='mm_activity', operation=operation, phase='start', event_id=self.sequence,
                   process_id=os.getpid(), instance=instance['identity'], at_ns=now,
                   previous_start_ns=previous, gap_ms=gap, **details)
        self._emit(row)
        return row

    def finish(self, token, *, error=None, result=None, **details):
        now = self.clock()
        duration = (now - token['at_ns']) / 1e6
        stats = self.operations[token['operation']]
        stats['completed'] += 1
        stats['errors'] += error is not None
        stats['returned_false'] += error is None and result is False
        stats['max_duration_ms'] = max(stats['max_duration_ms'], duration)
        self._emit({**token, **details, 'phase': 'end', 'start_ns': token['at_ns'], 'at_ns': now,
                    'duration_ms': duration,
                    'outcome': error or ('returned_false' if result is False else 'returned')})

    def summary(self):
        return dict(operations={k: dict(v) for k, v in self.operations.items()}, recent=list(self.recent),
                    live_instances=len(self.instances), events_evicted=self.evicted, sink_errors=self.sink_errors,
                    metadata_errors=self.metadata_errors,
                    capacity=self.recent.maxlen)


def instrument_cycle(owner, name, operation, recorder, *, asynchronous=False, details=None, start_details=None):
    original = getattr(owner, name)

    def fields(actor, getter=details):
        try:
            value = getter(actor) if getter else {}
            if not isinstance(value, dict):
                raise TypeError('Activity metadata must be a mapping')
            return value
        except Exception as error:
            recorder.metadata_errors += 1
            return dict(measurement_error=type(error).__name__)

    if asynchronous:
        @functools.wraps(original)
        async def measured(actor, *args, **kwargs):
            token = recorder.start(actor, operation, **fields(actor, start_details))
            result, error = None, None
            try:
                result = await original(actor, *args, **kwargs)
                return result
            except BaseException as exception:
                error = type(exception).__name__
                raise
            finally:
                recorder.finish(token, result=result, error=error, **fields(actor))
    else:
        @functools.wraps(original)
        def measured(actor, *args, **kwargs):
            token = recorder.start(actor, operation, **fields(actor, start_details))
            result, error = None, None
            try:
                result = original(actor, *args, **kwargs)
                return result
            except BaseException as exception:
                error = type(exception).__name__
                raise
            finally:
                recorder.finish(token, result=result, error=error, **fields(actor))
    setattr(owner, name, measured)


def instrument_stop_actions(owner, name, source, recorder):
    """Record nonempty proposals only; do not repeat guard/order evaluation."""
    original = getattr(owner, name)

    @functools.wraps(original)
    def measured(actor, *args, **kwargs):
        result = original(actor, *args, **kwargs)
        if result:
            try:
                token = recorder.start(actor, 'controller.stop_proposal', source=source,
                                       controller_id=actor.config.id,
                                       executor_ids=[action.executor_id for action in result[:24]],
                                       executor_ids_truncated=len(result) > 24)
                recorder.finish(token)
            except Exception:
                recorder.metadata_errors += 1
        return result
    setattr(owner, name, measured)


def instrument_order(owner, name, operation, recorder):
    original = getattr(owner, name)

    @functools.wraps(original)
    async def measured(actor, *args, **kwargs):
        order_id = kwargs.get('order_id', args[0] if args else None)
        token = recorder.start(actor, operation, order_id=order_id, connector=actor.name)
        result, error = None, None
        try:
            result = await original(actor, *args, **kwargs)
            return result
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            recorder.finish(token, result=result, error=error)
    setattr(owner, name, measured)


class VenueQuotes:
    """Only active quote identities and bounded terminal history, never fill history."""

    def __init__(self, *, clock=time.monotonic_ns, limit=256):
        if not 1 <= limit <= 12000:
            raise ValueError('Quote capacity must be within 1..12000')
        self.clock, self.limit = clock, limit
        self.orders, self.terminals = {}, deque(maxlen=limit)
        self.untracked = self.evicted = self.sequence = 0

    def accept(self, order):
        identity = order['clOrdId']
        if identity in self.orders:
            return
        if len(self.orders) >= self.limit:
            self.untracked += 1
            return
        self.orders[identity] = dict(order_id=identity, instrument=order['instId'], side=order['side'],
                                     accepted_ns=self.clock())

    def terminal(self, identity, state, *, request_ns=None):
        now = self.clock()
        self.sequence += 1
        self.evicted += len(self.terminals) == self.terminals.maxlen
        self.orders.pop(identity, None)
        self.terminals.append(dict(sequence=self.sequence, order_id=identity, state=state, at_ns=now,
                                   request_ns=request_ns,
                                   request_to_terminal_ms=(now - request_ns) / 1e6 if request_ns is not None else None))

    def snapshot(self):
        now = self.clock()
        return dict(at_ns=now, process_id=os.getpid(),
                    orders=[{**row, 'age_ms': (now - row['accepted_ns']) / 1e6} for row in self.orders.values()],
                    terminals=list(self.terminals), tracking_complete=self.untracked == 0,
                    untracked_acceptances=self.untracked, terminal_events_evicted=self.evicted,
                    capacity=self.limit)


class VenueSampler:
    """Runs in the load-client process; engine progress is never needed to sample."""

    def __init__(self, read, emit, *, clock=time.monotonic_ns):
        self.read, self.emit, self.clock = read, emit, clock
        self.samples = self.errors = self.clock_errors = self.sink_errors = 0
        self.previous = None
        self.max_gap_ms = 0

    async def sample(self):
        started = self.clock()
        try:
            value = await self.read()
            if (not isinstance(value, dict) or type(value.get('at_ns')) is not int
                    or not isinstance(value.get('orders'), list) or not isinstance(value.get('terminals'), list)
                    or type(value.get('tracking_complete')) is not bool):
                raise ValueError('Invalid venue diagnostic schema')
        except Exception as error:
            self.errors += 1
            row = dict(kind='venue_quote_error', at_ns=self.clock(), request_start_ns=started,
                       error=type(error).__name__)
        else:
            ended = self.clock()
            aligned = started <= value['at_ns'] <= ended
            self.clock_errors += not aligned
            self.samples += 1
            if self.previous is not None:
                self.max_gap_ms = max(self.max_gap_ms, (ended - self.previous) / 1e6)
            self.previous = ended
            row = dict(kind='venue_quote_sample', request_start_ns=started, response_end_ns=ended,
                       clock_aligned=aligned, **value)
        try:
            self.emit(row)
        except Exception:
            self.sink_errors += 1

    async def run(self):
        while True:
            await self.sample()
            await asyncio.sleep(1)

    def summary(self):
        return dict(samples=self.samples, errors=self.errors, clock_errors=self.clock_errors,
                    sink_errors=self.sink_errors, max_sample_gap_ms=self.max_gap_ms,
                    nominal_sample_interval_ms=1000, clock='shared Linux-kernel monotonic clock')
