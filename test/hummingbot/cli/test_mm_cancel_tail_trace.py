import asyncio
from test.e2e.offline import load_trace
from test.e2e.offline.load_incidents import IncidentBuffer
from test.e2e.offline.load_trace import DeferredOrderTrace

import pytest


def completed(identity, duration=125):
    return dict(kind='mm_activity', operation='order.cancel', phase='end',
                event_id=identity, duration_ms=duration, at_ns=1_000_000 + identity,
                start_ns=100, order_id=str(identity), connector='okx',
                private_arguments='must not be copied')


def test_cancel_observer_never_reenters_capture_and_retains_slow_tail_history():
    output = []
    capture = IncidentBuffer(output.append)
    observer = DeferredOrderTrace()
    capture.emit(dict(kind='before_cancel', at_ns=500))
    with capture.lock:
        # The producer can already be inside another recorder's critical section.
        # Only scalar queuing is permitted until the watchdog drains it.
        observer.observe(completed(1))
        assert not output
    observer.drain(capture.trigger)
    assert [row['kind'] for row in output] == ['before_cancel', 'cancel_tail_trigger']
    assert output[-1]['event_id'] == 1 and output[-1]['duration_ms'] == 125
    assert 'private_arguments' not in output[-1]
    observer.drain(capture.trigger)
    assert len(output) == 2


def test_cancel_tail_trigger_filters_short_incomplete_and_unrelated_activity():
    observer = DeferredOrderTrace()
    observer.observe(completed(1, 99.9))
    observer.observe({**completed(2), 'phase': 'start'})
    observer.observe({**completed(3), 'operation': 'strategy.tick'})
    observer.observe(completed(4, 100))
    events = []
    observer.drain(events.append)
    assert [row['event_id'] for row in events] == [4]


def test_cancel_tail_queue_is_bounded_and_reports_dropped_observations():
    observer = DeferredOrderTrace(limit=2)
    for identity in range(3):
        observer.observe(completed(identity))
    assert observer.summary() == dict(pending_events=2, max_events=2, dropped_events=1)
    events = []
    observer.drain(events.append)
    assert [row['event_id'] for row in events] == [1, 2]
    assert observer.summary()['pending_events'] == 0


def test_slow_submit_retains_authorization_sql_context_without_producer_reentry():
    output = []
    capture = IncidentBuffer(output.append)
    observer = DeferredOrderTrace()
    recorder = load_trace.TraceRecorder(capture.emit, slow_ms=0)

    class Gate:
        async def authorize(self, private_argument):
            await asyncio.sleep(0)
            child = recorder.begin('sql', backend_pid=42)
            recorder.end(child)
            return True

    load_trace.instrument_async(Gate, 'authorize', 'ownership.authorize_rest', recorder)
    assert asyncio.run(Gate().authorize('private authorization payload')) is True
    now = load_trace.time.monotonic_ns()
    with capture.lock:
        observer.observe({**completed(1, 331), 'operation': 'order.submit',
                          'at_ns': now, 'start_ns': now - 331_000_000})
    observer.drain(capture.trigger)
    assert [row['kind'] for row in output] == ['trace', 'submit_tail_trigger']
    spans = output[0]['spans']
    parent = next(row for row in spans if row['operation'] == 'ownership.authorize_rest')
    child = next(row for row in spans if row['operation'] == 'sql')
    assert child['parent_id'] == parent['span_id']
    assert child['backend_pid'] == 42
    assert not recorder.active_snapshot()
    assert 'private authorization payload' not in str(output)


def test_submit_stage_trace_preserves_throttler_wait_and_authorization_children(monkeypatch):
    from hummingbot.core.api_throttler.async_request_context_base import AsyncRequestContextBase
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.core.api_throttler.data_types import RateLimit

    output = []
    recorder = load_trace.TraceRecorder(output.append, slow_ms=0)
    result = object()

    class Authority:
        async def authorize_rest(self, private_request):
            sql = recorder.begin('sql', backend_pid=42)
            recorder.end(sql)

    class Connector:
        async def _place_order(self, private_request, throttler):
            async with throttler.execute_task('test-order'):
                await Authority().authorize_rest(private_request)
                return result

    # Restore the real inherited method after this test's instrumentation.
    monkeypatch.setattr(AsyncRequestContextBase, 'acquire', AsyncRequestContextBase.acquire)
    load_trace.instrument_submit_stages(recorder, (Connector,), Authority, AsyncRequestContextBase)

    async def exercise():
        throttler = AsyncThrottler([RateLimit('test-order', limit=10, time_interval=1)])
        await throttler._lock.acquire()
        pending = asyncio.create_task(Connector()._place_order('private auth payload', throttler))
        await asyncio.sleep(0)
        assert not pending.done()
        assert {row['operation'] for row in recorder.active_snapshot()} == {
            'order.submit.Connector', 'order.throttle_wait'}
        throttler._lock.release()
        assert await pending is result
        assert len(throttler._task_logs) == 1

    asyncio.run(exercise())
    spans = output[0]['spans']
    root = next(row for row in spans if row['operation'] == 'order.submit.Connector')
    throttle = next(row for row in spans if row['operation'] == 'order.throttle_wait')
    authority = next(row for row in spans if row['operation'] == 'ownership.authorize_rest')
    sql = next(row for row in spans if row['operation'] == 'sql')
    assert throttle['parent_id'] == authority['parent_id'] == root['span_id']
    assert sql['parent_id'] == authority['span_id']
    assert not recorder.active_snapshot()
    assert 'private auth payload' not in str(output)


@pytest.mark.parametrize('failure', [None, ValueError, asyncio.CancelledError])
def test_async_cancel_span_preserves_result_exception_and_omits_arguments(failure):
    output = []
    recorder = load_trace.TraceRecorder(output.append, slow_ms=0)
    result = object()

    class Connector:
        async def cancel(self, private_argument):
            await asyncio.sleep(0)
            if failure:
                raise failure('private message')
            return result

    load_trace.instrument_async(Connector, 'cancel', 'order.cancel', recorder)

    async def exercise():
        if failure:
            with pytest.raises(failure):
                await Connector().cancel('private payload')
        else:
            assert await Connector().cancel('private payload') is result
    asyncio.run(exercise())
    assert len(output) == 1
    assert output[0]['spans'][0]['operation'] == 'order.cancel'
    assert not recorder.active_snapshot()
    assert 'private' not in str(output)
