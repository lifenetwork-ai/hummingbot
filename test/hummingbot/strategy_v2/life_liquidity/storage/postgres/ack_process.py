"""Exit the actual ACK ingress transaction process at physical COMMIT."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ack_ingress import PgAckIngress
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));ingress=PgAckIngress(repo)
    original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    owner=OwnerToken.from_json(d['owner'])
    if d['phase']=='capture':
        await ingress.capture(PrepareSpot.from_json(d['prepare']),'123',owner=owner,request_sha256=d['request_sha256'])
    else:
        await ingress.mark_applied(await ingress.lookup(d['ingress_id']),owner=owner)
asyncio.run(run())
'''


async def exit_ack(cfg, prepare, owner, request_sha256, ingress_id, phase, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert phase in ('capture', 'delivery') and type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, prepare=prepare.json(), owner=owner.json(), request_sha256=request_sha256,
                ingress_id=ingress_id, phase=phase, committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
