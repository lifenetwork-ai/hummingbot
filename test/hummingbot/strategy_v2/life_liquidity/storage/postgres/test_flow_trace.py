"""Flow instrumentation preserves execution, task context and exceptions."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_trace import QuoteFlowTrace

import pytest


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
    assert trace.details[0].phase == 'http_order'
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
