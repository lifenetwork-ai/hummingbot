"""Real process exit at received-command/accounting/delivery COMMIT."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_ingress import PgFinancialIngress
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));intake=PgFinancialIngress(repo)
    value=d['command']
    if d['kind']=='fill':command=FillSpot.from_json(value)
    else:
        for key in ('signed_amount','independent_price_usdt'):value[key]=Decimal(value[key])
        value['observed_at']=datetime.fromisoformat(value['observed_at']);command=CashflowSpot(**value)
    owner=OwnerToken.from_json(d['owner']);original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    if d['phase']=='capture':await intake.capture(command,owner=owner)
    elif d['phase']=='apply':
        fn=repo.apply_fills if d['kind']=='fill' else repo.apply_cashflow
        await fn(command,cut=(await repo.observation_cut(repo.binding)).cut,owner=owner)
    else:await intake.mark_applied(await intake.lookup(d['ingress_id']),financial_operation_id=command.operation_id,owner=owner)
asyncio.run(run())
'''


async def exit_financial_ingress(cfg, command, owner, ingress_id, kind, phase, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert kind in ('fill', 'cashflow') and phase in ('capture', 'apply', 'delivery') and type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, command=command.json(), owner=owner.json(), ingress_id=ingress_id, kind=kind,
                phase=phase, committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
