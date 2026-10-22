"""Qualification fixtures preserve exact schema and per-case isolation."""
import os
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


async def catalog(c):
    return [await c.fetch(query) for query in (
        '''SELECT version,name,checksum FROM life_migrations.schema_migrations ORDER BY version''',
        '''SELECT p.oid::regprocedure::text AS signature,pg_get_functiondef(p.oid) AS definition,
            p.proacl,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='life_financial' ORDER BY signature''',
        '''SELECT c.relname,c.relkind,c.relacl,c.relowner FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='life_financial' ORDER BY c.relname''',
        '''SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='life_financial' ORDER BY tablename,indexname''',
        '''SELECT c.relname,a.attname,a.atttypid::regtype::text AS type,a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid) AS default_value
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='life_financial' AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum''',
        '''SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) AS definition
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='life_financial' ORDER BY c.relname,k.conname''',
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [99], indirect=True)
async def test_direct_upgrade_and_current_template_have_identical_schema_and_grants(database, migration_template):
    import asyncpg
    c, _, _ = database
    pending = await migrate(c, action='status')
    assert pending['pending'][0].startswith('000100_')
    assert len(pending['verified']) == 99
    assert (await migrate(c, action='up'))['applied'] == pending['pending']
    admin = await asyncpg.connect(os.environ['LIFE_PG_TEST_DSN'])
    name = 'life_pg2_clone_probe_' + uuid4().hex
    created = False
    clone = None
    try:
        assert await admin.fetchval('SELECT NOT datallowconn FROM pg_database WHERE datname=$1', migration_template)
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator TEMPLATE "{migration_template}"')
        created = True
        clone = await asyncpg.connect(database[2].rsplit('/', 1)[0] + '/' + name)
        assert await catalog(c) == await catalog(clone)
        assert (await migrate(clone, action='up'))['applied'] == []
        assert await clone.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
        await c.execute('CREATE TABLE public.fixture_isolation_probe (id int)')
        assert await clone.fetchval("SELECT to_regclass('public.fixture_isolation_probe')") is None
    finally:
        if clone is not None:
            await clone.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
