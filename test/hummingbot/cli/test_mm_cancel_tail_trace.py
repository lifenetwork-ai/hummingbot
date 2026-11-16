import asyncio
from test.e2e.offline import load_trace
from test.e2e.offline.load_incidents import IncidentBuffer
from test.e2e.offline.load_trace import DeferredCancelTrace

import pytest


def completed(identity, duration=125):
    return dict(kind='mm_activity', operation='order.cancel', phase='end',
                event_id=identity, duration_ms=duration, at_ns=1_000_000 + identity,
                start_ns=100, order_id=str(identity), connector='okx',
                private_arguments='must not be copied')


def test_cancel_observer_never_reenters_capture_and_retains_slow_tail_history():
    output = []
    capture = IncidentBuffer(output.append)
    observer = DeferredCancelTrace()
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
    observer = DeferredCancelTrace()
    observer.observe(completed(1, 99.9))
    observer.observe({**completed(2), 'phase': 'start'})
    observer.observe({**completed(3), 'operation': 'order.submit'})
    observer.observe(completed(4, 100))
    events = []
    observer.drain(events.append)
    assert [row['event_id'] for row in events] == [4]


def test_cancel_tail_queue_is_bounded_and_reports_dropped_observations():
    observer = DeferredCancelTrace(limit=2)
    for identity in range(3):
        observer.observe(completed(identity))
    assert observer.summary() == dict(pending_events=2, max_events=2, dropped_events=1)
    events = []
    observer.drain(events.append)
    assert [row['event_id'] for row in events] == [1, 2]
    assert observer.summary()['pending_events'] == 0


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
