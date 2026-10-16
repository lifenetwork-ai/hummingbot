"""Signed private reads through the real OKX REST chain, fake HTTP only."""
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader


def private_http(t):
    requests = []
    t.connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)

    async def request(**kwargs):
        requests.append(kwargs)
        result = Response()

        async def body():
            return {'code': '0', 'data': [{'uid': '12345'}]} if kwargs['url'].endswith('/account/config') else {'code': '0', 'data': []}
        result.json = body
        return result
    t.session.request = request
    return requests


@pytest.mark.asyncio
async def test_signed_query_reads_retain_original_credentials_and_force_uid_at_both_collection_edges(database):
    t = await transport(database)
    requests = private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        before = await t.repo.snapshot(t.cfg.binding)
        async with reader.collection():
            assert await reader.get_account_bills_page(after='123') == {'code': '0', 'data': []}
            assert await reader.get_all_spot_fill_history_page() == {'code': '0', 'data': []}
            assert await reader.get_order_by_client_id('LIFE-USDT', 'wire1') == {'code': '0', 'data': []}
        assert len(requests) == 5 and all(row['allow_redirects'] is False for row in requests)
        assert requests[1]['params'] == {'limit': '100', 'after': '123'}
        assert requests[3]['params'] == {'instId': 'LIFE-USDT', 'clOrdId': 'wire1'}
        assert all(row['headers']['OK-ACCESS-KEY'] == 'synthetic-key' for row in requests)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['query', 'method', 'endpoint', 'signature', 'unsigned', 'credentials_after', 'uid_after', 'runtime_after', 'substitute_runtime'])
async def test_private_read_substitution_or_changed_context_never_returns_financial_facts(database, fault):
    t = await transport(database)
    requests = private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        original_auth = t.assistant._authenticate
        original_http = t.session.request

        async def auth(request):
            from hummingbot.core.web_assistant.connections.data_types import RESTMethod
            if not request.url.endswith('/account/config'):
                if fault == 'query':
                    request.params['after'] = '999'
                elif fault == 'method':
                    request.method = RESTMethod.POST
                elif fault == 'endpoint':
                    request.url = 'https://foreign.test/api/v5/account/bills-archive'
                if fault == 'unsigned':
                    return request
            result = await original_auth(request)
            if fault == 'signature' and not request.url.endswith('/account/config'):
                result.headers['OK-ACCESS-SIGN'] = 'wrong'
            return result
        t.assistant._authenticate = auth

        async def http(**kwargs):
            result = await original_http(**kwargs)
            if kwargs['url'].endswith('/bills-archive'):
                if fault == 'credentials_after':
                    t.connector.okx_api_key = 'replaced-key'
                elif fault == 'runtime_after':
                    reader.runtime._closing = True
                elif fault == 'substitute_runtime':
                    from types import SimpleNamespace
                    t.journal.repository = SimpleNamespace(binding=t.cfg.binding, _runtime=reader.runtime)
            elif fault == 'uid_after' and len(requests) == 3:
                async def body():
                    return {'code': '0', 'data': [{'uid': '999'}]}
                result.json = body
            return result
        t.session.request = http
        with pytest.raises((StorageError, PermissionError)):
            async with reader.collection():
                await reader.get_account_bills_page(after='123')
        assert not reader.runtime.healthy
        reader.runtime._closing = False
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert all(row['data'] is None for row in requests)
    finally:
        # The deliberate local closing flag is not a real close.
        t.repo._runtime._closing = False
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['reads', 'bytes', 'timeout', 'nested', 'outside', 'bad_cursor', 'foreign_market', 'bad_algo', 'bad_response'])
async def test_explicit_read_collection_bounds_fail_closed_without_money_or_send(database, fault):
    import asyncio
    t = await transport(database)
    requests = private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=1, max_bytes=16 if fault == 'bytes' else 1048576,
                                     timeout_ms=20 if fault == 'timeout' else 1000)
        if fault == 'outside':
            with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_CONTEXT_CHANGED'):
                await reader.get_spot_cash_balances()
        else:
            with pytest.raises(StorageError):
                async with reader.collection():
                    if fault == 'nested':
                        async with reader.collection():
                            pass
                    elif fault == 'timeout':
                        await asyncio.Event().wait()
                    elif fault == 'bad_cursor':
                        await reader.get_account_bills_page(after='123&ccy=USDT')
                    elif fault == 'foreign_market':
                        await reader.get_order_by_client_id('BTC-USDT', 'wire1')
                    elif fault == 'bad_algo':
                        await reader.get_all_pending_spot_algo_orders_page('foreign')
                    else:
                        if fault == 'bad_response':
                            original = t.session.request

                            async def http(**kwargs):
                                response = await original(**kwargs)
                                if kwargs['url'].endswith('/bills-archive'):
                                    async def body():
                                        return {'code': 'error', 'data': []}
                                    response.json = body
                                return response
                            t.session.request = http
                        await reader.get_account_bills_page()
                        await reader.get_account_bills_page()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
        assert all(row['data'] is None for row in requests)
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['uid_response', 'collection'])
async def test_wall_clock_jump_cannot_be_hidden_by_refreshing_uid_after_collection(database, phase):
    from datetime import timedelta
    t = await transport(database)
    private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        clock, shift = t.journal.clock, [timedelta()]
        t.journal.clock = lambda: clock() + shift[0]
        original = t.session.request

        async def http(**kwargs):
            result = await original(**kwargs)
            if phase == 'uid_response' and kwargs['url'].endswith('/account/config'):
                shift[0] = timedelta(milliseconds=200)
            return result
        t.session.request = http
        with pytest.raises(StorageError, match='PG_ACCOUNT_CLOCK_UNQUALIFIED'):
            async with reader.collection():
                await reader.get_account_bills_page()
                shift[0] = timedelta(milliseconds=200)
        assert not reader.runtime.healthy and not t.session.sent
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_active_private_collection_denies_quote_but_keeps_safety_reads_available(database):
    t = await transport(database)
    requests = private_http(t)
    try:
        account = await source(t)
        reader = PgSpotAccountReader(account, max_reads=32, max_bytes=1048576, timeout_ms=1000)
        async with reader.collection():
            assert reader.runtime._observation_inflight == 1 and not reader.runtime.healthy
            assert (await t.repo.snapshot(t.cfg.binding)).balance.total_quote_usdt == 10
            with pytest.raises(PermissionError, match='REQUEST_AUTHORIZATION_DENIED'):
                await t.place(guard=account.guard(action=t.create, preparation=t.preparation))
            assert await reader.get_account_bills_page() == {'code': '0', 'data': []}
        assert reader.runtime._observation_inflight == 0
        assert all(row['data'] is None for row in requests) and not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('path,params', [('/api/v5/asset/transfer', {}),
                                         ('/api/v5/account/balance', {'ccy': 'USDT'}),
                                         ('/api/v5/trade/fills-history', {'instType': 'SPOT', 'limit': '100', 'instId': 'BTC-USDT', 'ordId': '123'}),
                                         ('/api/v5/trade/fills-history', {'instType': 'SPOT', 'limit': '100', 'ordId': '123'}),
                                         ('/api/v5/trade/order', {'instId': 'LIFE-USDT'}),
                                         ('/api/v5/trade/order', {'instId': 'LIFE-USDT', 'ordId': '123'})])
