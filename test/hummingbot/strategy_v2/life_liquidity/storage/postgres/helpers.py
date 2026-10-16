"""Synthetic administrator-only account/owner seeds; no runtime bootstrap."""
from datetime import timedelta
from uuid import UUID, uuid4

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import provision_access


async def seed(database):
    c, dsn, _ = database
    account, lineage, session = uuid4(), uuid4(), uuid4()
    db_lineage = UUID(str(await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')))
    now = await c.fetchval('SELECT clock_timestamp()')
    owner = OwnerToken(uuid4(), uuid4(), 1, session, now - timedelta(seconds=1), now + timedelta(seconds=30))
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', lineage, 1, 'a' * 64, 'b' * 64)
    async with c.transaction():
        await c.execute('''INSERT INTO life_financial.accounts(account_id,database_lineage,environment,exchange,account_uid,risk_pool,
            lineage,schema_version,policy_hash,config_hash) VALUES($1,$2,'OFFLINE_SIMULATION','okx','12345','usdt',$3,1,$4,$5)''',
                        account, db_lineage, lineage, binding.policy_hash, binding.config_hash)
        await c.execute("INSERT INTO life_financial.policies VALUES($1,$2,'{}')", account, binding.policy_hash)
        await c.execute("INSERT INTO life_financial.configurations VALUES($1,1,$2,$3,'{}')", account, binding.config_hash, binding.policy_hash)
        await c.execute("INSERT INTO life_financial.sessions VALUES($1,$2,1,1,$3,$4,'NATIVE','synthetic','{}')",
                        account, session, owner.valid_from, now + timedelta(seconds=60))
        await c.execute('''UPDATE life_financial.accounts SET mode='ACTIVE',halted=false,owner_process=$2,owner_generation=$3,
            owner_epoch=1,current_session=$4,owner_since=$5,owner_until=$6 WHERE account_id=$1''',
                        account, owner.process_id, owner.generation, session, owner.valid_from, owner.expires_at)
        await c.execute("INSERT INTO life_financial.balances(account_id,asset,total) VALUES($1,'USDT',10),($1,'LIFE',5)", account)
    await provision_access(c, principal='life_pg2_runtime', account_id=account, write=True)
    await provision_access(c, principal='life_pg2_reporter', account_id=account, write=False)
    return account, db_lineage, binding, owner, dsn.replace('postgres@', 'life_pg2_runtime@')


async def backend(database, **changes):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
    account, db_lineage, binding, owner, dsn = await seed(database)
    from dataclasses import replace
    config = PgConfig(dsn=dsn, principal='life_pg2_runtime', account_id=account, database_lineage=db_lineage, binding=binding,
                      server_major=16, min_size=1, max_size=2, max_pending=4, safety_size=1, safety_max_pending=2,
                      acquire_timeout_ms=1000, statement_timeout_ms=1000, transaction_timeout_ms=3000,
                      idle_transaction_timeout_ms=4000, shutdown_timeout_ms=2000, retry_count=2, retry_base_ms=10,
                      local_insecure=True)
    config = replace(config, **changes)
    return await PgRuntime.open(config), config, owner
