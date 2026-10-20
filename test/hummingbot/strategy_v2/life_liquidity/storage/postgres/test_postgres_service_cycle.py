"""Actual configured maker cancellation, original SDK closure and replenishment."""
import asyncio
import json
from datetime import datetime, timezone
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def cancellation_feed(t, *, private_closed=True, sdk_closed=True):
    original, canceled = t.session.request, []
    stream = asyncio.create_task(t.connector._user_stream_event_listener())

    async def http(**kwargs):
        if not kwargs['url'].endswith('/cancel-order'):
            return await original(**kwargs)
        t.requests.append(kwargs)
        body = json.loads(kwargs['data'])
        order, = [o for o in t.service_orders if o['clOrdId'] == body['clOrdId']]
        assert order['state'] == 'live'
        if private_closed:
            order['state'] = 'canceled'
        canceled.append(body['clOrdId'])
        now = datetime.now(timezone.utc).timestamp()
        t.connector._set_current_timestamp(now)
        # The real SDK consumes this fake private-channel message. The service
        # must independently collect signed HTTP truth before terminal/release.
        if sdk_closed:
            await t.connector._user_stream_tracker.user_stream.put(dict(
                arg=dict(channel='orders'), data=[dict(order, state='canceled', tradeId='', uTime=str(int(now * 1000)))]))
            async with asyncio.timeout(5):
                while body['clOrdId'] in t.connector._order_tracker.active_orders:
                    await asyncio.sleep(.001)
        response = Response()

        async def data():
            return dict(code='0', data=[dict(sCode='0', clOrdId=body['clOrdId'], ordId=order['ordId'])])
        response.json = data
        return response
    t.session.request = http
    return stream, canceled


@pytest.mark.asyncio
async def test_configured_maker_can_cancel_retire_and_replace_without_reusing_money_or_identity(database):
    t, service = await configured(database, maker_cycle=True)
    stream, canceled = await cancellation_feed(t)
    primary = None
    try:
        original_balance = await service.financial.balance(t.cfg.binding)
        first = await service.quote('maker-first', TradeType.BUY, 0)
        wire = service.quote_source.wire_for_action(first)
        executor, = service.runner.executor_orchestrator.active_executors['main']
        assert (await service.financial.attempt('maker-first', t.cfg.binding)).state.value == 'ACKED'
        targets = (first.executor_config.id,)
        await service.cancel_makers(targets)
        assert canceled == [wire] and not executor.is_active and executor._control_loop_task.done()
        assert (await service.financial.attempt('maker-first', t.cfg.binding)).state.value == 'TERMINAL'
        assert await service.source.capital.claims(t.cfg.binding) == ()
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert not t.connector._order_tracker.active_orders
        assert (await service.financial.balance(t.cfg.binding)).total_quote_usdt == original_balance.total_quote_usdt
        assert (await service.financial.balance(t.cfg.binding)).total_base_life == original_balance.total_base_life
        original_close = executor.close_timestamp
        # Original permanent IDs remain consumed, and a completed cancellation
        # replay cannot act on the next generation of makers.
        replacement = await service.quote('maker-second', TradeType.SELL, 0)
        assert executor.close_timestamp == original_close
        assert replacement.executor_config.id != first.executor_config.id
        await service.cancel_makers(targets)
        assert canceled == [wire]
        assert (await service.financial.attempt('maker-second', t.cfg.binding)).state.value == 'ACKED'
        posts = [r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]
        assert len(posts) == 2
        assert len({json.loads(p['data'])['clOrdId'] for p in posts}) == 2
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await service.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note('cleanup_failure=' + type(error).__name__ + ':' + str(error))
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('private_closed,sdk_closed', [(False, False), (True, False), (False, True)])
async def test_configured_cancel_ack_or_one_sided_closure_never_refunds_or_retries(database, private_closed, sdk_closed):
    t, service = await configured(database, maker_cycle=True)
    stream, canceled = await cancellation_feed(t, private_closed=private_closed, sdk_closed=sdk_closed)
    try:
        action = await service.quote('unclosed-maker', TradeType.BUY, 0)
        before = await service.financial.balance(t.cfg.binding)
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY|SDK_NOT_QUIESCENT'):
            await service.cancel_makers((action.executor_config.id,))
        assert len(canceled) == 1 and service._revoked
        after = await service.financial.balance(t.cfg.binding)
        assert after == before
        assert (await service.financial.attempt('unclosed-maker', t.cfg.binding)).state.value == 'ACKED'
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        with pytest.raises(StorageError, match='RECONCILIATION_REQUIRED'):
            await service.cancel_makers((action.executor_config.id,))
        assert len(canceled) == 1
    finally:
        try:
            await service.close()
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_reserves_cycle_before_awaiting_history_and_releases_it_on_invalid_targets(database, monkeypatch):
    t, service = await configured(database)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None

    async def blocked_history(targets):
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(service, '_retired_maker_history', blocked_history)
    try:
        before = await service.financial.balance(t.cfg.binding)
        task = asyncio.create_task(service.cancel_makers(('absent-maker',)))
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await service.quote('concurrent-maker', TradeType.BUY, 0)
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await service.cancel_makers(('another-maker',))
        release.set()
        with pytest.raises(StorageError, match='TARGET_SCOPE'):
            await task
        assert service._quote_task is None and not service._revoked
        assert (await service.financial.balance(t.cfg.binding)) == before
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()
