"""Real process exit around atomic shared capital allocation COMMIT; loopback only."""
import asyncio
import json
import sys
from dataclasses import asdict, fields

from hummingbot.strategy_v2.life_liquidity.shared_capital import _json

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim,CapitalSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
from decimal import Decimal
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    owner=OwnerToken.from_json(d['owner']);value=d['snapshot']
    for key in ('spot_life_base','spot_cash_quote','long_base','short_base','collateral_quote','initial_margin_quote','maintenance_margin_quote','funding_liability_quote','mark_price_usdt','index_price_usdt'):value[key]=Decimal(value[key])
    value['pending']=();snapshot=CapitalSnapshot(**value)
    claim=d['command']['claim'];claim['quantity_base']=Decimal(claim['quantity_base']);claim['price_usdt']=Decimal(claim['price_usdt'])
    command=ReserveCapital(d['command']['operation_id'],CapitalClaim(**claim))
    store=PgSharedCapitalRepository(repo,owner=owner,observation=lambda:snapshot,max_rows=64,max_bytes=1048576)
    cut=await store.cut(repo.binding);original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await store.reserve(command,cut,owner)
asyncio.run(run())
'''


async def exit_shared_capital(cfg, command, owner, snapshot, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, command=_json(asdict(command)), owner=owner.json(), snapshot=_json(asdict(snapshot)), committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
