"""Native aiohttp transport/callback markers must preserve cancel semantics."""
import asyncio
import json
from test.e2e.offline import load_mm_activity as activity
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.http_writer import StreamWriter
from aiohttp.test_utils import TestServer

from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTResponse
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant


@pytest.mark.asyncio
async def test_actual_transport_can_finish_terminal_callback_before_rest_ack_without_changing_request(monkeypatch):
    rows, payloads = [], []
    terminal, ack = asyncio.Event(), asyncio.Event()
    result, callback_result = object(), object()

    class Authority:
        async def authorize_rest(self, request):
            return None

    authority = Authority()

    async def before(request):
        return await authority.authorize_rest(request)

    monkeypatch.setattr('hummingbot.core.order_submission_guard.before_rest', before)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.after_rest', AsyncMock())

    class Connector:
        name = 'okx'

        async def _place_cancel(self, order_id, order):
            body = await assistant.execute_request(
                url=str(server.make_url('/api/v5/trade/cancel-order')), method=RESTMethod.POST,
                throttler_limit_id='cancel', data={'clOrdId': order_id},
                headers={'Private-Header': 'secret-not-in-trace'})
            assert body == {'code': '0'}
            return result

        async def _execute_cancel(self, trading_pair, order_id):
            return await self._place_cancel(order_id, SimpleNamespace(client_order_id=order_id))

    connector = Connector()

    class Tracker:
        _connector = connector

        def _trigger_cancelled_event(self, order):
            terminal.set()
            return callback_result

    tracker = Tracker()

    async def handler(request):
        payloads.append((request.headers['Private-Header'], await request.json()))
        assert tracker._trigger_cancelled_event(SimpleNamespace(client_order_id='order-one')) is callback_result
        await ack.wait()
        return web.json_response({'code': '0'})

    for owner, method in [(RESTConnection, '__init__'), (RESTConnection, 'call'),
                          (RESTAssistant, '_post_process_response'), (RESTResponse, 'json'),
                          (StreamWriter, '_write'), (StreamWriter, '_writelines')]:
        monkeypatch.setattr(owner, method, getattr(owner, method))
    install = getattr(activity, 'instrument_cancel_timeline', lambda *args, **kwargs: None)
    timeline = install(rows.append, connectors=(Connector,), connection=RESTConnection,
                       assistant=RESTAssistant, response=RESTResponse, tracker=Tracker, authority=Authority)
    app = web.Application()
    app.router.add_post('/api/v5/trade/cancel-order', handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        assistant = RESTAssistant(RESTConnection(session), AsyncThrottler([RateLimit('cancel', 10, 1)]))
        pending = asyncio.create_task(connector._execute_cancel('LIFE-USDT', 'order-one'))
        try:
            await asyncio.wait_for(terminal.wait(), 3)
            assert not pending.done()
        finally:
            ack.set()
        assert await pending is result
    stages = {row['stage'] for row in rows}
    assert {'place_start', 'headers_prepared', 'transport_enqueue_start', 'transport_enqueue_end', 'response_headers', 'connection_returned', 'caller_resumed',
            'body_ready', 'json_returned', 'place_end', 'terminal_callback_end'} <= stages
    assert payloads == [('secret-not-in-trace', {'clOrdId': 'order-one'})]
    assert 'secret-not-in-trace' not in json.dumps(rows)
    by_stage = {row['stage']: row for row in rows}
    assert by_stage['terminal_callback_end']['at_ns'] < by_stage['response_headers']['at_ns']
    assert by_stage['connection_returned']['at_ns'] <= by_stage['caller_resumed']['at_ns']
    call_id = by_stage['place_start']['call_id']
    assert all(row['call_id'] == call_id for row in rows if not row['stage'].startswith('terminal_callback'))
    assert timeline.summary()['sink_errors'] == 0
    # Keyword call shapes are preserved too; these are unrelated to any cancel.
    response = SimpleNamespace(url='https://offline.invalid/public')
    assert await assistant._post_process_response(response=response) is response


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, ValueError, asyncio.CancelledError])
@pytest.mark.parametrize('broken_sink', [False, True])
async def test_cancel_timeline_preserves_results_failures_and_clears_context(monkeypatch, failure, broken_sink):
    rows = []
    result = object()

    class Connector:
        name = 'okx'

        async def _place_cancel(self, order_id, order):
            await asyncio.sleep(0)
            if failure:
                raise failure('private-message')
            return result

        async def _execute_cancel(self, trading_pair, order_id):
            return await self._place_cancel(order_id, None)

    class Connection:
        def __init__(self, session):
            pass

        async def call(self, request):
            return result

    class Assistant:
        async def _post_process_response(self, response):
            return response

    class Response:
        async def json(self):
            return result

    class Tracker:
        def _trigger_cancelled_event(self, order):
            return result

    class Authority:
        async def authorize_rest(self, request):
            return result

    for name in ('_write', '_writelines'):
        monkeypatch.setattr(StreamWriter, name, getattr(StreamWriter, name))

    def emit(row):
        if broken_sink:
            raise OSError('test sink failure')
        rows.append(row)

    timeline = activity.instrument_cancel_timeline(emit, connectors=(Connector,), connection=Connection,
                                                   assistant=Assistant, response=Response,
                                                   tracker=Tracker, authority=Authority)
    if failure:
        with pytest.raises(failure, match='private-message'):
            await Connector()._execute_cancel('LIFE-USDT', 'one')
    else:
        results = await asyncio.gather(*(Connector()._execute_cancel('LIFE-USDT', key) for key in ('one', 'two')))
        assert all(value is result for value in results)
    assert timeline.current.get() is timeline.transport.get() is None
    if broken_sink:
        assert timeline.summary()['sink_errors'] > 0
    else:
        assert rows[-1]['stage'] == 'execute_end'
        assert rows[-1]['error_type'] == (failure.__name__ if failure else None)
        by_order = {row['order_id']: row['call_id'] for row in rows}
        assert all(row['call_id'] == by_order[row['order_id']] for row in rows)
        if failure is None:
            assert len(set(by_order.values())) == 2
    assert 'private-message' not in json.dumps(rows)


