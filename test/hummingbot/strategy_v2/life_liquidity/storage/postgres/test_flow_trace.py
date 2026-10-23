"""Flow instrumentation preserves execution, task context and exceptions."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_trace import (
    DatabaseSpan,
    FlowSpan,
    QuoteFlowTrace,
)

import pytest


def test_database_capture_window_excludes_proposal_ack_and_reporting(monkeypatch):
    trace = QuoteFlowTrace(monkeypatch)
    trace.spans.extend([FlowSpan(1, 'db_capture', 100, 10, 'success'), FlowSpan(1, 'db_arm', 500, 100, 'success')])
    for phase, at in [('proposal', 50), ('capture', 100), ('source', 300), ('arm', 500), ('ack', 650), ('reporting', 700)]:
        trace.database_spans.append(DatabaseSpan(1, phase, at, 10, (('execute', 8),), 'success'))
    summary, = trace.summaries()
    assert set(summary['database_capture_through_arm']) == {'capture', 'source', 'arm'}
    assert len(summary['database_by_operation']) == 6


def test_background_lease_overlap_does_not_become_serial_flow_cost(monkeypatch):
    trace = QuoteFlowTrace(monkeypatch)
    trace.spans.append(FlowSpan(1, 'hedge_total', 100, 100, 'success'))
    trace.background_leases.extend([
        DatabaseSpan(0, 'process_lease_heartbeat', 80, 40, (('execute', 40),), 'success'),
        DatabaseSpan(0, 'process_lease_heartbeat', 150, 100, (('execute', 100),), 'success'),
        DatabaseSpan(0, 'process_lease_claim', 0, 50, (), 'success'),
    ])
    summary, = trace.summaries()
    assert summary['background_lease_complete']
    assert summary['background_lease_overlap'] == dict(count=2, elapsed_ms=140, overlap_ms=70)
    assert summary['database_by_operation'] == {}
    assert summary['phases']['hedge_total']['elapsed_ms'] == 100


def test_background_lease_ring_is_bounded_and_does_not_evict_financial_flow(monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _Trace
    trace = QuoteFlowTrace(monkeypatch, capacity=2)
    trace.database()
    trace.spans.append(FlowSpan(1, 'quote_total', 0, 1000, 'success'))
    for _ in range(5):
        sample = _Trace('process_lease_heartbeat', True)
        sample.backend_pid = 987654
        sample.finish('success')
    _Trace('private_arbitrary_operation', True).finish('error')
    summary, = trace.summaries()
    assert len(trace.background_leases) == 2 and trace.background_leases_dropped == 3
    assert not summary['background_lease_complete']
    assert 'background_lease_overlap' not in summary
    assert summary['database_complete'] and not trace.database_spans
    assert '987654' not in repr(trace.background_leases) and 'private' not in repr(trace.background_leases)


def test_database_samples_are_bounded_redacted_and_do_not_evict_birth_bounds(monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _Trace
    trace = QuoteFlowTrace(monkeypatch, capacity=2)
    trace.database()
    trace._quote.set(1)
    for _ in range(5):
        sample = _Trace('joint_source_context', True)
        sample.backend_pid = 12345
        sample.enter('execute')
        sample.finish('success')
    trace.spans.extend([FlowSpan(1, 'db_capture', 0, 10, 'success'), FlowSpan(1, 'http_order', 500, 1, 'success')])
    summary, = trace.summaries()
    assert summary['capture_to_http_entry_ms'] == dict(lower=490, upper=500)
    assert len(trace.database_spans) == 2 and trace.database_dropped == 3
    assert summary['database_complete'] is False
    assert summary['database_by_operation']['joint_source_context']['count'] == 2
    assert '12345' not in repr(trace.database_spans)


def test_database_aggregation_preserves_phase_costs_and_original_diagnostic(monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _Trace
    trace = QuoteFlowTrace(monkeypatch)
    trace.database()
    trace.spans.append(FlowSpan(1, 'quote_total', 0, 100, 'error'))
    token = trace._quote.set(1)
    sample = _Trace('spot_state', True)
    sample.enter('execute')
    result = sample.finish('error')
    trace._quote.reset(token)
    summary, = trace.summaries()
    group = summary['database_by_operation']['spot_state']
    assert group['count'] == group['errors'] == 1
    assert group['elapsed_ms'] == round(result.elapsed_ms, 3)
    assert group['phases_ms'] == {k: round(v, 3) for k, v in result.phases_ms}
    assert summary['database_complete'] is True


@pytest.mark.asyncio
async def test_nested_child_spans_keep_quote_context_without_arguments_or_payloads(monkeypatch):
    class Worker:
        async def quote(self, secret):
            return await asyncio.create_task(self.child(secret))

        async def child(self, secret):
            return secret
    trace = QuoteFlowTrace(monkeypatch, capacity=3)
    trace.wrap(Worker, 'quote', 'quote_total', starts_quote=True)
    trace.wrap(Worker, 'child', 'child')
    w = Worker()
    assert await w.quote('never record secret') == 'never record secret'
    assert [v.quote for v in trace.spans] == [1, 1]
    assert [v.phase for v in trace.spans] == ['child', 'quote_total']
    assert all(v.elapsed_ms >= 0 and v.outcome == 'success' for v in trace.spans)
    await w.quote('second secret')
    assert len(trace.spans) == 3 and trace.dropped == 1
    assert 'secret' not in repr(trace.rows())


@pytest.mark.asyncio
async def test_cancellation_and_original_exception_propagate_and_restore_context(monkeypatch):
    error = ValueError('unrecorded driver payload')

    class Worker:
        async def quote(self, fail):
            if fail:
                raise error
            raise asyncio.CancelledError()
    trace = QuoteFlowTrace(monkeypatch)
    trace.wrap(Worker, 'quote', 'quote_total', starts_quote=True)
    with pytest.raises(ValueError) as caught:
        await Worker().quote(True)
    assert caught.value is error and trace._quote.get() == 0
    with pytest.raises(asyncio.CancelledError):
        await Worker().quote(False)
    assert trace._quote.get() == 0
    assert [v.outcome for v in trace.spans] == ['error', 'cancelled']
    assert 'payload' not in repr(trace.rows())


@pytest.mark.asyncio
async def test_http_span_never_records_url_credentials_query_or_body(monkeypatch):
    from types import SimpleNamespace

    async def request(**kwargs):
        return 42
    session = SimpleNamespace(request=request)
    trace = QuoteFlowTrace(monkeypatch)
    trace.http(session)
    assert await session.request(url='https://private:secret@example.org/api/v5/trade/order?token=secret', method='POST', data='secret') == 42
    assert trace.spans[0].phase == 'http_order'
    assert 'secret' not in repr(trace.rows())


@pytest.mark.asyncio
async def test_high_volume_http_detail_cannot_evict_core_quote_phases(monkeypatch):
    from types import SimpleNamespace

    async def request(**kwargs):
        return 42
    session = SimpleNamespace(request=request)

    class Worker:
        async def quote(self):
            for _ in range(10):
                await session.request(url='https://example.org/api/v5/account/config', method='GET')
    trace = QuoteFlowTrace(monkeypatch, capacity=2)
    trace.http(session)
    trace.wrap(Worker, 'quote', 'quote_total', starts_quote=True)
    await Worker().quote()
    assert len(trace.spans) == 1 and trace.spans[0].phase == 'quote_total'
    assert len(trace.details) == 2 and trace.details_dropped == 8 and trace.dropped == 0
    assert len(trace.rows()) == 3 and {v[0] for v in trace.rows()} == {1}


@pytest.mark.asyncio
async def test_source_polling_cannot_evict_capture_or_order_endpoint_bounds(monkeypatch):
    from types import SimpleNamespace

    async def request(**kwargs):
        return 42
    session = SimpleNamespace(request=request)

    class Worker:
        async def capture(self):
            await asyncio.sleep(0)

        async def quote(self):
            await self.capture()
            for _ in range(10):
                await session.request(url='https://example.org/api/v5/account/config', method='GET')
            await session.request(url='https://example.org/api/v5/trade/order', method='POST')
    trace = QuoteFlowTrace(monkeypatch, capacity=3)
    trace.http(session)
    trace.wrap(Worker, 'capture', 'db_capture')
    trace.wrap(Worker, 'quote', 'quote_total', starts_quote=True)
    await Worker().quote()
    summary, = trace.summaries()
    assert trace.details_dropped == 7 and trace.dropped == 0
    assert {v.phase for v in trace.spans} == {'db_capture', 'http_order', 'quote_total'}
    assert summary['retention_complete'] and not summary['details_complete']
    bounds = summary['capture_to_http_entry_ms']
    assert 0 <= bounds['lower'] <= bounds['upper']


def test_summary_bounds_original_birth_between_capture_admission_and_confirmation(monkeypatch):
    trace = QuoteFlowTrace(monkeypatch)
    trace.spans.extend([
        FlowSpan(1, 'quote_total', 0, 1100, 'success'),
        FlowSpan(1, 'prepare_collection', 200, 250, 'success'),
        FlowSpan(1, 'native_context', 200, 80, 'success'),
        FlowSpan(1, 'native_context', 350, 90, 'success'),
    ])
    trace.details.extend([
        FlowSpan(0, 'http_order', 0, 5, 'success'),  # Setup is not a quote.
        FlowSpan(1, 'db_capture', 100, 100, 'success'),
        FlowSpan(1, 'commit_capture', 150, 40, 'success'),
        FlowSpan(1, 'http_order', 1000, 20, 'success'),
    ])
    trace.details_dropped = 1000  # Quota polling cannot evict core DB/HTTP.
    summary, = trace.summaries()
    assert summary['capture_to_http_entry_ms'] == dict(lower=800, upper=900)
    # Nested contexts are separately labelled, not added to the source duration
    # or to either bound. No host/DB wall clock comparison is involved.
    assert summary['phases']['prepare_collection'] == dict(count=1, elapsed_ms=250, errors=0)
    assert summary['phases']['native_context'] == dict(count=2, elapsed_ms=170, errors=0)
    assert summary['phases']['commit_capture'] == dict(count=1, elapsed_ms=40, errors=0)
    assert summary['retention_complete'] is True and summary['details_complete'] is False


def test_failed_arm_has_only_attempt_start_bounds_and_no_invented_http_time(monkeypatch):
    trace = QuoteFlowTrace(monkeypatch)
    trace.details.extend([
        FlowSpan(1, 'db_capture', 100, 100, 'success'),
        FlowSpan(1, 'db_arm', 800, 250, 'error'),
    ])
    summary, = trace.summaries()
    assert summary['capture_to_arm_start_ms'] == dict(lower=600, upper=700)
    assert 'capture_to_http_entry_ms' not in summary
    assert summary['phases']['db_arm']['errors'] == 1


def test_evicted_core_disables_bounds_instead_of_treating_partial_trace_as_complete(monkeypatch):
    trace = QuoteFlowTrace(monkeypatch)
    trace.details.extend([
        FlowSpan(1, 'db_capture', 0, 100, 'success'),
        FlowSpan(1, 'http_order', 500, 10, 'success'),
    ])
    trace.dropped = 1
    summary, = trace.summaries()
    assert summary['retention_complete'] is False
    assert 'capture_to_http_entry_ms' not in summary


@pytest.mark.parametrize('capture', ['absent', 'error', 'multiple', 'overlap'])
def test_missing_uncertain_or_ambiguous_capture_cannot_yield_interval_estimate(monkeypatch, capture):
    trace = QuoteFlowTrace(monkeypatch, capacity=2)
    if capture != 'absent':
        trace.details.append(FlowSpan(1, 'db_capture', 0, 100, 'error' if capture == 'error' else 'success'))
    if capture == 'multiple':
        trace.details.append(FlowSpan(1, 'db_capture', 100, 100, 'success'))
        trace.spans.append(FlowSpan(1, 'http_order', 500, 10, 'success'))
    else:
        trace.details.append(FlowSpan(1, 'http_order', 50 if capture == 'overlap' else 500, 10, 'success'))
    summary, = trace.summaries()
    assert not any(k.startswith('capture_to_') for k in summary)


@pytest.mark.asyncio
async def test_database_spans_only_fixed_operations_keep_commit_errors_and_quote_context(monkeypatch):
    from types import SimpleNamespace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    error = ConnectionError('private driver payload')

    async def admit(self, callback, **kwargs):
        token = _current_trace.set(SimpleNamespace(operation=kwargs.get('operation')))
        try:
            return await callback(self)
        finally:
            _current_trace.reset(token)

    async def commit(self, transaction):
        raise error

    async def read(self):
        return 'secret result'
    monkeypatch.setattr(PgRuntime, '_admit', admit)
    monkeypatch.setattr(PgRuntime, '_commit', commit)
    trace = QuoteFlowTrace(monkeypatch)
    trace.database()
    store = object.__new__(PgRuntime)
    token = trace._quote.set(7)
    try:
        assert await store._admit(read, operation='secret_unknown_operation') == 'secret result'
        assert not trace.spans
        with pytest.raises(ConnectionError) as caught:
            await store._admit(lambda s: s._commit('private transaction'), operation='spot_capture_quote_action')
        assert caught.value is error
    finally:
        trace._quote.reset(token)
    assert [(v.quote, v.phase, v.outcome) for v in trace.spans] == [
        (7, 'commit_capture', 'error'), (7, 'db_capture', 'error')]
    assert 'secret' not in repr(trace.rows()) and 'private' not in repr(trace.rows())
