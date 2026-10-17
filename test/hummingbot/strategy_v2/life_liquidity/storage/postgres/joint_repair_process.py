"""Actual child exits around signed repair COMMIT, disposable loopback only."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import ResolveJointSources,PgJointRepairRepository
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));owner=OwnerToken.from_json(d['owner'])
    capital=PgSharedCapitalRepository(financial,owner=owner,observation=lambda:None,max_rows=64,max_bytes=1048576)
    repo=PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)));command=ResolveJointSources.from_json(d['command'])
    original=financial._runtime._commit
    async def die(tx):
        assert await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id=$1",command.operation_id)=='REPAIRED'
        if d['committed']:await original(tx)
        os._exit(73)
    financial._runtime._commit=die
    await repo.resolve(command,cut=FinancialCut.from_json(d['cut']),owner=owner)
asyncio.run(run())
'''


async def exit_joint_repair(cfg, command, cut, owner, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, command=command.json(), cut=cut.json(), owner=owner.json(), committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
