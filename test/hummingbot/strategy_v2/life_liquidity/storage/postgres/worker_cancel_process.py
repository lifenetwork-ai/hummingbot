"""Actual foreground signal handling with a broker and a retained native ACK."""
import asyncio
import json
import sys
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_generation_broker import broker_service

import asyncpg

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.postgres import worker


async def launch(**kwargs):
    data = json.load(sys.stdin)
    assert data['migration_dsn'].startswith('postgresql://life_pg2_migrator@127.0.0.1:')
    assert data['root_dsn'].startswith('postgresql://postgres@127.0.0.1:')
    assert all('/life_pg2_' in data[key] for key in ('migration_dsn', 'root_dsn'))
    assert data['product'] in ('SPOT', 'SWAP')
    connection = await asyncpg.connect(data['migration_dsn'])
    t, service, _, state = await broker_service((connection, data['root_dsn'], data['migration_dsn']), hedge=data['product'] == 'SWAP')
    await service.arm_process()
    await state.control.sync(state.client.identity.registration, enable_active=True)
    if data['product'] == 'SPOT':
        await service.quote('foreground-cancel', TradeType.BUY, 0)
    else:
        await service.execute_hedge('foreground-cancel')
    original = t.session.request

    async def http(**request):
        if request['url'].endswith('/cancel-order'):
            body = json.loads(request['data'])
            worker.event('FIXTURE_EXACT_CANCEL', trading_permission=False, request=body)
            response = Response()

            async def payload():
                return dict(code='0', data=[dict(sCode='0', ordId=body['ordId'], clOrdId=body['clOrdId'])])
            response.json = payload
            return response
        if request['url'].endswith('/account/config'):
            worker.event('FIXTURE_ACKED_BLOCKED', trading_permission=False, account_id=str(t.cfg.account_id),
                         product=data['product'], order=t.service_orders[0])
            await asyncio.Event().wait()
        return await original(**request)

    t.session.request = http
    await kwargs['on_started'](service)
    # A real SDK request blocks the whole launch coroutine before it can call
    # close(). First synchronous signal revoke must initiate exact cancellation.
    await service.connectors['okx']._api_get('/api/v5/account/config', is_auth_required=True)
    raise AssertionError('original blocked operation unexpectedly returned')


original_init = worker.WorkerSupervisor.__init__


def install(self, config):
    original_init(self, config, launch=launch)


if __name__ == '__main__':
    worker.WorkerSupervisor.__init__ = install
    sys.exit(worker.main())
