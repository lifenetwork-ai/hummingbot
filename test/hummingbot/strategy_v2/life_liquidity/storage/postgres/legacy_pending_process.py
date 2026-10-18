"""Actual process exit at native legacy pending publication; loopback only."""
import asyncio
import json
import sys
from dataclasses import fields

CHILD = r'''
import asyncio,json,os,sys,asyncpg
from uuid import UUID
from datetime import datetime
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding,OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorVerifier,OperatorApproval
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import ImportLegacyApplying,PgLegacyApplyingImporter
async def run():
    d=json.loads(sys.stdin.readline());cfg=d['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));owner=OwnerToken.from_json(d['owner'])
    capital=PgSharedCapitalRepository(financial,owner=owner,observation=lambda:None,max_rows=64,max_bytes=1048576)
    batch=PgJointBatchRepository(PgSwapRepository(capital))
    connection=await asyncpg.connect(d['migration_dsn'])
    verifier=OperatorVerifier({k:bytes.fromhex(v) for k,v in d['keys'].items()}, {k:frozenset(v) for k,v in d['roles'].items()})
    importer=PgLegacyApplyingImporter(connection,batch,verifier=verifier,source_verifier=verifier,max_fact_age_ms=5000)
    print('READY',flush=True)
    d.update(json.loads(sys.stdin.readline()))
    value=d['approval'];value['binding']=cfg['binding']
    for k in ('issued_at','expires_at'):value[k]=datetime.fromisoformat(value[k])
    approval=OperatorApproval(**value)
    original=financial._runtime._commit
    async def die(tx):
        if d['committed']:await original(tx)
        os._exit(73)
    financial._runtime._commit=die
    await importer.import_pending(ImportLegacyApplying.from_json(d['command']),source_store=None,approval=approval,
        cut=FinancialCut.from_json(d['cut']),facts=SpotFinancialRecoveryFacts.from_json(d['facts']))
asyncio.run(run())
'''


async def exit_pending(cfg, migration_dsn, owner, command, verifier, committed, capture):
    assert cfg.dsn.startswith('postgresql://life_pg2_runtime@127.0.0.1:') and '/life_pg2_' in cfg.dsn
    assert migration_dsn == cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_migrator@')
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = dict(config=values, migration_dsn=migration_dsn, owner=owner.json(), command=command.json(), committed=committed,
                keys={k: v.hex() for k, v in verifier.keys.items()}, roles={k: sorted(v) for k, v in verifier.roles.items()})
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        process.stdin.write(json.dumps(data).encode() + b'\n')
        await process.stdin.drain()
        ready = await asyncio.wait_for(process.stdout.readline(), 15)
        if ready != b'READY\n':
            raise AssertionError((await process.stderr.read()).decode())
        # Capture/approve after child imports and initial primary admission.
        # A changing time-sensitive readiness cut cannot be pre-signed and
        # carried through unrelated interpreter startup as though still current.
        cut, facts, approval = await capture()
        payload = dict(cut=cut.json(), facts=facts.json(), approval={**approval.body(), 'signature': approval.signature})
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(payload).encode() + b'\n'), 15)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 73, stderr.decode()
    return cut, facts, approval
