"""Opt-in disposable PostgreSQL only; never consume the repository .env."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.failed:
        samples = [repr(sample) for store in getattr(item, '_pg_diagnostic_runtimes', ()) for sample in store.diagnostics]
        if samples:
            report.sections.append(('PostgreSQL bounded physical diagnostics', '\n'.join(samples)))


@pytest_asyncio.fixture
async def database(request, monkeypatch, tmp_path):
    dsn = os.environ.get('LIFE_PG_TEST_DSN')
    if not dsn:
        pytest.skip('Requires run_postgres.py disposable database')
    asyncpg = pytest.importorskip('asyncpg')
    # Test-only export on failure, outside financial admission/COMMIT. Retains
    # the bounded, redacted samples after test cleanup, never a pool/DSN dump.
    request.node._pg_diagnostic_runtimes = []
    original = PgRuntime.__init__

    def tracked(store, config):
        original(store, config)
        request.node._pg_diagnostic_runtimes.append(store)
    monkeypatch.setattr(PgRuntime, '__init__', tracked)
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_pg2_test')
    admin = await asyncpg.connect(dsn)
    name = 'life_pg2_' + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
    root = dsn.rsplit('/', 1)[0]
    migration_dsn = root.replace('postgres@', 'life_pg2_migrator@') + '/' + name
    c = await asyncpg.connect(migration_dsn)
    try:
        directory = DEFAULT_DIRECTORY
        if hasattr(request, 'param'):
            # Explicit upgrade-test starting point, on this disposable DB only.
            assert type(request.param) is int and request.param > 0
            directory = tmp_path / 'prior_migrations'
            directory.mkdir()
            for path in DEFAULT_DIRECTORY.glob('*.sql'):
                if int(path.name.split('_', 1)[0]) <= request.param:
                    (directory / path.name).write_bytes(path.read_bytes())
        await migrate(c, directory, action='up')
        yield c, root + '/' + name, migration_dsn
    finally:
        await c.close()
        await admin.close()
        admin = await asyncpg.connect(dsn)
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
