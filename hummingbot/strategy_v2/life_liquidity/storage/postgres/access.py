"""Migration-owner provisioning of existing least-privilege login roles.

No role, account, money or grant bootstrap. Intended for the privileged migration/
import stage; runtime has no permission to change these mappings or grants.
"""
import re
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def role_name(value):
    if type(value) is not str or re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value) is None:
        raise StorageError('PG_ROLE_NAME_INVALID')
    return value


async def provision_access(connection, *, principal, account_id, write):
    role_name(principal)
    if type(account_id) is not UUID or type(write) is not bool:
        raise StorageError('PG_ACCESS_INVALID')
    async with connection.transaction():
        owner = await connection.fetchval("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial'")
        if owner != await connection.fetchval('SELECT current_user'):
            raise StorageError('PG_MIGRATION_OWNER_REQUIRED')
        role = await connection.fetchrow('SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin FROM pg_roles WHERE rolname=$1', principal)
        if (role is None or not role['rolcanlogin'] or any(role[k] for k in ('rolsuper', 'rolbypassrls', 'rolcreaterole', 'rolcreatedb', 'rolreplication'))
                or await connection.fetchval("SELECT pg_has_role($1,$2,'MEMBER')", principal, owner)
                or await connection.fetchval("SELECT has_database_privilege($1,current_database(),'CREATE')", principal)):
            raise StorageError('PG_ROLE_PRIVILEGES_UNSAFE')
        if not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.accounts WHERE account_id=$1)', account_id):
            raise StorageError('PG_ACCESS_ACCOUNT_MISSING')
        # Names were validated above; SQL values remain bound parameters.
        await connection.execute(f'GRANT USAGE ON SCHEMA life_financial,life_storage,life_migrations TO "{principal}"')
        tables = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='life_financial' AND tablename<>'access_bindings'")
        for row in tables:
            name = role_name(row['tablename'])
            await connection.execute(f'GRANT SELECT ON life_financial."{name}" TO "{principal}"')
        await connection.execute(f'GRANT SELECT ON life_storage.storage_metadata,life_migrations.schema_migrations TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.can_access(uuid,boolean) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO "{principal}"')
        if write:
            await connection.execute(f'''GRANT EXECUTE ON FUNCTION life_financial.guard_account(uuid,jsonb,jsonb,bigint),
                life_financial.allocate_balance(uuid,jsonb,jsonb,bigint,life_financial.identity,life_financial.digest,text,life_financial.positive_amount),
                life_financial.spot_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb),
                life_financial.spot_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity)
                TO "{principal}"''')
        await connection.execute('''INSERT INTO life_financial.access_bindings(principal,account_id,can_write) VALUES($1,$2,$3)
            ON CONFLICT(principal,account_id) DO UPDATE SET can_write=EXCLUDED.can_write''', principal, account_id, write)
