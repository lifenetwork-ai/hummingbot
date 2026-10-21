"""Physical death of the configured service; local disposable DB/fake HTTP only."""
import asyncio
import json
import os
import sys
from dataclasses import asdict, fields
from datetime import timedelta


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


async def crash_service(database, phase):
    """No inherited event loop, driver pool, SDK or financial capability."""
    assert phase in ('capture', 'prepare', 'send', 'ack')
    assert database[2].startswith('postgresql://life_pg2_migrator@127.0.0.1:') and '/life_pg2_' in database[2]
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(dict(
            migration_dsn=database[2], root_dsn=database[1], phase=phase)).encode()), 45)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
    return json.loads(stdout.decode().splitlines()[-1])


async def run():
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

    import asyncpg

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
    data = json.load(sys.stdin)
    assert data['migration_dsn'].startswith('postgresql://life_pg2_migrator@127.0.0.1:')
    assert '/life_pg2_' in data['migration_dsn']
    connection = await asyncpg.connect(data['migration_dsn'])
    t, service = await configured((connection, data['root_dsn'], data['migration_dsn']),
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                    sources=[asdict(r.source) for r in service.reporting.events], phase=data['phase'],
                    process_id=os.getpid(), account_config=t.config, balance=t.balance, positions=t.positions)

    def die():
        print(json.dumps(dict(manifest, orders=t.service_orders)), flush=True)
        os._exit(73)

    original = service.runtime._commit
    target = {'capture': 'spot_capture_quote_action', 'prepare': 'spot_action_prepare'}.get(data['phase'])

    async def committed(transaction):
        await original(transaction)
        if target is not None and _current_trace.get().operation == target:
            die()
    service.runtime._commit = committed
    if data['phase'] == 'send':
        http = t.session.request

        async def accepted_without_ack(**kwargs):
            response = await http(**kwargs)
            if kwargs['method'] == 'POST' and kwargs['url'].endswith('/order'):
                # Fake exchange has accepted original bytes. No response reaches
                # the service, durable ingress or SDK tracking before death.
                die()
            return response
        t.session.request = accepted_without_ack
    await service.quote('whole-service-original', TradeType.BUY, 0)
    assert data['phase'] == 'ack', 'Requested crash boundary was not reached'
    die()


if __name__ == '__main__':
    asyncio.run(run())
