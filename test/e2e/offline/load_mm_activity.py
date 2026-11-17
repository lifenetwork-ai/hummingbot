"""Opt-in offline MM activity measurements; never changes trading decisions."""
import asyncio
import functools
import os
import time
import weakref
from collections import deque

OPERATIONS = ('strategy.tick', 'controller.cycle', 'order.submit', 'order.cancel',
              'executor.shutdown', 'controller.stop_proposal', 'order.terminal',
              'position.checkpoint', 'service.stop_core', 'service.prepare', 'service.start')


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
            value = dict(value)
            if 'phase' in value:
                value['service_phase'] = value.pop('phase')
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


def instrument_terminal(owner, name, recorder):
    original = getattr(owner, name)

    @functools.wraps(original)
    def measured(actor, *args, **kwargs):
        event = kwargs.get('event', args[-1] if args else None)
        tracked = getattr(actor, '_order', None)
        identity = getattr(event, 'order_id', None)
        if tracked is None or identity != tracked.order_id:
            return original(actor, *args, **kwargs)
        try:
            details = dict(executor_id=actor.config.id)
        except Exception as error:
            recorder.metadata_errors += 1
            details = dict(measurement_error=type(error).__name__)
        token = recorder.start(actor, 'order.terminal', order_id=identity, callback=name, **details)
        result, error = None, None
        try:
            result = original(actor, *args, **kwargs)
            return result
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            # A callback return is an observation, not terminal/financial authority.
            recorder.finish(token, result=result, error=error)
    setattr(owner, name, measured)


def instrument_checkpoint(owner, name, recorder):
    original = getattr(owner, name)

    @functools.wraps(original)
    def measured(actor, positions, infos, *args, **kwargs):
        try:
            if not isinstance(infos, (tuple, list)):
                raise TypeError('Cannot consume an iterable for diagnostics')
            fields = dict(executor_ids=[info.id for info in infos[:64]], executor_ids_truncated=len(infos) > 64)
        except Exception as error:
            recorder.metadata_errors += 1
            fields = dict(measurement_error=type(error).__name__)
        token = recorder.start(actor, 'position.checkpoint', **fields)
        result, error = None, None
        try:
            result = original(actor, positions, infos, *args, **kwargs)
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
        self.cancel_events = deque(maxlen=limit)
        self.cancel_sequence = self.cancel_evicted = 0
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
                    cancel_events=list(self.cancel_events), cancel_events_evicted=self.cancel_evicted,
                    capacity=self.limit)

    def cancel_request(self, identities, received_ns):
        self.cancel_sequence += 1
        self.cancel_stage(self.cancel_sequence, identities, 'received', at_ns=received_ns)
        return self.cancel_sequence

    def cancel_stage(self, request_id, identities, stage, *, at_ns=None, state=None):
        for identity in identities:
            self.cancel_evicted += len(self.cancel_events) == self.cancel_events.maxlen
            self.cancel_events.append(dict(request_id=request_id, order_id=identity, stage=stage,
                                           at_ns=self.clock() if at_ns is None else at_ns, state=state))


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


