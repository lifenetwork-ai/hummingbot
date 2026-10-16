"""Explicit PG journal + actual OKX/auth/REST chain, fake HTTP and synthetic facts.

This does not install a configured PostgreSQL controller or authenticate a live
account, reference, fee feed, runner scope, or external credential fence.
"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response, Session, Throttler
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_runner_journal import assembled
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_transport import (
    PgQuoteRequestGuard,
    QuoteSendAgeLimits,
)

URL = 'https://www.okx.com/api/v5/trade/order'
# Synthetic values matching the explicitly imported fixture admission policy.
AGES = QuoteSendAgeLimits(1000, 1000, 1000, 1000)


async def transport(database, **seed_options):
    repo, cfg, owner, row, journal, create, runner, dispatch, active, ready = await assembled(database, **seed_options)
    await journal.publish('claim', (create,))
    dispatch.submit([create])
    await dispatch.drain()
    preparation = await command(database, cfg, owner, row)
    await journal.prepare_quote_action(create, preparation)

    async def facts():
        return (await command(database, cfg, owner, row)).admission

    guard = PgQuoteRequestGuard(journal, action=create, preparation=preparation,
                                admission_source=facts, endpoint_url=URL, age_limits=AGES)
    session, throttle = Session(), Throttler()
    throttle.release.set()
    connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair('LIFE-USDT')
    assistant = RESTAssistant(RESTConnection(session), throttle, auth=connector.authenticator)
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(return_value=URL)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')

    async def response():
        return {'code': '0', 'data': [{'sCode': '0', 'ordId': '123', 'clOrdId': 'wire1'}]}
    response_object = Response()
    response_object.json = response

    async def send(**kwargs):
        session.sent.append(kwargs)
        return response_object
    session.request = send

    async def place(**kwargs):
        selected_guard = kwargs.pop('guard', guard)
        return await connector._place_order(
            order_id='wire1', trading_pair='LIFE-USDT', amount=preparation.prepare.quantity_base,
            trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER, price=preparation.prepare.limit_price_usdt,
            pre_send_check=kwargs.pop('pre_send_check', lambda _: None),
            async_on_ack=selected_guard.acknowledge, request_send_guard=selected_guard, **kwargs)

    async def close():
        await dispatch.close()
        await repo.close()
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_exact_signed_http_body_arms_and_persists_ack_once_without_enabling_runner_submit(database):
    t = await transport(database)
    try:
        assert (await t.place())[0] == '123'
        assert len(t.session.sent) == 1
        saved = await t.repo.lookup_quote_arm('operation1', t.cfg.binding)
        assert saved.wire_sha256 == hashlib.sha256(t.session.sent[0]['data']).hexdigest()
        assert saved.trading_permission is False
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        before = await t.repo.balance(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        with pytest.raises(StorageError, match='PG_REQUEST_ACK_ALREADY_USED'):
            await t.guard.acknowledge('123')
        with pytest.raises(StorageError, match='PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED'):
            t.journal.submit_executor_spot_order(t.create.executor_config)
        assert await t.repo.balance(t.cfg.binding) == before and len(t.session.sent) == 1
        # A new wrapper cannot adopt retained DB/local arm state as permission.
        cold = PgQuoteRequestGuard(t.journal, action=t.create, preparation=t.preparation,
                                   admission_source=t.facts, endpoint_url=URL, age_limits=AGES)
        with pytest.raises(StorageError, match='RUNNER_PREPARATION_REQUIRED'):
            await t.place(guard=cold)
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_final_denial_cannot_be_revived_and_ack_before_send_is_denied(database):
    t = await transport(database)
    checked = []
    original = t.guard.check

    def check(request):
        checked.append(request)
        return original(request)
    t.guard.check = check
    try:
        with pytest.raises(StorageError, match='PG_REQUEST_ACK_BEFORE_SEND'):
            await t.guard.acknowledge('123')
        with pytest.raises(PermissionError, match='REQUEST_FINAL_CHECK_DENIED'):
            await t.place(pre_send_check=lambda _: t.ready.__setitem__(0, False))
        t.ready[0] = True
        assert original(checked[0]) is False
        with pytest.raises(StorageError, match='PG_REQUEST_ACK_BEFORE_SEND'):
            await t.guard.acknowledge('123')
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['throttle', 'arm_return', 'final_check', 'expired_return'])
async def test_local_stop_at_each_await_boundary_sends_zero_http_and_retains_finance(database, monkeypatch, phase):
    t = await transport(database)
    try:
        kwargs = {}
        if phase == 'throttle':
            t.throttle.release.clear()
        elif phase in ('arm_return', 'expired_return'):
            original = t.repo.arm_quote_action

            async def revoked(*args, **kw):
                result = await original(*args, **kw)
                if phase == 'arm_return':
                    t.journal.revoke()
                else:
                    expired_now = datetime.now(timezone.utc) + timedelta(seconds=2)
                    t.journal.clock = lambda: expired_now
                return result
            monkeypatch.setattr(t.repo, 'arm_quote_action', revoked)
        else:
            kwargs['pre_send_check'] = lambda _: t.journal.revoke()
        task = asyncio.create_task(t.place(**kwargs))
        if phase == 'throttle':
            await asyncio.wait_for(t.throttle.entered.wait(), 2)
            t.journal.revoke()
            t.throttle.release.set()
        with pytest.raises((StorageError, PermissionError)):
            await task
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == (
            'PREPARED' if phase == 'throttle' else 'SEND_UNKNOWN')
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['lost_reply', 'cancelled'])
async def test_arm_reply_uncertainty_never_retries_or_sends(database, monkeypatch, fault):
    t = await transport(database)
    entered = asyncio.Event()
    original = t.repo.arm_quote_action

    async def held(*args, **kwargs):
        await original(*args, **kwargs)
        entered.set()
        if fault == 'lost_reply':
            raise OSError('synthetic lost committed reply')
        await asyncio.Event().wait()
    monkeypatch.setattr(t.repo, 'arm_quote_action', held)
    try:
        task = asyncio.create_task(t.place())
        await asyncio.wait_for(entered.wait(), 3)
        if fault == 'cancelled':
            task.cancel()
        with pytest.raises(OSError if fault == 'lost_reply' else asyncio.CancelledError):
            await task
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        assert await t.repo.lookup_quote_arm('operation1', t.cfg.binding) is not None
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['endpoint', 'body', 'source', 'auth'])
async def test_invalid_final_request_or_source_never_reaches_http(database, fault):
    t = await transport(database)
    try:
        if fault == 'endpoint':
            t.connector._api_request_url.return_value = 'https://wrong.test/api/v5/trade/order'
        elif fault == 'body':
            class Processor:
                async def pre_process(self, request):
                    body = json.loads(request.data)
                    body['px'] = '9'
                    request.data = json.dumps(body)
                    return request
            t.assistant._rest_pre_processors = [Processor()]
        elif fault == 'auth':
            t.assistant._auth = None
        else:
            async def unavailable():
                raise StorageError('SYNTHETIC_SOURCE_UNAVAILABLE')
            t.guard._admission_source = unavailable
        with pytest.raises(StorageError, match={
            'endpoint': 'PG_REQUEST_SCOPE_INVALID', 'body': 'PG_QUOTE_WIRE_MISMATCH',
                'source': 'SYNTHETIC_SOURCE_UNAVAILABLE', 'auth': 'PG_REQUEST_SCOPE_INVALID'}[fault]):
            await t.place()
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [None, {'data': []}, {'code': '0', 'data': []},
                                  {'code': '0', 'data': [{'clOrdId': 'foreign', 'sCode': '0', 'ordId': '123'}]},
                                  {'code': '0', 'data': [{'clOrdId': 'wire1', 'sCode': '0', 'ordId': 'bad'}]},
                                  {'code': '0', 'data': [{'clOrdId': 'wire1', 'sCode': '0', 'ordId': '123'}] * 2},
                                  {'code': '0', 'data': [{'clOrdId': 'wire1', 'sCode': '1', 'ordId': '123', 'sMsg': 'denied'}]}])
async def test_unqualified_ack_never_accounts_or_releases_unknown_send(database, body):
    t = await transport(database)
    try:
        async def response():
            return body
        t.response_object.json = response
        with pytest.raises(IOError):
            await t.place()
        assert len(t.session.sent) == 1
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_transport_timeout_retains_unknown_send_and_disallows_retry(database):
    t = await transport(database)

    async def lost(**kwargs):
        t.session.sent.append(kwargs)
        raise TimeoutError('synthetic lost HTTP reply')
    t.session.request = lost
    try:
        with pytest.raises(TimeoutError):
            await t.place()
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        assert len(t.session.sent) == 1
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_lost_committed_ack_does_not_restore_transport_or_ack_permission(database, monkeypatch):
    t = await transport(database)
    original = t.repo.acknowledge

    async def lost(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError('synthetic lost ACK commit reply')
    monkeypatch.setattr(t.repo, 'acknowledge', lost)
    try:
        with pytest.raises(OSError, match='synthetic lost ACK'):
            await t.place()
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        with pytest.raises(StorageError, match='PG_REQUEST_ACK_ALREADY_USED'):
            await t.guard.acknowledge('123')
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_ack_after_local_stop_is_persisted_under_original_observation_scope(database):
    t = await transport(database)
    original = t.session.request

    async def stopped(**kwargs):
        t.journal.revoke()
        return await original(**kwargs)
    t.session.request = stopped
    try:
        assert (await t.place())[0] == '123'
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        assert not t.journal.local_runner_create_fence(t.create)
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_time_sync_retry_cannot_repeat_guarded_http_send(database):
    t = await transport(database)
    try:
        t.connector._update_time_synchronizer = AsyncMock()
        t.response_object.status = 400

        async def error():
            return '{"code":"50113"}'
        t.response_object.text = error
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        assert len(t.session.sent) == 1
        assert t.connector._update_time_synchronizer.await_count == 1
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('offset_seconds', [-5, 5])
async def test_ack_uses_primary_clock_when_local_clock_jumps_after_http_send(database, offset_seconds):
    t = await transport(database)
    original = t.session.request

    async def clock_jump(**kwargs):
        response = await original(**kwargs)
        jumped = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
        t.journal.clock = lambda: jumped
        return response
    t.session.request = clock_jump
    try:
        assert (await t.place())[0] == '123'
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        assert len(t.session.sent) == 1
        with pytest.raises(StorageError, match='PG_REQUEST_ACK_ALREADY_USED'):
            await t.guard.acknowledge('123')
    finally:
        await t.close()
