"""Real process termination around carry evidence COMMIT; disposable loopback only."""
import asyncio
import json
import sys
from dataclasses import asdict, fields

from hummingbot.strategy_v2.life_liquidity.shared_capital import _json

CHILD = r'''
import asyncio,json,os,sys
from uuid import UUID
from decimal import Decimal as D
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot,MarginTier
from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryAccount,CarryBundle,CarryPrice,CarryFunding,CarryTerms,FundingPayment
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    owner=OwnerToken.from_json(d['owner']);b=d['bundle'];s=b['account']['snapshot']
    for key in ('spot_life_base','spot_cash_quote','long_base','short_base','collateral_quote','initial_margin_quote','maintenance_margin_quote','funding_liability_quote','mark_price_usdt','index_price_usdt'):s[key]=D(s[key])
    assert not s['pending'];s['pending']=();snapshot=CapitalSnapshot(**s)
    account=CarryAccount(snapshot,tuple(b['account']['included_funding_bill_ids']),b['account']['settled_funding_included'],b['account']['unsettled_funding_only'])
    for key in ('mark','index'):b[key]['price_usdt']=D(b[key]['price_usdt'])
    b['funding']['rate']=D(b['funding']['rate'])
    b['funding']['payments']=tuple(FundingPayment(p['bill_id'],p['settled_at_ms'],D(p['signed_payment_quote'])) for p in b['funding']['payments'])
    for key in ('maker_fee_rate','taker_fee_rate'):b['terms'][key]=D(b['terms'][key])
    b['terms']['tiers']=tuple(MarginTier(**{k:D(v) for k,v in t.items()}) for t in b['terms']['tiers'])
    bundle=CarryBundle(account,CarryPrice(**b['mark']),CarryPrice(**b['index']),CarryFunding(**b['funding']),CarryTerms(**b['terms']))
    capital=PgSharedCapitalRepository(repo,owner=owner,observation=lambda:snapshot,max_rows=64,max_bytes=1048576)
    carry=PgCarryRepository(capital,observation=lambda:bundle,max_rows=64,max_bytes=1048576)
    cut=await carry.cut(repo.binding);original=repo._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await carry.observe('boundary',cut=cut,owner=owner)
asyncio.run(run())
'''


async def exit_carry(cfg, owner, bundle, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, owner=owner.json(), bundle=_json(asdict(bundle)), committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
