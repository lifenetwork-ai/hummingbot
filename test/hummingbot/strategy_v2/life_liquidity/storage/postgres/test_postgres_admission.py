"""Primary admission metadata comes from one statement, without cached privileges."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


class CountedConnection:
    def __init__(self, connection):
        self.connection, self.statements = connection, 0

    async def fetchrow(self, *args):
        self.statements += 1
        return await self.connection.fetchrow(*args)

    async def fetchval(self, *args):
        self.statements += 1
        return await self.connection.fetchval(*args)

    async def fetch(self, *args):
        self.statements += 1
        return await self.connection.fetch(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize('write', [True, False])
async def test_native_admission_reads_coherent_identity_durability_schema_and_privileges_once(database, write):
    runtime, cfg, owner = await backend(database)
    reader = None
    try:
        if not write:
            cfg = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
            reader = await PgRuntime.open(cfg)
        selected = runtime if write else reader
        async with selected._pools[True].acquire() as c:
            counted = CountedConnection(c)
            await selected._validate_connection(counted)
            assert counted.statements == 1
    finally:
        if reader is not None:
            await reader.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_native_admission_never_caches_a_previously_valid_write_profile(database):
    runtime, cfg, owner = await backend(database)
    try:
        async with runtime._pools[True].acquire() as c:
            await runtime._validate_connection(c)
            await database[0].execute('REVOKE EXECUTE ON FUNCTION life_financial.spot_observe_markout(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) FROM life_pg2_runtime')
            with pytest.raises(StorageError, match='PG_ROLE_PROFILE_INVALID'):
                await runtime._validate_connection(c)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds,reason', [(-100, 'PG_QUOTE_CAPTURE_STALE'), (100, 'PG_QUOTE_CAPTURE_FUTURE')])
async def test_capture_time_denials_are_distinct_from_payload_identity_mismatches(database, seconds, reason):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import (
        bridge,
        command,
    )
    at = await database[0].fetchval('SELECT clock_timestamp()')
    repo, cfg, owner, action = await bridge(database, payload_changes={'timestamp': at.timestamp() + seconds})
    try:
        value = await command(database, cfg, owner, action)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match=reason):
            await repo.prepare_quote_action(value, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog', ['tables', 'roles'])
async def test_temporary_catalog_names_cannot_hide_real_unsafe_actor_privileges(database, catalog):
    import asyncpg
    runtime, cfg, owner = await backend(database)
    admin = await asyncpg.connect(database[1])
    try:
        async with runtime._pools[True].acquire() as c:
            if catalog == 'tables':
                await c.execute('CREATE TEMP TABLE pg_tables(schemaname text,tablename text)')
                await admin.execute('GRANT UPDATE ON life_financial.balances TO life_pg2_runtime')
            else:
                await c.execute('''CREATE TEMP TABLE pg_roles AS SELECT current_user::text AS rolname,
                    false AS rolsuper,false AS rolbypassrls,false AS rolcreaterole,false AS rolcreatedb,false AS rolreplication''')
                await admin.execute('ALTER ROLE life_pg2_runtime BYPASSRLS')
            with pytest.raises(StorageError, match='PG_RUNTIME_PRIVILEGES_UNSAFE'):
                await runtime._validate_connection(c)
    finally:
        await admin.execute('ALTER ROLE life_pg2_runtime NOBYPASSRLS')
        await admin.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_changed_session_search_path_is_not_a_validated_callback_context(database):
    runtime, cfg, owner = await backend(database)
    try:
        async with runtime._pools[True].acquire() as c:
            await c.execute('SET search_path=pg_temp,pg_catalog')
            with pytest.raises(StorageError, match='PG_PRIMARY_CONTEXT_INVALID'):
                await runtime._validate_connection(c)
    finally:
        await runtime.close()
