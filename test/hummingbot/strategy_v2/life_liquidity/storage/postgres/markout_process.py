"""Real process exit around atomic markout observation COMMIT; loopback only."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.markout import PgSpotMarkoutRepository
from datetime import datetime
from decimal import Decimal
from hummingbot.strategy_v2.life_liquidity.storage.markout import ObserveSpotMarkout
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));store=PgSpotMarkoutRepository(repo,max_rows=64,max_bytes=1048576)
    value=d['command'];value['independent_price_usdt']=Decimal(value['independent_price_usdt'])
    value['observed_at']=datetime.fromisoformat(value['observed_at']);command=ObserveSpotMarkout(**value)
    owner=OwnerToken.from_json(d['owner']);original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await store.observe(command,cut=(await repo.snapshot(repo.binding)).cut,owner=owner)
asyncio.run(run())
'''


async def exit_markout(cfg, command, owner, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, command=command.json(), owner=owner.json(), committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