async def test_even_internal_read_entry_requires_fixed_supported_route_and_complete_query(database, path, params):
    t = await transport(database)
    requests = private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_ROUTE_INVALID'):
            async with reader.collection():
                await reader._read(path, params)
        assert len(requests) == 1 and requests[0]['url'].endswith('/account/config')
        assert not reader.runtime.healthy
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_new_risk_already_waiting_for_connection_validation_is_denied_when_observation_starts(database, monkeypatch):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    t = await transport(database)
    gate, entered = asyncio.Event(), asyncio.Event()
    original = t.repo._runtime._validate_connection
    held = [False]

    async def validate(c):
        await original(c)
        if not held[0]:
            held[0] = True
            entered.set()
            await gate.wait()
    monkeypatch.setattr(t.repo._runtime, '_validate_connection', validate)
    try:
        monkeypatch.setattr(t.repo._runtime, '_validate_connection', original)
        cut = (await t.repo.snapshot(t.cfg.binding)).cut
        monkeypatch.setattr(t.repo._runtime, '_validate_connection', validate)
        task = asyncio.create_task(t.repo.prepare(order(t.owner, operation_id='queued', intent_id='queued', client_order_id='wire2', quote_level=1),
                                                  cut=cut, owner=t.owner))
        await entered.wait()
        t.repo._runtime._observation_inflight = 1
        gate.set()
        with pytest.raises(StorageError, match='PG_OBSERVATION_IN_PROGRESS'):
            await task
        t.repo._runtime._observation_inflight = 0
        assert await t.repo.lookup_prepare('queued', t.cfg.binding) is None
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt == t.preparation.prepare.quantity_base * t.preparation.prepare.limit_price_usdt + t.preparation.prepare.fee_buffer_quote_usdt
    finally:
        gate.set()
        t.repo._runtime._observation_inflight = 0
        await t.close()