class CancelTimeline:
    """Scalar per-attempt markers, independent of slow-span sampling thresholds."""

    def __init__(self, emit, *, clock=time.monotonic_ns):
        import contextvars

        self.emit, self.clock = emit, clock
        self.current = contextvars.ContextVar('cancel_timeline', default=None)
        self.transport = contextvars.ContextVar('cancel_transport', default=None)
        self.sessions = weakref.WeakSet()
        self.sequence = self.events = self.sink_errors = self.metadata_errors = 0
        self.last_event_ns = None

    def metadata(self, connector, order_id):
        if not isinstance(connector, str) or not isinstance(order_id, str) or len(order_id) > 128:
            self.metadata_errors += 1
            return None
        previous = self.current.get()
        if previous and previous[1:] == (connector, order_id):
            return previous
        self.sequence += 1
        return (f'{os.getpid()}:{self.sequence}:{self.clock()}', connector, order_id)

    def mark(self, stage, *, metadata=None, error=None):
        value = metadata if metadata is not None else self.current.get()
        if value is None:
            return
        row = dict(kind='cancel_timeline', stage=stage, at_ns=self.clock(), process_id=os.getpid(),
                   call_id=value[0], connector=value[1], order_id=value[2], error_type=error)
        self.events += 1
        self.last_event_ns = row['at_ns']
        try:
            self.emit(row)
        except Exception:
            self.sink_errors += 1

    @staticmethod
    def cancel_url(value):
        from urllib.parse import urlsplit
        try:
            return urlsplit(str(value)).path == '/api/v5/trade/cancel-order'
        except (TypeError, ValueError):
            return False

    def attach(self, session):
        if session in self.sessions:
            return
        import aiohttp

        config = aiohttp.TraceConfig()

        async def start(_session, context, params):
            context.cancel_metadata = self.current.get() if self.cancel_url(params.url) else None
            if context.cancel_metadata:
                self.mark('request_start', metadata=context.cancel_metadata)

        config.on_request_start.append(start)
        for name, stage in [('on_request_headers_sent', 'headers_prepared'),
                            ('on_request_chunk_sent', 'body_prepared'),
                            ('on_request_end', 'response_headers'),
                            ('on_response_chunk_received', 'body_ready'),
                            ('on_connection_queued_start', 'pool_wait_start'),
                            ('on_connection_queued_end', 'pool_wait_end'),
                            ('on_request_exception', 'request_error')]:
            async def marker(_session, context, _params, stage=stage):
                value = getattr(context, 'cancel_metadata', None)
                if value:
                    self.mark(stage, metadata=value)
            getattr(config, name).append(marker)
        config.freeze()
        # A test-only observer on the already-created native session. No request
        # argument/header/body, timeout, pool or authentication setting changes.
        session._trace_configs.append(config)
        self.sessions.add(session)

    def summary(self):
        return dict(events=self.events, attempts=self.sequence, sink_errors=self.sink_errors,
                    metadata_errors=self.metadata_errors, process_id=os.getpid(),
                    last_event_ns=self.last_event_ns, at_ns=self.clock())


def instrument_cancel_timeline(emit, *, connectors, connection, assistant, response, tracker, authority):
    recorder = CancelTimeline(emit)

    def native(owner, name, stage, identity_index):
        original = getattr(owner, name)

        @functools.wraps(original)
        async def measured(actor, *args, **kwargs):
            order_id = kwargs.get('order_id', args[identity_index] if len(args) > identity_index else None)
            try:
                metadata = recorder.metadata(actor.name, order_id)
            except Exception:
                recorder.metadata_errors += 1
                metadata = None
            token = recorder.current.set(metadata)
            recorder.mark(stage + '_start')
            error = None
            try:
                return await original(actor, *args, **kwargs)
            except BaseException as exception:
                error = type(exception).__name__
                raise
            finally:
                recorder.mark(stage + '_end', error=error)
                recorder.current.reset(token)
        setattr(owner, name, measured)

    for connector in connectors:
        native(connector, '_execute_cancel', 'execute', 1)
        native(connector, '_place_cancel', 'place', 0)

    original_init = connection.__init__

    @functools.wraps(original_init)
    def initialize(actor, *args, **kwargs):
        result = original_init(actor, *args, **kwargs)
        try:
            recorder.attach(actor._client_session)
        except Exception:
            recorder.metadata_errors += 1
        return result
    connection.__init__ = initialize

    def asynchronous(owner, name, start, end):
        original = getattr(owner, name)

        @functools.wraps(original)
        async def measured(actor, *args, **kwargs):
            request = args[0] if args else kwargs.get('request', kwargs.get('response'))
            selected = recorder.cancel_url(getattr(request, 'url', None))
            transport_token = recorder.transport.set(recorder.current.get()) if selected and owner is connection else None
            if selected:
                recorder.mark(start)
            error = None
            try:
                return await original(actor, *args, **kwargs)
            except BaseException as exception:
                error = type(exception).__name__
                raise
            finally:
                if selected:
                    recorder.mark(end, error=error)
                if transport_token is not None:
                    recorder.transport.reset(transport_token)
        setattr(owner, name, measured)

    asynchronous(connection, 'call', 'connection_enter', 'connection_returned')
    asynchronous(assistant, '_post_process_response', 'caller_resumed', 'postprocessed')
    # json() has no positional request: the response itself carries the URL.
    original_json = response.json

    @functools.wraps(original_json)
    async def decoded(actor, *args, **kwargs):
        selected = recorder.cancel_url(actor.url)
        if selected:
            recorder.mark('json_enter')
        error = None
        try:
            return await original_json(actor, *args, **kwargs)
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            if selected:
                recorder.mark('json_returned', error=error)
    response.json = decoded
    asynchronous(authority, 'authorize_rest', 'authorize_start', 'authorize_end')

    # TraceConfig's "headers sent" callback precedes aiohttp's actual write.
    # Bound the native transport enqueue, without reading/copying its bytes or
    # consuming a writelines iterable for a second time. Enqueue is not delivery.
    from aiohttp.http_writer import StreamWriter

    def transport_method(original):
        @functools.wraps(original)
        def written(actor, *args, **kwargs):
            metadata = recorder.transport.get()
            if metadata:
                recorder.mark('transport_enqueue_start', metadata=metadata)
            error = None
            try:
                return original(actor, *args, **kwargs)
            except BaseException as exception:
                error = type(exception).__name__
                raise
            finally:
                if metadata:
                    recorder.mark('transport_enqueue_end', metadata=metadata, error=error)
        return written

    for method in ('_write', '_writelines'):
        if hasattr(StreamWriter, method):
            setattr(StreamWriter, method, transport_method(getattr(StreamWriter, method)))

    original_callback = tracker._trigger_cancelled_event

    @functools.wraps(original_callback)
    def callback(actor, order):
        try:
            metadata = (None, actor._connector.name, order.client_order_id)
        except Exception:
            recorder.metadata_errors += 1
            return original_callback(actor, order)
        recorder.mark('terminal_callback_start', metadata=metadata)
        error = None
        try:
            return original_callback(actor, order)
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            recorder.mark('terminal_callback_end', metadata=metadata, error=error)
    tracker._trigger_cancelled_event = callback
    return recorder


