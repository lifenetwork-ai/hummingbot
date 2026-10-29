"""Test diagnostics measure physical worker admission without changing dispatch."""
import asyncio
import contextvars
import threading
from collections import defaultdict, deque
from test.e2e.offline import load_trace

import pytest

from hummingbot.cli.admin_database import AdminDatabaseExecutor


def measured(tracer=None):
    class MeasuredExecutor(AdminDatabaseExecutor):
        pass
    samples, totals = defaultdict(lambda: deque(maxlen=2)), defaultdict(int)
    load_trace.instrument_admin_queue(MeasuredExecutor, samples, totals, tracer)
    return MeasuredExecutor(), samples, totals


@pytest.mark.asyncio
async def test_worker_wait_is_measured_before_call_with_request_context_and_bounded_samples():
    records = []
    tracer = load_trace.TraceRecorder(records.append, slow_ms=0)
    root = tracer.begin('http', request_id='diagnostic-request')
    value = contextvars.ContextVar('private_request_value')
    value.set('not-a-trace-attribute')
    executor, samples, totals = measured(tracer)
    ready, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    started = 0
    futures = []

    def blocked():
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                ready.set()
        assert release.wait(5)

    try:
        futures = [executor.submit(blocked) for _ in range(2)]
        assert await asyncio.to_thread(ready.wait, 2)
        queued = executor.submit(lambda: (value.get(), threading.get_ident()))
        futures.append(queued)
        await asyncio.sleep(.025)
        assert not queued.done()
        release.set()
        result = await queued
        await asyncio.gather(*futures)
        assert result[0] == value.get() and result[1] != threading.get_ident()
        assert len(samples['api_worker_queue_ms']) == 2
        assert samples['api_worker_queue_ms'][-1] >= 15
        assert totals['api_worker_queue_count'] == 3
        assert tracer.current.get() is root
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()
        tracer.end(root)
    spans = [span for trace in records for span in trace.get('spans', []) if span['operation'] == 'api_database_worker_start']
    assert len(spans) == 3 and max(span['queue_wait_ms'] for span in spans) >= 15
    assert all(span['request_id'] == 'diagnostic-request' for span in spans)
    assert 'not-a-trace-attribute' not in str(records)


@pytest.mark.asyncio
async def test_cancelled_queued_work_is_not_recorded_as_a_worker_start_and_control_still_runs():
    executor, samples, totals = measured()
    release, ready = threading.Event(), threading.Event()
    lock = threading.Lock()
    started = 0
    futures = []

    def blocked():
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                ready.set()
        assert release.wait(5)

    try:
        futures = [executor.submit(blocked) for _ in range(2)]
        assert await asyncio.to_thread(ready.wait, 2)
        cancelled = executor.submit(lambda: pytest.fail('Cancelled request executed'))
        futures.append(cancelled)
        cancelled.cancel()
        control = executor.submit(lambda: 'accepted', control=True)
        futures.append(control)
        release.set()
        assert await control == 'accepted'
        await asyncio.gather(*futures, return_exceptions=True)
        assert totals['api_worker_queue_count'] == 3
        assert len(samples['api_worker_queue_ms']) == 2
        assert executor.active == executor.pending == 0
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()


@pytest.mark.asyncio
async def test_worker_exception_and_closed_admission_are_preserved():
    executor, _, totals = measured()
    error = ValueError('original failure')

    def fail():
        raise error

    try:
        with pytest.raises(ValueError) as caught:
            await executor.submit(fail)
        assert caught.value is error
        assert totals['api_worker_queue_count'] == 1
    finally:
        await executor.close()
    with pytest.raises(RuntimeError):
        executor.submit(lambda: None)
    assert totals['api_worker_queue_count'] == 1