def test_cancel_venue_history_is_bounded_and_retries_have_separate_identity():
    clock = iter(range(100, 1000)).__next__
    venue = activity.VenueQuotes(limit=4, clock=clock)
    first = venue.cancel_request(('same',), 90)
    venue.cancel_stage(first, ('same',), 'terminal_observed', state='canceled')
    venue.cancel_stage(first, ('same',), 'response_ready')
    second = venue.cancel_request(('same',), 95)
    venue.cancel_stage(second, ('same',), 'response_eof')
    snapshot = venue.snapshot()
    assert second != first and len(snapshot['cancel_events']) == 4
    assert snapshot['cancel_events_evicted'] == 1
    assert {row['request_id'] for row in snapshot['cancel_events']} == {first, second}


@pytest.mark.asyncio
async def test_actual_simulator_records_response_eof_without_changing_cancel_body():
    from test.hummingbot.cli.test_mm_activity_exchange import exchange, request as signed_request

    venue = exchange()
    await venue.control({'mm_activity': True})
    await venue.handle(signed_request('/api/v5/trade/order', dict(
        clOrdId='one', instId='LIFE-USDT', side='buy', sz='1', px='.1')))
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', venue.handle)
    signed = signed_request('/api/v5/trade/cancel-order', {'clOrdId': 'one'})
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        async with session.post(server.make_url(signed.path), data=await signed.text(), headers=signed.headers) as response:
            body = await response.json()
        assert body['code'] == '0' and body['data'][0]['clOrdId'] == 'one'
        await asyncio.sleep(0)
    assert [row['stage'] for row in venue.quote_activity.snapshot()['cancel_events']] == [
        'received', 'terminal_observed', 'response_ready', 'response_eof']


def timeline_fixture():
    def event(stage, millis, call_id='one', order_id='same'):
        return dict(kind='cancel_timeline', stage=stage, at_ns=millis * 1000000,
                    call_id=call_id, order_id=order_id, connector='okx', process_id=1, error_type=None)
    stages = [('place_start', 100), ('connection_enter', 110), ('transport_enqueue_start', 115),
              ('transport_enqueue_end', 125), ('response_headers', 170), ('connection_returned', 180),
              ('caller_resumed', 210), ('json_enter', 215), ('body_ready', 220),
              ('json_returned', 225), ('place_end', 230)]
    engine = [event(stage, stamp) for stage, stamp in stages]
    engine += [event('terminal_callback_start', 160, None), event('terminal_callback_end', 165, None)]
    venue = [dict(request_id=1, order_id='same', stage=stage, at_ns=stamp * 1000000, state=state)
             for stage, stamp, state in [('received', 120, None), ('terminal_observed', 130, 'canceled'),
                                         ('response_ready', 140, None), ('response_eof', 175, None)]]
    samples = [dict(clock_aligned=True, at_ns=250000000, request_start_ns=240000000,
                    response_end_ns=260000000, cancel_events=venue)]
    return engine, samples


def test_timeline_join_preserves_overlapping_enqueue_and_callback_before_ack():
    engine, samples = timeline_fixture()
    analyze = getattr(activity, 'cancel_timeline_summary', lambda *args, **kwargs: {'calls': []})
    result = analyze(engine, samples, 0, 1000000000)
    assert len(result['calls']) == 1
    call = result['calls'][0]
    assert call['status'] == 'matched'
    assert call['pre_transport_ms'] == 15
    assert call['enqueue_to_venue_bounds_ms'] == [0, 5]
    assert call['connection_return_to_caller_ms'] == 30
    assert call['server_eof_to_client_headers_ms'] == -5
    assert call['callback_before_native_return'] is True
    assert call['place_ms'] == 130 and call['terminal_to_callback_ms'] == 35


@pytest.mark.parametrize('problem', ['missing', 'repeated_venue', 'overlapping_client', 'clock'])
def test_incomplete_or_ambiguous_timeline_never_guesses_zero_delay(problem):
    engine, samples = timeline_fixture()
    if problem == 'missing':
        engine = [row for row in engine if row['stage'] != 'caller_resumed']
    elif problem == 'repeated_venue':
        samples[0]['cancel_events'] += [{**row, 'request_id': 2} for row in samples[0]['cancel_events']]
    elif problem == 'overlapping_client':
        engine += [{**row, 'call_id': 'two'} for row in engine if row['call_id'] is not None]
    else:
        samples[0]['clock_aligned'] = False
    result = activity.cancel_timeline_summary(engine, samples, 0, 1000000000)
    assert result['calls'] and all(row['status'] != 'matched' for row in result['calls'])
    assert all('connection_return_to_caller_ms' not in row for row in result['calls'])
