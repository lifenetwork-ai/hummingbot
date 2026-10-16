"""Final request guards observe the immutable payload actually handed to HTTP."""
import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from hummingbot.connector.exchange.okx.okx_auth import OkxAuth
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant


class Throttler:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    def execute_task(self, **kwargs):
        outer = self

        class Slot:
            async def __aenter__(self):
                outer.entered.set()
                await outer.release.wait()

            async def __aexit__(self, *_):
                pass
        return Slot()


class Response:
    status = 200
    content_type = 'application/json'

    async def json(self):
        return {'code': '0', 'data': []}


class Session:
    def __init__(self):
        self.sent = []

    async def request(self, **kwargs):
        self.sent.append(kwargs)
        return Response()


class Guard:
    def __init__(self):
        self.observed = []

    async def authorize(self, request):
        self.observed.append(request)
        return True

    def check(self, request):
        self.observed.append(request)
        return True


@pytest.mark.asyncio
async def test_guard_runs_after_throttle_preprocessing_auth_and_sees_exact_signed_http_body():
    session, throttle, guard = Session(), Throttler(), Guard()

    class Processor:
        async def pre_process(self, request):
            request.data = '{"actual":"body", "unicode":"é"}'
            return request

    auth = OkxAuth('synthetic-key', 'synthetic-secret', 'synthetic-pass', SimpleNamespace(time=lambda: 1700000000))
    assistant = RESTAssistant(RESTConnection(session), throttle, [Processor()], auth=auth)
    task = asyncio.create_task(assistant.execute_request(
        url='https://www.okx.com/api/v5/trade/order', throttler_limit_id='order',
        method=RESTMethod.POST, data={'original': 'body'}, is_auth_required=True, request_send_guard=guard))
    await asyncio.wait_for(throttle.entered.wait(), 2)
    assert not guard.observed and not session.sent
    throttle.release.set()
    await task
    first, last = guard.observed
    assert first is last and type(first) is RESTRequestSnapshot
    actual = session.sent[0]
    assert first.data == '{"actual":"body", "unicode":"é"}'
    # Supplying bytes prevents aiohttp from choosing another charset/encoding.
    assert actual['data'] == first.data.encode('utf-8')
    assert tuple(actual['headers'].items()) == first.headers
    headers = dict(first.headers)
    expected = base64.b64encode(hmac.new(b'synthetic-secret', (
        headers['OK-ACCESS-TIMESTAMP'] + 'POST/api/v5/trade/order' + first.data).encode(), hashlib.sha256).digest()).decode()
    assert headers['OK-ACCESS-SIGN'] == expected
    assert actual['allow_redirects'] is False
    assert 'synthetic-key' not in repr(first) and 'synthetic-pass' not in repr(first)
    with pytest.raises(FrozenInstanceError):
        first.data = 'changed'


@pytest.mark.asyncio
async def test_request_mutation_during_authorization_cannot_change_network_arguments():
    session, guard = Session(), Guard()
    request = RESTRequest(RESTMethod.POST, url='https://example.test/path', params={'x': 'one'},
                          data='original', headers={'signed': 'original'})

    async def mutate(snapshot):
        request.url, request.data = 'https://wrong.test/', 'changed'
        request.params['x'], request.headers['signed'] = 'two', 'changed'
        await asyncio.sleep(0)
        return True
    guard.authorize = mutate
    await RESTConnection(session).call(request, request_send_guard=guard)
    assert session.sent == [{'method': 'POST', 'url': 'https://example.test/path', 'params': {'x': 'one'},
                             'data': b'original', 'headers': {'signed': 'original'}, 'allow_redirects': False}]


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['authorize', 'check'])
@pytest.mark.parametrize('answer', [None, 1, 'true'])
async def test_guard_requires_literal_true(phase, answer):
    session, guard = Session(), Guard()

    async def authorize(_):
        return answer

    def check(_):
        return answer
    setattr(guard, phase, authorize if phase == 'authorize' else check)
    with pytest.raises(PermissionError):
        await RESTConnection(session).call(RESTRequest(RESTMethod.POST, url='https://example.test/', data='{}'),
                                           request_send_guard=guard)
    assert not session.sent


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['authorize', 'check', 'legacy_async', 'legacy_sync'])
async def test_denial_at_every_gate_sends_zero_requests(phase):
    session, guard = Session(), Guard()
    calls = []

    async def authorize(_):
        calls.append('authorize')
        return phase != 'authorize'

    def check(_):
        calls.append('check')
        return phase != 'check'

    async def legacy_async():
        calls.append('legacy_async')
        if phase == 'legacy_async':
            raise PermissionError('legacy')

    def legacy_sync():
        calls.append('legacy_sync')
        if phase == 'legacy_sync':
            raise PermissionError('legacy')
    guard.authorize, guard.check = authorize, check
    with pytest.raises(PermissionError):
        await RESTConnection(session).call(RESTRequest(RESTMethod.POST, url='https://example.test/', data='{}'),
                                           pre_send_check=legacy_sync, async_pre_send_check=legacy_async,
                                           request_send_guard=guard)
    assert not session.sent
    if phase == 'check':
        assert calls == ['legacy_async', 'authorize', 'legacy_sync', 'check']


@pytest.mark.asyncio
async def test_cancellation_while_authorizing_does_not_run_final_check_or_http():
    session, guard, entered = Session(), Guard(), asyncio.Event()

    async def held(_):
        entered.set()
        await asyncio.Event().wait()
    guard.authorize = held
    task = asyncio.create_task(RESTConnection(session).call(
        RESTRequest(RESTMethod.POST, url='https://example.test/', data='{}'), request_send_guard=guard))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not session.sent and not guard.observed


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'data': {}}, {'headers': {'x': []}}, {'params': {'x': 1}}, {'url': None},
                                     {'headers': {'X-Sign': 'one', 'x-sign': 'two'}}])
async def test_guard_rejects_non_immutable_request_fields(changes):
    session = Session()
    request = RESTRequest(RESTMethod.POST, url='https://example.test/', data=json.dumps({'x': 'y'}))
    for key, value in changes.items():
        setattr(request, key, value)
    with pytest.raises(ValueError, match='REQUEST_SNAPSHOT_INVALID'):
        await RESTConnection(session).call(request, request_send_guard=Guard())
    assert not session.sent


@pytest.mark.asyncio
async def test_okx_protected_scheduler_forwards_guard_and_rejects_malformed_guard():
    from decimal import Decimal
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.data_type.common import OrderType, TradeType
    connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair('LIFE-USDT')
    connector._create_order = AsyncMock()
    guard = Guard()
    kwargs = dict(order_id='wire1', trading_pair='LIFE-USDT', amount=Decimal('1'), price=Decimal('1'),
                  trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
                  pre_send_check=lambda _: None, on_ack=lambda _: None)
    with pytest.raises(ValueError):
        connector.submit_protected_order(**kwargs, request_send_guard=object())
    assert connector._create_order.call_count == 0
    assert connector.submit_protected_order(**kwargs, request_send_guard=guard) == 'wire1'
    await asyncio.sleep(0)
    assert connector._create_order.await_args.kwargs['request_send_guard'] is guard
