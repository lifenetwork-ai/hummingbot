"""Actual configured maker cancellation, original SDK closure and replenishment."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
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
@pytest.mark.parametrize('cycles', [1, 2])
async def test_imported_schedule_retires_every_original_maker_before_next_generation(database, cycles):
    def model(body):
        body.update(maker_schedule=dict(schedule_id='synthetic-bounded-cycles', levels=[0], refresh_interval_ms=1, max_cycles=cycles),
                    quote_schedule_timeout_ms=60000)
    # Explicit new synthetic multi-generation lease, fixed at initial seed.
    # Source, SQL, cancellation-phase and final capture limits stay unchanged.
    t, service = await configured(database, maker_cycle=True, model_change=model,
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    stream, canceled = await cancellation_feed(t)
    primary = None
    try:
        task = service.start_makers()
        with pytest.raises(StorageError, match='MAKER_RUNNING'):
            await service.quote('external-during-schedule', TradeType.BUY, 0)
        await task
        assert service.makers.completed_cycles == cycles and service.makers.targets == ()
        assert service.makers.failure is None and not service._revoked
        assert len(canceled) == len(set(canceled)) == 2 * cycles
        posts = [r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]
        assert len(posts) == 2 * cycles and len({json.loads(p['data'])['clOrdId'] for p in posts}) == 2 * cycles
        assert not t.connector._order_tracker.active_orders
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert not await service.source.capital.claims(t.cfg.binding)
        balance = await service.financial.balance(t.cfg.binding)
        assert balance.total_base_life == 5 and balance.total_quote_usdt == 10
        assert balance.reserved_base_life == balance.reserved_quote_usdt == 0
        # Each completely closed generation has one shared NAV/source basis;
        # the two releases still carry separate original signed proofs.
        assert await database[0].fetchval("""SELECT count(*) FROM life_financial.operations
            WHERE command->>'kind'='PORTFOLIO_NAV' AND operation_id LIKE 'service-retire-nav:%'""") == cycles
        assert await database[0].fetchval("""SELECT count(*) FROM life_financial.operations
            WHERE command->>'kind'='SHARED_RELEASE' AND operation_id LIKE 'service-retire:%'""") == 2 * cycles
        with pytest.raises(StorageError, match='ALREADY_STARTED'):
            service.start_makers()
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
async def test_two_sided_makers_keep_both_original_claims_and_sdk_scope(database, monkeypatch):
    t, service = await configured(database, maker_cycle=True)
    primary = None
    try:
        bid = await service.quote('standing-bid', TradeType.BUY, 0)
        batches = []
        read_batch = service.collector.reader.read_batch

        async def recorded(routes):
            batches.append(routes)
            return await read_batch(routes)
        monkeypatch.setattr(service.collector.reader, 'read_batch', recorded)
        ask = await service.quote('standing-ask', TradeType.SELL, 0)
        assert {e.config.id for e in service.runner.executor_orchestrator.active_executors['main']} == {'standing-bid', 'standing-ask'}
        assert set(t.connector._order_tracker.active_orders) == {service.quote_source.wire_for_action(a) for a in (bid, ask)}
        assert (await service.financial.attempt('standing-bid', t.cfg.binding)).state.value == 'ACKED'
        assert (await service.financial.attempt('standing-ask', t.cfg.binding)).state.value == 'ACKED'
        claims = await service.source.capital.claims(t.cfg.binding)
        assert {c.intent_id for c in claims} == {'standing-bid', 'standing-ask'}
        frame = await service.source.carry.frame(t.cfg.binding)
        assert [c['intent_id'] for c in frame['bundle']['account']['snapshot']['pending']] == ['standing-bid']
        balance = await service.financial.balance(t.cfg.binding)
        assert balance.total_quote_usdt == 10 and balance.total_base_life == 5
        assert balance.reserved_quote_usdt > 0 and balance.reserved_base_life > 0
        assert len([r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]) == 2
        assert all(len(routes) <= 8 for routes in batches)
        assert any('/api/v5/account/balance' in {path for path, _ in routes}
                   and ('/api/v5/trade/order', {'instId': 'LIFE-USDT', 'clOrdId': service.quote_source.wire_for_action(bid)}) in routes
                   for routes in batches)
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


@pytest.mark.asyncio
async def test_shared_generation_nav_cannot_retire_next_claim_after_native_money_corruption(database, monkeypatch):
    # This two-target fault must reach the second retirement under the same
    # explicit aggregate calibration as the multi-generation acceptance case.
    t, service = await configured(database, maker_cycle=True,
                                  model_change=lambda model: model.update(quote_schedule_timeout_ms=60000),
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    stream, _ = await cancellation_feed(t)
    corrupted, calls = False, []
    primary = None

    async def rewrite_balance(delta):
        # Deliberate owner-level storage corruption, not runtime DML. Without
        # this test-only bypass the row-version trigger correctly rejects the
        # injection before the source verifier can be exercised.
        async with database[0].transaction():
            await database[0].execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.balances SET total=total+$2 WHERE account_id=$1 AND asset='USDT'", t.cfg.account_id, delta)
            await database[0].execute('ALTER TABLE life_financial.balances ENABLE TRIGGER USER')
    try:
        await service.quote('nav-bid', TradeType.BUY, 0)
        await service.quote('nav-ask', TradeType.SELL, 0)
        retire = service.cancellation.release.retire_claim

        async def changed(operation_id, intent_id, terminal_id):
            nonlocal corrupted
            calls.append(intent_id)
            if len(calls) == 2:
                # Privileged test corruption, without a financial-version bump.
                # An earlier valid NAV/retirement cannot mask a new bad row.
                await rewrite_balance(1)
                corrupted = True
            return await retire(operation_id, intent_id, terminal_id)
        monkeypatch.setattr(service.cancellation.release, 'retire_claim', changed)
        with pytest.raises(StorageError, match='UNVERIFIED|UNQUALIFIED'):
            await service.cancel_makers(('nav-bid', 'nav-ask'))
        assert len(calls) == 2 and corrupted and service._revoked
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=$1', t.cfg.account_id) == 1
        assert await database[0].fetchval('''SELECT count(*) FROM life_financial.shared_capital_claims c
            WHERE c.account_id=$1 AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases r
                WHERE r.account_id=c.account_id AND r.intent_id=c.intent_id)''', t.cfg.account_id) == 1
        assert not t.connector._order_tracker.active_orders
    except BaseException as error:
        primary = error
        raise
    finally:
        if corrupted:
            await rewrite_balance(-1)
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
