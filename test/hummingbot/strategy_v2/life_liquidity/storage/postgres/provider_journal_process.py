"""Actual process death around diagnostic page COMMIT on synthetic local PG."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for k in ('account_id','database_lineage'):cfg[k]=UUID(cfg[k])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    journal=PgProviderJournal(repo)
    if d['collection']:await journal.capture_collection(tuple(d['body']))
    else:await journal.capture(d['body'])
asyncio.run(run())
'''


async def exit_provider_page(cfg, body, committed, *, collection=False):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert type(committed) is bool
    assert type(collection) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(dict(config=values, body=body, committed=committed, collection=collection)).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
