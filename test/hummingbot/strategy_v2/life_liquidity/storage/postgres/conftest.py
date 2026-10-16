"""Opt-in disposable PostgreSQL only; never consume the repository .env."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


@pytest_asyncio.fixture
async def database():
    dsn = os.environ.get('LIFE_PG_TEST_DSN')
    if not dsn:
        pytest.skip('Requires run_postgres.py disposable database')
    asyncpg = pytest.importorskip('asyncpg')
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_pg2_test')
    admin = await asyncpg.connect(dsn)
    name = 'life_pg2_' + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
    root = dsn.rsplit('/', 1)[0]
    migration_dsn = root.replace('postgres@', 'life_pg2_migrator@') + '/' + name
    c = await asyncpg.connect(migration_dsn)
    try:
        await migrate(c, action='up')
        yield c, root + '/' + name, migration_dsn
    finally:
        await c.close()
        await admin.close()
        admin = await asyncpg.connect(dsn)
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
