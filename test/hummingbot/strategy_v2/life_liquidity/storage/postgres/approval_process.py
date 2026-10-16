"""Real process-exit fixture with synthetic public operator keys and bounds."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,asyncpg,json,os,sys
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ApproveSpotCashflow,OperatorApproval,OperatorVerifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.postgres.approval_import import PgCashflowApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    d=json.load(sys.stdin);cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    c=await asyncpg.connect(d['migration_dsn'])
    verifier=OperatorVerifier({k:bytes.fromhex(v) for k,v in d['keys'].items()},{k:frozenset(v) for k,v in d['roles'].items()})
    importer=PgCashflowApprovalImporter(c,financial,verifier=verifier,max_fact_age_ms=10000)
    value=d['command'];bill=value['bill'];bill['signed_amount']=Decimal(bill['signed_amount'])
    command=ApproveSpotCashflow(value['operation_id'],ApprovedSpotCashflow(**bill))
    auth=d['approval'];auth['binding']=StateBinding.from_json(auth['binding'])
    for key in ('issued_at','expires_at'):auth[key]=datetime.fromisoformat(auth[key])
    approval=OperatorApproval(**auth);original=importer._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    importer._commit=die
    await importer.approve(command,approval=approval,cut=FinancialCut.from_json(d['cut']),facts=SpotFinancialRecoveryFacts.from_json(d['facts']))
asyncio.run(run())
'''


async def exit_approval(cfg, migration_dsn, command, approval, cut, facts, verifier, committed):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert migration_dsn.startswith('postgresql://life_pg2_migrator@127.0.0.1:') and type(committed) is bool
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, migration_dsn=migration_dsn, command=command.json(), approval={**approval.body(), 'signature': approval.signature},
                cut=cut.json(), facts=facts.json(), keys={k: v.hex() for k, v in verifier.keys.items()},
                roles={k: sorted(v) for k, v in verifier.roles.items()}, committed=committed)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
