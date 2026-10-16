"""Real disposable-process exit harness, with explicitly synthetic read limits."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection,EventSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport
from hummingbot.strategy_v2.life_liquidity.storage.postgres.positions import PgPositionRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    if data['kind']=='event':
        command=EventProjection.from_json(data['command'])
        repo=PgEventRepository(financial,source=command.source,max_rows=64,max_read_bytes=1048576)
    elif data['kind']=='position':
        command=PositionReport.from_json(data['command'])
        repo=PgPositionRepository(financial,controller_id=command.controller_id,max_rows=64,max_read_bytes=1048576)
    else:raise AssertionError('unsupported disposable report type')
    original=financial._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    financial._runtime._commit=die
    kwargs=dict(cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
    if data['phase']=='stage':await repo.stage(command,**kwargs)
    else:await repo.apply(command.operation_id,**kwargs)
asyncio.run(run())
'''


async def exit_report(cfg, command, owner, cut, phase, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert phase in ('stage', 'apply') and type(committed) is bool
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport
    assert type(command) in (EventProjection, PositionReport)
    kind = 'event' if type(command) is EventProjection else 'position'
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = json.dumps(dict(config=values, command=command.json(), owner=owner.json(), cut=cut.json(), phase=phase, committed=committed, kind=kind))
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(data.encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
