"""Native ACK identities cancel before shutdown joins blocked SDK/DB work."""
import asyncio
import json
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_generation_broker import broker_service

import pytest

from hummingbot.core.data_type.common import TradeType


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
@pytest.mark.parametrize('blocked', ['none', 'sdk', 'database'])
async def test_shutdown_cancels_original_owned_order_before_physical_join(database, product, blocked):
    t, service, _, state = await broker_service(database, hedge=product == 'SWAP')
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    work = closing = None
    original = t.session.request
    cancel_bodies = []

    async def http(**kwargs):
        if kwargs['url'].endswith('/cancel-order'):
            body = json.loads(kwargs['data'])
            cancel_bodies.append(body)
            response = Response()

            async def data():
                return dict(code='0', data=[dict(sCode='0', ordId=body['ordId'], clOrdId=body['clOrdId'])])
            response.json = data
            cancelled.set()
            return response
        if blocked == 'sdk' and kwargs['url'].endswith('/account/config'):
            entered.set()
            await release.wait()
        return await original(**kwargs)

    try:
        await service.arm_process()
        await state.control.sync(state.client.identity.registration, enable_active=True)
        if product == 'SPOT':
            await service.quote('shutdown-owned', TradeType.BUY, 0)
        else:
            await service.execute_hedge('shutdown-owned')
        attempt = dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=$1', product))
        assert attempt['state'] == 'ACKED' and attempt['exchange_order_id']
        before = await service.financial.snapshot(t.cfg.binding)
        claims = await service.source.capital.claims(t.cfg.binding)
        t.session.request = http
        if blocked == 'sdk':
            work = asyncio.create_task(service.connectors['okx']._api_get('/api/v5/account/config', is_auth_required=True))
        elif blocked == 'database':
            async def physical(c):
                await c.fetchval('SELECT 1')
                entered.set()
                await release.wait()
                return await c.fetchval('SELECT 1')
            work = asyncio.create_task(service.runtime._admit(physical, safety=False, operation='shutdown_test_original'))
        if work is not None:
            await asyncio.wait_for(entered.wait(), 2)
            # Exercise the same cleanup join with an actual owned SDK or native
            # database operation. No financial state/permission is synthesized.
            service._quote_task = work
        # This is the actual synchronous signal boundary, before the blocked
        # headless loop can call close(). Repeated stops must not resend.
        service.revoke()
        original_cancel = service._shutdown_cancel_task
        service.revoke()
        assert service._shutdown_cancel_task is original_cancel
        await asyncio.wait_for(cancelled.wait(), 2)
        assert service._revoked and not service._trading_ready()
        closing = asyncio.create_task(service.close())
        if work is not None:
            assert not work.done() and not closing.done()
        await asyncio.wait_for(asyncio.shield(service._shutdown_cancel_task), 2)
        assert service.shutdown_cancellation == dict(status='ATTEMPTED', identified=1, acknowledged=1, unresolved=0)
        assert cancel_bodies == [dict(instId='LIFE-USDT' + ('-SWAP' if product == 'SWAP' else ''),
                                      clOrdId=attempt['wire_id'], ordId=attempt['exchange_order_id'])]
        assert dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=$1', product)) == attempt
        if work is not None:
            after = await service.financial.snapshot(t.cfg.binding)
            assert (after.balance, after.budget) == (before.balance, before.budget)
            assert await service.source.capital.claims(t.cfg.binding) == claims
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_shutdown_does_not_invent_identity_for_accepted_unknown_send(database, product):
    t, service, _, state = await broker_service(database, hedge=product == 'SWAP')
    entered, release = asyncio.Event(), asyncio.Event()
    original = t.session.request
    task = None
    cancels = []

    async def http(**kwargs):
        response = await original(**kwargs)
        if kwargs['url'].endswith('/order') and kwargs.get('data'):
            entered.set()
            await release.wait()  # Exchange accepted; SDK still has no ACK.
        if kwargs['url'].endswith('/cancel-order'):
            cancels.append(kwargs)
        return response

    try:
        await service.arm_process()
        await state.control.sync(state.client.identity.registration, enable_active=True)
        t.session.request = http
        task = asyncio.create_task(service.quote('shutdown-unknown', TradeType.BUY, 0) if product == 'SPOT'
                                   else service.execute_hedge('shutdown-unknown'))
        waiting = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait((task, waiting), timeout=service._trading_model.quote_schedule_timeout_ms / 1000,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
            assert waiting in done
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        attempt = dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=$1', product))
        assert attempt['state'] == 'SEND_UNKNOWN' and attempt['exchange_order_id'] is None
        before = await service.financial.snapshot(t.cfg.binding)
        claims = await service.source.capital.claims(t.cfg.binding)
        service.revoke()
        await asyncio.wait_for(asyncio.shield(service._shutdown_cancel_task), 2)
        assert service.shutdown_cancellation == dict(status='ATTEMPTED', identified=0, acknowledged=0, unresolved=0)
        assert not cancels and not task.done()
        assert dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=$1', product)) == attempt
        after = await service.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await service.source.capital.claims(t.cfg.binding) == claims
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()
