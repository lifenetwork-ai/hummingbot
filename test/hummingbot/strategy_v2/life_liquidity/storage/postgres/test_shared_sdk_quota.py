"""Actual SDK/auth/REST and AsyncThrottler; fake HTTP, no database/account."""
import asyncio
import time
from decimal import Decimal
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response, Session
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def fake_http(sdk, session):
    sdk._web_assistants_factory = WebAssistantsFactory(
        throttler=sdk._throttler, auth=sdk._auth,
        connections_factory=SimpleNamespace(get_rest_connection=AsyncMock(return_value=RESTConnection(session))))
    sdk.order_book_tracker.data_source._api_factory = sdk._web_assistants_factory
    if sdk._user_stream_tracker is not None:
        sdk._user_stream_tracker.data_source._api_factory = sdk._web_assistants_factory


def sdk_pair(share=Decimal('100')):
    spot = OkxExchange(
        'synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=['LIFE-USDT'],
        trading_required=False, rate_limits_share_pct=share)
    swap = OkxPerpetualDerivative(
        okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
        okx_perpetual_passphrase='synthetic-pass', trading_pairs=['LIFE-USDT'],
        trading_required=False, rate_limits_share_pct=share)
    session = Session()

    async def request(**kwargs):
        session.sent.append(kwargs)
        response = Response()

        async def body():
            return dict(code='0', data=[dict(uid='12345')])
        response.json = body
        return response
    session.request = request
    for sdk in (spot, swap):
        fake_http(sdk, session)
    return spot, swap, session


@pytest.mark.asyncio
async def test_collection_waits_before_observations_without_erasing_used_capacity():
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    await swap.get_account_uid()
    before = time.monotonic()
    async with asyncio.timeout(3), quota.collection():
        assert time.monotonic() - before >= 2
        assert len(session.sent) == 1
        assert len(quota._shared._task_logs) == 1  # No synthetic refunds/reset.
        assert await spot.get_account_uid() == await swap.get_account_uid() == '12345'
    assert len(session.sent) == 3


@pytest.mark.asyncio
async def test_waiting_for_sdk_websocket_login_does_not_occupy_the_rest_collection_window():
    from hummingbot.connector.exchange.okx.okx_constants import WS_LOGIN_LIMIT_ID
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    async with spot._throttler.execute_task(WS_LOGIN_LIMIT_ID):
        pass

    async def next_login():
        async with spot._throttler.execute_task(WS_LOGIN_LIMIT_ID):
            pytest.fail('WebSocket login rate was not retained')
    waiting = asyncio.create_task(next_login())
    try:
        await asyncio.sleep(.03)
        assert not waiting.done()
        assert await asyncio.wait_for(swap.get_account_uid(), .5) == '12345'
        assert len(session.sent) == len(quota._shared._task_logs) == 1
        assert not waiting.done()
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_collection_excludes_other_sdk_tasks_and_revokes_escaped_lease():
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    external, escaped = asyncio.Event(), asyncio.Event()

    async def later(event, sdk):
        await event.wait()
        return await sdk.get_account_uid()
    other = asyncio.create_task(later(external, swap))  # Captures no collection.
    try:
        async with quota.collection():
            child = asyncio.create_task(later(escaped, spot))
            inherited = asyncio.create_task(spot.get_account_uid())
            external.set()
            await asyncio.sleep(.03)
            assert not other.done() and not inherited.done() and not session.sent
            assert await spot.get_account_uid() == '12345'
        escaped.set()
        with pytest.raises(StorageError, match='PG_SHARED_QUOTA_COLLECTION_CLOSED'):
            await child
        assert await asyncio.wait_for(other, 1) == '12345'
        assert await asyncio.wait_for(inherited, 1) == '12345'
        assert len(session.sent) == 3
    finally:
        external.set()
        escaped.set()
        await asyncio.gather(other, child, inherited, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['cancel', 'drift'])
