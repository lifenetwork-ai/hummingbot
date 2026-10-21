"""Actual configured SPOT fills, signed accounting and original claim retirement."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def partial_spot_fill(t, service, values, side, fee_currency):
    stream = None
    try:
        action = await service.quote('filled-original-maker', side, 0)
        executor, = service.runner.executor_orchestrator.active_executors['main']
        order, = t.service_orders
        quantity, price, fee = D(order['sz']) / 2, D(order['px']), D('.0001')
        assert quantity > 0
        at = int(datetime.now(timezone.utc).timestamp() * 1000)
        trade = 'original-spot-partial'
        values[('SPOT', trade)] = IndependentFillObservation(price, at, at, 'independent_market')
        signed_base = quantity if side == TradeType.BUY else -quantity
        signed_quote = -signed_base * price
        expected_base = D('5') + signed_base - (fee if fee_currency == 'LIFE' else 0)
        expected_quote = D('10') + signed_quote - (fee if fee_currency == 'USDT' else 0)
        fill = dict(instType='SPOT', instId=order['instId'], clOrdId=order['clOrdId'], ordId=order['ordId'],
                    tradeId=trade, billId='102', side=order['side'], fillSz=str(quantity), fillPx=order['px'],
                    feeCcy=fee_currency, fee=str(-fee), ts=str(at))
        bills = []
        for bill_id, currency, principal in [('102', 'LIFE', signed_base), ('101', 'USDT', signed_quote)]:
            cost = -fee if currency == fee_currency else D('0')
            bills.append(dict(billId=bill_id, instType='SPOT', instId=order['instId'], clOrdId=order['clOrdId'],
                              ordId=order['ordId'], tradeId=trade, type='2', subType='1' if side == TradeType.BUY else '2',
                              ccy=currency, balChg=str(principal + cost), fee=str(cost), ts=str(at)))
        for asset, amount in zip(t.balance['details'], (expected_base, expected_quote)):
            asset.update(cashBal=str(amount), eq=str(amount), uTime=str(at))
        t.balance['uTime'] = str(at)
        order.update(state='partially_filled', accFillSz=str(quantity))
        http = t.session.request

        async def filled_http(**kwargs):
            tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
            if tail not in ('bills-archive', 'fills-history'):
                return await http(**kwargs)
            t.requests.append(kwargs)
            response = Response()

            async def data():
                rows = [*bills, dict(billId='100')] if tail == 'bills-archive' else [fill] if params['instType'] == 'SPOT' else []
                if tail == 'fills-history':
                    rows = [r for r in rows if all(not params.get(k) or r[k] == params[k] for k in ('instId', 'ordId'))]
                return dict(code='0', data=deepcopy(rows))
            response.json = data
            return response
        t.session.request = filled_http
        stream = asyncio.create_task(t.connector._user_stream_event_listener())
        await t.connector._user_stream_tracker.user_stream.put(dict(arg=dict(channel='orders'), data=[dict(
            order, tradeId=trade, fillFeeCcy=fee_currency, fillFee=str(-fee), fillSz=str(quantity),
            fillPx=order['px'], uTime=str(at))]))
        async with asyncio.timeout(5):
            while executor._order.order.executed_amount_base != quantity:
                await asyncio.sleep(.001)
        return SimpleNamespace(action=action, executor=executor, order=order, quantity=quantity, price=price, fee=fee,
                               at=at, trade=trade, expected_base=expected_base, expected_quote=expected_quote,
                               stream=stream, fill=fill, bills=bills)
    except BaseException:
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('fee_currency', ['LIFE', 'USDT'])
async def test_configured_spot_partial_fill_books_signed_money_before_original_claim_release(database, side, fee_currency):
    values = {}
    t, service = await configured(
        database, maker_cycle=True, independent_fill_value=lambda product, trade, at: values[(product, trade)],
        spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    stream, primary = None, None
    try:
        f = await partial_spot_fill(t, service, values, side, fee_currency)
        stream = f.stream
        action, order, at = f.action, f.order, f.at
        expected_base, expected_quote, fee, price = f.expected_base, f.expected_quote, f.fee, f.price
        await service.events.drain()
        await service.reporting.flush_deferred()
        await service.reporting.drain()
        booked = await service.financial.balance(t.cfg.binding)
        assert (booked.total_base_life, booked.total_quote_usdt) == (expected_base, expected_quote)
        claims = await service.source.capital.claims(t.cfg.binding)
        assert len(claims) == 1 and claims[0].intent_id == action.executor_config.id
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 1
        cost = (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt
        assert cost == fee * (price if fee_currency == 'LIFE' else 1)

        # The actual SDK sees terminal truth independently; signed HTTP must
        # still confirm original order/fills/bills before native retirement.
        order['state'] = 'canceled'
        await t.connector._user_stream_tracker.user_stream.put(dict(
            arg=dict(channel='orders'), data=[dict(order, tradeId='', uTime=str(at + 1))]))
        async with asyncio.timeout(5):
            while order['clOrdId'] in t.connector._order_tracker.active_orders:
                await asyncio.sleep(.001)
        assert await service.cancel_makers((action.executor_config.id,)) == (action.executor_config.id,)
        after = await service.financial.balance(t.cfg.binding)
        assert (after.total_base_life, after.total_quote_usdt) == (expected_base, expected_quote)
        assert after.reserved_base_life == after.reserved_quote_usdt == 0
        assert not await service.source.capital.claims(t.cfg.binding)
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == cost
        assert await service.cancel_makers((action.executor_config.id,)) == (action.executor_config.id,)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 1
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
        quota = service.quote_source._quota
        with pytest.raises(StorageError, match='PG_SHARED_QUOTA_SPOT_PHASE_INVALID'):
            async with quota.spot_phase(service.quote_application, action):
                pytest.fail('A historical action joined another task\'s quote window')
        assert quota._spot_cycle is None
        assert service.runtime.healthy
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
            if stream is not None:
                stream.cancel()
                await asyncio.gather(stream, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_configured_quote_releases_priority_waiters_without_releasing_claim(database, monkeypatch):
    t, service = await configured(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.quote_source.capture_and_publish_intent_ready
    task, background = None, None

    async def before_capture(*args, **kwargs):
        # No action has been issued: cancellation cannot renew its timestamp.
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(service.quote_source, 'capture_and_publish_intent_ready', before_capture)
    try:
        task = asyncio.create_task(service.quote('cancel-priority-original', TradeType.BUY, 0))
        async with asyncio.timeout(30):
            await entered.wait()
        quota = service.quote_source._quota
        assert quota._spot_cycle is not None
        background = asyncio.create_task(t.swap_connector.get_account_uid())
        await asyncio.sleep(.03)
        assert not background.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.wait_for(background, 5) == '12345'
        assert quota._spot_cycle is None and not quota._collection_lock.locked()
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        assert await service.financial.attempt('cancel-priority-original', t.cfg.binding) is None
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        release.set()
        for pending in (task, background):
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        await service.close()
