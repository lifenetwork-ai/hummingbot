"""Physical death of the configured service; local disposable DB/fake HTTP only."""
import asyncio
import json
import os
import sys
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone


def encoded_config(config):
    values = {f.name: getattr(config, f.name) for f in fields(config)}
    return dict(values, account_id=str(config.account_id), database_lineage=str(config.database_lineage),
                binding=config.binding.json())


def decoded_config(values):
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig
    assert values['dsn'].startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in values['dsn']
    values = dict(values, binding=StateBinding.from_json(values['binding']))
    for key in ('account_id', 'database_lineage'):
        values[key] = UUID(values[key])
    return PgConfig(**values)


async def crash_service(database, phase, *, product='SPOT', provisional_quotes=False):
    """No inherited event loop, driver pool, SDK or financial capability."""
    assert product in ('SPOT', 'SWAP')
    assert type(provisional_quotes) is bool and (not provisional_quotes or product == 'SPOT')
    assert phase in (('capture', 'prepare', 'send', 'ack') if product == 'SPOT'
                     else ('capture', 'prepare', 'send', 'ack', 'terminal', 'settlement', 'release'))
    assert database[2].startswith('postgresql://life_pg2_migrator@127.0.0.1:') and '/life_pg2_' in database[2]
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(dict(
            migration_dsn=database[2], root_dsn=database[1], phase=phase, product=product,
            provisional_quotes=provisional_quotes)).encode()), 45)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
    return json.loads(stdout.decode().splitlines()[-1])


def cold_service_context(manifest, *, independent_fill_value=None):
    """Fresh actual SDKs; signed original fake truth, never a restored sender."""
    from copy import deepcopy
    from decimal import Decimal as D
    from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service import limits
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_shared_sdk_quota import sdk_pair
    from types import SimpleNamespace

    from controllers.generic.life_liquidity import LifeLiquidityConfig
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
    config = decoded_config(manifest['config'])
    spot, swap, http = sdk_pair()
    for sdk in (spot, swap):
        sdk.enable_protected_trading_pair('LIFE-USDT')
    PgSharedSdkQuota.install(spot=spot, swap=swap)
    connectors, calls = dict(okx=spot, okx_perpetual=swap), []
    swap._contract_sizes['LIFE-USDT'] = D('.25')
    swap._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    swap._perpetual_trading.set_leverage('LIFE-USDT', 2)

    async def original_exchange(**kwargs):
        calls.append(kwargs)
        assert kwargs['method'] == 'GET', 'Cold recovery attempted a new wire'
        assert kwargs['headers'].get('OK-ACCESS-SIGN')
        tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
        rows = {'config': [manifest['account_config']], 'balance': [manifest['balance']],
                'positions': manifest['positions'], 'bills-archive': manifest.get('bills', [dict(billId='100')]),
                'fills-history': manifest.get('fills', {}).get(params.get('instType'), []), 'orders-algo-pending': []}
        if tail in ('order', 'orders-pending'):
            data = manifest['orders']
            if tail == 'orders-pending':
                data = [row for row in data if row['state'] in ('live', 'partially_filled')]
                data = sorted(data, key=lambda row: int(row['ordId']), reverse=True)
            for key in ('clOrdId', 'instId', 'instType'):
                if params.get(key):
                    data = [row for row in data if row[key] == params[key]]
        else:
            data = rows[tail]
            if tail == 'fills-history':
                data = [r for r in data if all(not params.get(k) or r[k] == params[k] for k in ('instId', 'ordId'))]
        response = Response()

        async def body():
            return dict(code='0', data=deepcopy(data))
        response.json = body
        return response
    http.request = original_exchange

    async def reopen(native_trading):
        return await PgLiquidityService.open(
            LifeLiquidityConfig.model_validate_json(manifest['controller']), runtime_config=config,
            owner=OwnerToken.from_json(manifest['owner']), connectors=connectors,
            market_data_provider=MarketDataProvider(connectors),
            sources=tuple(EventSource(**s) for s in manifest['sources']), limits=limits(),
            clock=lambda: datetime.now(timezone.utc), independent_fill_value=independent_fill_value or (lambda *args: None),
            independent_cashflow_value=lambda *args: None, native_trading=native_trading)
    return SimpleNamespace(config=config, calls=calls, connectors=connectors, reopen=reopen)