async def test_interrupted_collection_keeps_logs_and_does_not_start_observations(fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    await spot.get_account_uid()

    async def collect():
        async with quota.collection():
            pytest.fail('source facts started with an occupied quota window')
    pending = asyncio.create_task(collect())
    await asyncio.sleep(.03)
    if fault == 'cancel':
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await swap.get_account_uid() == '12345'
        assert len(session.sent) == len(quota._shared._task_logs) == 2
    else:
        swap.okx_perpetual_api_key = 'changed'
        with pytest.raises(StorageError, match='PG_SHARED_QUOTA_CONTEXT_CHANGED'):
            await asyncio.wait_for(pending, 1)
        assert len(session.sent) == len(quota._shared._task_logs) == 1


@pytest.mark.asyncio
async def test_spot_and_swap_share_five_uid_requests_instead_of_two_independent_budgets():
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    assert quota.assert_binding() is None
    assert [await sdk.get_account_uid() for sdk in (spot, swap, spot, swap, spot)] == ['12345'] * 5
    sixth = asyncio.create_task(swap.get_account_uid())
    try:
        await asyncio.sleep(.05)
        assert not sixth.done() and len(session.sent) == 5
        assert await asyncio.wait_for(sixth, 3) == '12345'
        assert len(session.sent) == 6
        assert all(row['headers'].get('OK-ACCESS-SIGN') for row in session.sent)
    finally:
        if not sixth.done():
            sixth.cancel()
            await asyncio.gather(sixth, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['credentials', 'factory', 'data_factory', 'throttler', 'profile'])
async def test_context_drift_while_waiting_for_shared_quota_never_reaches_http(fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    for sdk in (spot, swap, spot, swap, spot):
        await sdk.get_account_uid()
    sixth = asyncio.create_task(swap.get_account_uid())
    try:
        await asyncio.sleep(.05)
        assert len(session.sent) == 5
        if fault == 'credentials':
            spot.okx_api_key = 'changed'
        elif fault == 'factory':
            spot._web_assistants_factory = object()
        elif fault == 'data_factory':
            spot.order_book_tracker.data_source._api_factory = object()
        elif fault == 'throttler':
            spot._throttler = object()
        else:
            quota._shared._id_to_limit_map['/api/v5/account/config'].limit = 999
        with pytest.raises(StorageError, match='PG_SHARED_QUOTA_CONTEXT_CHANGED'):
            await asyncio.wait_for(sixth, 3)
        assert len(session.sent) == 5
        with pytest.raises(StorageError):
            await swap.get_account_uid()
    finally:
        if not sixth.done():
            sixth.cancel()
            await asyncio.gather(sixth, return_exceptions=True)


@pytest.mark.asyncio
async def test_bootstrap_refuses_used_or_foreign_sdk_without_partially_replacing_either_factory():
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    for fault in ('used', 'credentials', 'factory', 'data_factory', 'shared_base', 'pair'):
        spot, swap, session = sdk_pair()
        if fault == 'used':
            await spot.get_account_uid()
        elif fault == 'credentials':
            swap.okx_perpetual_api_key = 'foreign'
        elif fault == 'factory':
            swap._web_assistants_factory = object()
        elif fault == 'data_factory':
            swap.order_book_tracker.data_source._api_factory = object()
        elif fault == 'shared_base':
            swap._throttler = swap._web_assistants_factory._throttler = spot._throttler
        else:
            swap._trading_pairs.append('BTC-USDT')
        original = spot._throttler, swap._throttler, spot._web_assistants_factory, swap._web_assistants_factory
        with pytest.raises(StorageError, match='PG_SHARED_QUOTA_BOOTSTRAP_INVALID'):
            PgSharedSdkQuota.install(spot=spot, swap=swap)
        assert original == (spot._throttler, swap._throttler, spot._web_assistants_factory, swap._web_assistants_factory)


@pytest.mark.asyncio
async def test_unknown_or_new_pair_routes_cannot_use_unlimited_sdk_fallback():
    from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_web_utils
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair()
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    swap._throttler.add_rate_limits(okx_perpetual_web_utils._build_private_pair_specific_rate_limits(['LIFE-USDT']))
    quota.assert_binding()
    with pytest.raises(StorageError, match='PG_SHARED_QUOTA_ROUTE_UNQUALIFIED'):
        async with spot._throttler.execute_task('/api/v5/unqualified'):
            pytest.fail('unknown route acquired quota')
    with pytest.raises(StorageError, match='PG_SHARED_QUOTA_PROFILE_CHANGED'):
        swap._throttler.add_rate_limits(okx_perpetual_web_utils._build_private_pair_specific_rate_limits(['BTC-USDT']))
    assert not session.sent


@pytest.mark.asyncio
async def test_lower_sdk_share_and_repeated_normal_startup_do_not_reset_or_enlarge_quota():
    from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_web_utils
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot, swap, session = sdk_pair(share=Decimal('25'))
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    assert quota._shared._id_to_limit_map['/api/v5/account/config'].limit == 1
    assert await spot.get_account_uid() == '12345'
    swap._throttler.add_rate_limits(okx_perpetual_web_utils._build_private_pair_specific_rate_limits(['LIFE-USDT']))
    pending = asyncio.create_task(swap.get_account_uid())
    try:
        await asyncio.sleep(.05)
        assert not pending.done() and len(session.sent) == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(session.sent) == 1
        quota.assert_binding()
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_original_sdk_time_synchronizer_auxiliary_http_uses_shared_quota(monkeypatch):
    from hummingbot.core.web_assistant.connections.connections_factory import ConnectionsFactory
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    spot = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=['LIFE-USDT'], trading_required=False)
    swap = OkxPerpetualDerivative(
        okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
        okx_perpetual_passphrase='synthetic-pass', trading_pairs=['LIFE-USDT'], trading_required=False)
    session = Session()

    async def request(**kwargs):
        session.sent.append(kwargs)
        response = Response()

        async def body():
            return dict(code='0', data=[dict(ts=str(int(time.time() * 1000)))]) if kwargs['url'].endswith('/public/time') else dict(code='0', data=[dict(uid='12345')])
        response.json = body
        return response
    session.request = request
    monkeypatch.setattr(ConnectionsFactory, '_get_shared_client', AsyncMock(return_value=session))
    # Keep the original SDK factories/processors: their time-provider closure
    # captured the pre-install throttler and creates another factory for GET.
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    assert await spot.get_account_uid() == '12345'
    clock_reads = [r for r in session.sent if r['url'].endswith('/public/time')]
    assert len(clock_reads) == 1
    assert len([r for r in quota._shared._task_logs if r.rate_limit.limit_id == '/api/v5/public/time']) == 1