def cancel_timeline_summary(engine, samples, low, high, *, max_calls=256):
    """Join unique physical attempts only; missing/ambiguous evidence stays partial."""
    from collections import Counter, defaultdict

    if type(max_calls) is not int or not 1 <= max_calls <= 12000:
        raise ValueError('Cancel analysis capacity must be within 1..12000')
    groups, callbacks, venue = {}, [], {}
    overflow = invalid_clocks = 0
    for row in engine:
        if row.get('kind') != 'cancel_timeline':
            continue
        key = row.get('call_id')
        if key is None:
            if len(callbacks) < max_calls * 2:
                callbacks.append(row)
            else:
                overflow += 1
        elif key in groups or len(groups) < max_calls:
            groups.setdefault(key, []).append(row)
        else:
            overflow += 1
    for sample in samples:
        if sample.get('clock_aligned') is not True or not (
                sample['request_start_ns'] <= sample['at_ns'] <= sample['response_end_ns']):
            invalid_clocks += 1
            continue
        for row in sample.get('cancel_events', []):
            key = row['order_id'], row['request_id']
            if key not in venue and len(venue) >= max_calls * 2:
                overflow += 1
                continue
            events = venue.setdefault(key, {})
            events[(row['stage'], row['at_ns'])] = row
    prepared, usage = [], Counter()
    for identity, rows in groups.items():
        stages = defaultdict(list)
        for row in rows:
            stages[row['stage']].append(row)
        if not any(low <= row['at_ns'] <= high for row in stages['place_end']):
            continue
        result = dict(call_id=identity, order_id=rows[0]['order_id'], connector=rows[0]['connector'], status='partial')
        required = ('place_start', 'place_end', 'connection_enter', 'response_headers', 'connection_returned', 'caller_resumed')
        if (any(len(stages[name]) != 1 for name in required)
                or not stages['transport_enqueue_start'] or not stages['transport_enqueue_end']
                or not stages['body_ready'] or not stages['json_returned']):
            result['reason'] = 'missing_or_repeated_client_stage'
            prepared.append((result, stages, []))
            continue
        start, end = stages['place_start'][0], stages['place_end'][0]
        if end['at_ns'] < start['at_ns'] or end.get('error_type'):
            result['reason'] = 'failed_or_invalid_native_call'
            prepared.append((result, stages, []))
            continue
        candidates = [key for key, events in venue.items() if key[0] == result['order_id'] and any(
            row['stage'] == 'received' and stages['connection_enter'][0]['at_ns'] <= row['at_ns']
            <= stages['response_headers'][0]['at_ns'] for row in events.values())]
        usage.update(candidates)
        prepared.append((result, stages, candidates))
    results = []
    for result, stages, candidates in prepared:
        results.append(result)
        if result.get('reason'):
            continue
        if overflow or len(candidates) != 1 or usage[candidates[0]] != 1:
            result['reason'] = 'capacity_or_ambiguous_venue_attempt' if candidates else 'venue_attempt_unavailable'
            continue
        events = defaultdict(list)
        for row in venue[candidates[0]].values():
            events[row['stage']].append(row)
        if any(len(events[name]) != 1 for name in ('received', 'terminal_observed', 'response_ready', 'response_eof')):
            result['reason'] = 'missing_or_repeated_venue_stage'
            continue

        def stamp(name):
            return stages[name][0]['at_ns']
        received, terminal, ready, eof = (events[name][0]['at_ns'] for name in
                                          ('received', 'terminal_observed', 'response_ready', 'response_eof'))
        enqueue_start = min(row['at_ns'] for row in stages['transport_enqueue_start'])
        enqueue_end = max(row['at_ns'] for row in stages['transport_enqueue_end'])
        if not (stamp('place_start') <= enqueue_start <= enqueue_end <= stamp('response_headers')
                and enqueue_start <= received <= terminal <= ready <= stamp('response_headers')
                and stamp('response_headers') <= stamp('connection_returned') <= stamp('caller_resumed') <= stamp('place_end')):
            result['reason'] = 'inconsistent_causal_intervals'
            continue
        callback = [row for row in callbacks if row['order_id'] == result['order_id']
                    and row['connector'] == result['connector'] and row['process_id'] == stages['place_start'][0]['process_id']
                    and row['stage'] == 'terminal_callback_end' and row['at_ns'] >= terminal and not row.get('error_type')]
        if len(callback) != 1 or events['terminal_observed'][0]['state'] != 'canceled':
            result['reason'] = 'terminal_callback_unavailable_or_ambiguous'
            continue
        result.update(status='matched', venue_request_id=candidates[0][1],
                      place_start_ns=stamp('place_start'), place_end_ns=stamp('place_end'),
                      place_ms=(stamp('place_end') - stamp('place_start')) / 1e6,
                      pre_transport_ms=(enqueue_start - stamp('place_start')) / 1e6,
                      enqueue_to_venue_bounds_ms=[max(0, received - enqueue_end) / 1e6, (received - enqueue_start) / 1e6],
                      venue_to_terminal_ms=(terminal - received) / 1e6,
                      terminal_to_response_ready_ms=(ready - terminal) / 1e6,
                      response_ready_to_client_headers_ms=(stamp('response_headers') - ready) / 1e6,
                      server_eof_to_client_headers_ms=(stamp('response_headers') - eof) / 1e6,
                      client_headers_to_connection_return_ms=(stamp('connection_returned') - stamp('response_headers')) / 1e6,
                      connection_return_to_caller_ms=(stamp('caller_resumed') - stamp('connection_returned')) / 1e6,
                      caller_resume_to_place_end_ms=(stamp('place_end') - stamp('caller_resumed')) / 1e6,
                      json_read_and_decode_ms=(max(row['at_ns'] for row in stages['json_returned'])
                                               - min(row['at_ns'] for row in stages['json_enter'])) / 1e6,
                      json_return_to_place_end_ms=(stamp('place_end')
                                                   - max(row['at_ns'] for row in stages['json_returned'])) / 1e6,
                      terminal_to_callback_ms=(callback[0]['at_ns'] - terminal) / 1e6,
                      callback_before_native_return=callback[0]['at_ns'] < stamp('place_end'))
    return dict(calls=results, matched=sum(row['status'] == 'matched' for row in results),
                partial=sum(row['status'] != 'matched' for row in results), overflow_events=overflow,
                invalid_venue_clock_samples=invalid_clocks,
                interpretation='Transport enqueue is an interval, not delivery. Server EOF and client header receipt may overlap. '
                'Callback completion and native REST return are distinct; missing stages never mean zero latency.')