async def run():
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

    import asyncpg

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
    data = json.load(sys.stdin)
    assert data['migration_dsn'].startswith('postgresql://life_pg2_migrator@127.0.0.1:')
    assert '/life_pg2_' in data['migration_dsn']
    connection = await asyncpg.connect(data['migration_dsn'])
    product, phase = data['product'], data['phase']
    options = {}
    if product == 'SWAP':
        from dataclasses import replace
        from decimal import Decimal
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import policy
        options['hedge_policy'] = replace(policy(), initial_target_base=Decimal('5')) if phase == 'capture' else policy()
    t, service = await configured((connection, data['root_dsn'], data['migration_dsn']), **options,
                                  provisional_quotes=data.get('provisional_quotes', False),
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                    sources=[asdict(r.source) for r in service.reporting.events], phase=data['phase'],
                    process_id=os.getpid(), account_config=t.config, balance=t.balance, positions=t.positions, product=product)

    def die():
        print(json.dumps(dict(manifest, orders=t.service_orders)), flush=True)
        os._exit(73)

    original = service.runtime._commit
    target = ({'capture': 'spot_capture_quote_action', 'prepare': 'spot_action_prepare'}.get(phase) if product == 'SPOT'
              else {'terminal': 'swap_terminal_command', 'release': 'shared_release_command'}.get(phase))
    if data.get('provisional_quotes', False):
        target = {'capture': 'quote_birth_issue', 'prepare': 'quote_birth_prepare'}.get(phase)

    async def committed(transaction):
        await original(transaction)
        if target is not None and _current_trace.get().operation == target:
            die()
    service.runtime._commit = committed
    if product == 'SWAP':
        plan = PgHedgeRepository.plan

        async def planned(repository, command, **kwargs):
            result = await plan(repository, command, **kwargs)
            preflight = 'hedge-source-plan:' + fingerprint(['whole-service-hedge'])
            if phase == 'capture' and command.operation_id == preflight:
                die()
            if phase == 'prepare' and command.operation_id == 'whole-service-hedge' and result.child_operation_id is not None:
                die()
            return result
        PgHedgeRepository.plan = planned
        settle = service.hedge.repository.settle

        async def settled(*args, **kwargs):
            result = await settle(*args, **kwargs)
            if phase == 'settlement' and result.state == 'TERMINAL':
                die()
            return result
        service.hedge.repository.settle = settled
    if phase == 'send':
        http = t.session.request

        async def accepted_without_ack(**kwargs):
            response = await http(**kwargs)
            if kwargs['method'] == 'POST' and kwargs['url'].endswith('/order'):
                # Fake exchange has accepted original bytes. No response reaches
                # the service, durable ingress or SDK tracking before death.
                die()
            return response
        t.session.request = accepted_without_ack
    if product == 'SPOT':
        await service.quote('whole-service-original', TradeType.BUY, 0)
    else:
        await service.execute_hedge('whole-service-hedge')
        if phase in ('terminal', 'settlement', 'release'):
            order, = t.service_orders
            order['state'] = 'canceled'
            now = datetime.now(timezone.utc).timestamp()
            asyncio.create_task(t.swap_connector._user_stream_event_listener())
            t.swap_connector._set_current_timestamp(now)
            await t.swap_connector._user_stream_tracker.user_stream.put(dict(
                arg=dict(channel='orders'), data=[dict(order, tradeId='', uTime=str(int(now * 1000)))]))
            async with asyncio.timeout(5):
                while order['clOrdId'] in t.swap_connector._order_tracker.active_orders:
                    await asyncio.sleep(.001)
            await service.settle_hedge('whole-service-close', 'whole-service-hedge')
    assert phase == 'ack', 'Requested crash boundary was not reached'
    die()


if __name__ == '__main__':
    asyncio.run(run())
