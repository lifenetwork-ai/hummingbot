"""Configured native hedge entry point, real SDK/runner/DB and fake HTTP only."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_collateral_source import bodies
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_cycle import cancellation_feed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.hedge import HedgePolicy
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeExecutionPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import UpdateHedgeTarget


def policy():
    # Explicit synthetic values, independently imported before service startup.
    return HedgeExecutionPolicy(HedgePolicy('LIFE-USDT-SWAP', D('.1'), D('.25'), D('2'), D('3'),
                                            1000, 3, D('1'), D('100'), D('.001')),
                                D('0'), D('5'), 5000, 'bounded_maker_and_pause')


@pytest.mark.asyncio
async def test_configured_hedge_requires_imported_policy_at_startup(database):
    with pytest.raises(StorageError, match='PG_SERVICE_HEDGE_POLICY_REQUIRED'):
        await configured(database, model_change=lambda model: model.update(hedge_enabled=True))


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['funding', 'joint'])
async def test_configured_service_requires_both_product_monetary_routes_at_startup(database, missing):
    with pytest.raises(StorageError, match='PG_SERVICE_FINANCIAL_INTAKE_REQUIRED'):
        await configured(database, omit_financial_policy=missing)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0


@pytest.mark.asyncio
async def test_configured_quote_only_profile_cannot_execute_hedge(database):
    t, service = await configured(database)
    try:
        with pytest.raises(StorageError, match='PG_SERVICE_HEDGE_NOT_ENABLED'):
            await service.execute_hedge('not-enabled')
        assert not t.service_orders and not service._revoked
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_configured_direct_hedge_has_no_cycle_ownership_or_send_authority(database):
    t, service = await configured(database, hedge_policy=policy())
    try:
        with pytest.raises(StorageError, match='PG_HEDGE_APPLICATION_ORIGIN_REQUIRED'):
            await service.hedge.execute('not-a-service-cycle')
        assert not t.service_orders
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
        assert not await service.source.capital.claims(t.cfg.binding)
        assert service.runtime.healthy and not service.hedge._stopped
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('partial_fill', [False, True])
async def test_configured_hedge_terminal_settlement_and_release_require_signed_original_order(database, partial_fill):
    values = {}
    t, service = await configured(database, hedge_policy=policy(), independent_fill_value=lambda product, trade, at: values[trade])
    stream = None
    try:
        before = await service.financial.balance(t.cfg.binding)
        await service.execute_hedge('to-close')
        order, = t.service_orders
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        # A new fake exchange private message closes its original SDK order.
        # It supplies no monetary/terminal authority to the service: signed
        # full HTTP collection must still agree at native terminal and release.
        order['state'] = 'canceled'
        now = datetime.now(timezone.utc).timestamp()
        if partial_fill:
            at = int(now * 1000)
            values['original-partial'] = IndependentFillObservation(D('1'), at, at, 'independent_market')
            order['accFillSz'] = '2'  # .5 LIFE, with the imported .25 contract.
            fill = dict(instType='SWAP', instId=order['instId'], clOrdId=order['clOrdId'], ordId=order['ordId'],
                        tradeId='original-partial', billId='101', side='sell', fillSz='2', fillPx=order['px'],
                        feeCcy='USDT', fee='-.001', fillPnl='0', ts=str(at))
            bill = dict(billId='101', instType='SWAP', instId=order['instId'], clOrdId=order['clOrdId'],
                        ordId=order['ordId'], tradeId=fill['tradeId'], type='2', subType='4', ccy='USDT',
                        balChg='-.001', fee='-.001', pnl='0', ts=str(at))
            _, balance, positions = bodies(mode='long_short_mode', short='2', cash='9.999', entry=order['px'])
            # Position quantities on the wire are contracts; UPL is USDT.
            upl = D('.5') * (D(order['px']) - D('1'))
            positions[0]['upl'] = str(upl)
            positions[0]['uTime'] = str(at)
            balance['details'][1].update(upl=str(upl), eq=str(D('9.999') + upl))
            balance['uTime'] = str(at)
            for asset in balance['details']:
                asset['uTime'] = str(at)
            t.balance.clear()
            t.balance.update(balance)
            t.positions[:] = positions
            http = t.session.request

            async def filled_http(**kwargs):
                tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
                if tail not in ('bills-archive', 'fills-history'):
                    return await http(**kwargs)
                t.requests.append(kwargs)
                response = Response()

                async def data():
                    rows = [bill, dict(billId='100')] if tail == 'bills-archive' else [fill] if params['instType'] == 'SWAP' else []
                    return dict(code='0', data=deepcopy(rows))
                response.json = data
                return response
            t.session.request = filled_http
        stream = asyncio.create_task(t.swap_connector._user_stream_event_listener())
        t.swap_connector._set_current_timestamp(now)
        await t.swap_connector._user_stream_tracker.user_stream.put(dict(
            arg=dict(channel='orders'), data=[dict(order, tradeId='', uTime=str(int(now * 1000)))]))
        async with asyncio.timeout(5):
            while order['clOrdId'] in t.swap_connector._order_tracker.active_orders:
                await asyncio.sleep(.001)
        result = await service.settle_hedge('close-once', 'to-close')
        assert result.state == 'TERMINAL'
        assert not await service.source.capital.claims(t.cfg.binding)
        after = await service.financial.balance(t.cfg.binding)
        assert after.total_quote_usdt == before.total_quote_usdt - (D('.001') if partial_fill else D('0'))
        assert after.total_base_life == before.total_base_life
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == int(partial_fill)
        cost = D('.001') if partial_fill else D('0')
        assert await database[0].fetchval('SELECT actual_cost_quote FROM life_financial.hedge_settlements') == cost
        assert (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == cost
        assert (await service.settle_hedge('close-once', 'to-close')).replayed
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
        assert service.runtime.healthy
    finally:
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_configured_hedge_uses_signed_sources_original_sdk_and_permanent_ack(database):
    t, service = await configured(database, hedge_policy=policy())
    try:
        before = await service.financial.balance(t.cfg.binding)
        receipt = await service.execute_hedge('configured-hedge')
        assert receipt.child_state == 'PREPARED' and receipt.child_operation_id is not None
        attempt = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=\'SWAP\'')
        assert attempt['state'] == 'ACKED'
        posts = [r for r in t.requests if r['method'] == 'POST']
        assert len(posts) == 1
        body = json.loads(posts[0]['data'])
        assert body['instId'] == 'LIFE-USDT-SWAP' and body['posSide'] == 'short'
        assert body['clOrdId'] == attempt['wire_id']
        assert body['clOrdId'] in t.swap_connector._order_tracker.active_orders
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert (await service.financial.balance(t.cfg.binding)).total_quote_usdt == before.total_quote_usdt
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        assert (await service.execute_hedge('configured-hedge')).replayed
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_configured_hedge_retires_original_spot_maker_before_swap_source_and_post(database):
    t, service = await configured(database, hedge_policy=replace(policy(), initial_target_base=D('5')), maker_cycle=True)
    stream = None
    try:
        balanced = await service.execute_hedge('initial-balanced')
        assert balanced.decision['reason_code'] == 'HEDGE_DEADBAND' and balanced.child_operation_id is None
        await service.quote('original-maker', TradeType.BUY, 0)
        # An explicit native target command changes the desired exposure;
        # no historical plan, source timestamp or policy is rewritten.
        await service.hedge.repository.update_target(UpdateHedgeTarget('new-target', 0, D('0')),
                                                     cut=(await service.financial.snapshot(t.cfg.binding)).cut,
                                                     owner=service.journal.owner)
        stream, canceled = await cancellation_feed(t)
        receipt = await service.execute_hedge('after-maker')
        assert receipt.child_operation_id is not None and len(canceled) == 1
        assert not t.connector._order_tracker.active_orders
        assert not service.runner.executor_orchestrator.active_executors['main']
        claims = await service.source.capital.claims(t.cfg.binding)
        assert len(claims) == 1 and claims[0].product == 'SWAP'
        wires = [json.loads(r['data'])['instId'] for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]
        assert wires == ['LIFE-USDT', 'LIFE-USDT-SWAP']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 1
    finally:
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capture', 'after_ack'])
async def test_configured_hedge_excludes_quote_cancel_and_direct_hedge_during_capture(database, phase):
    t, service = await configured(database, hedge_policy=policy())
    entered, release = asyncio.Event(), asyncio.Event()
    http = t.session.request

    async def held(**kwargs):
        if phase == 'capture' and kwargs['url'].endswith('/market/books'):
            entered.set()
            await release.wait()
        return await http(**kwargs)
    t.session.request = held
    observe = service.events.observer.observe

    async def held_observation(*args, **kwargs):
        if phase == 'after_ack':
            entered.set()
            await release.wait()
        return await observe(*args, **kwargs)
    service.events.observer.observe = held_observation
    task = asyncio.create_task(service.execute_hedge('held-hedge'))
    try:
        await asyncio.wait_for(entered.wait(), 25)
        for work in (service.quote('overlap', TradeType.BUY, 0), service.cancel_makers(('overlap',)),
                     service.execute_hedge('second-hedge')):
            with pytest.raises(StorageError, match='PG_SERVICE_QUOTE_BACKPRESSURE'):
                await work
        with pytest.raises(StorageError, match='PG_HEDGE_APPLICATION_ORIGIN_REQUIRED'):
            await service.hedge.execute('direct-overlap')
        assert len(t.service_orders) == int(phase == 'after_ack')
        if phase == 'after_ack':
            assert not service.hedge._stopped and service.runtime._healthy and not service.runtime.healthy
            assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE product='SWAP'") == 'ACKED'
        release.set()
        assert (await task).child_operation_id is not None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['revoke', 'cancel'])
async def test_configured_hedge_shutdown_holds_original_sdk_and_db_through_late_ack(database, fault):
    t, service = await configured(database, hedge_policy=policy())
    entered, release = asyncio.Event(), asyncio.Event()
    http = t.session.request

    async def held(**kwargs):
        if kwargs['method'] == 'POST' and kwargs['url'].endswith('/order'):
            entered.set()
            await release.wait()
        return await http(**kwargs)
    t.session.request = held
    task = asyncio.create_task(service.execute_hedge('late-original'))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 25)
        if fault == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            service.revoke()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(.01)
        assert not closing.done() and not service.runtime._closing and service.hedge._physical
        release.set()
        if fault == 'revoke':
            with pytest.raises(StorageError):
                await task
        await closing
        assert service._close_task.done() and not service.hedge._physical and not service.runtime._tasks
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE product='SWAP'") == 'ACKED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await service.close()
