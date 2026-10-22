"""Opt-in disposable PostgreSQL only; never consume the repository .env."""
import os
from collections import deque
from uuid import uuid4

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


@pytest.fixture
def reference_migrations_100(tmp_path):
    """Exact target of retained pre-101 differential proofs, not latest schema."""
    directory = tmp_path / 'reference_migrations_100'
    directory.mkdir()
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) <= 100:
            (directory / path.name).write_bytes(path.read_bytes())
    return directory


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.failed:
        samples = [repr(sample) for store in getattr(item, '_pg_diagnostic_runtimes', ()) for sample in store.diagnostics]
        if samples:
            report.sections.append(('PostgreSQL bounded physical diagnostics', '\n'.join(samples)))
        codes = getattr(item, '_pg_reason_codes', ())
        if codes:
            report.sections.append(('PostgreSQL normalized reason codes', '\n'.join(codes)))


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
    # SDK failure hints intentionally carry no raw driver exception. Preserve
    # only the already sanitized application codes for failed-test triage;
    # never retain driver messages, SQL, parameters or credential data.
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    original_error = runtime.sql_error
    request.node._pg_reason_codes = deque(maxlen=64)

    def normalized(error):
        result = original_error(error)
        request.node._pg_reason_codes.append(str(result)[:128])
        return result
    monkeypatch.setattr(runtime, 'sql_error', normalized)
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_pg2_test')
    admin = await asyncpg.connect(dsn)
    name = 'life_pg2_' + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
    function_profile = os.environ.get('LIFE_PG_TEST_FUNCTION_PROFILE') == '1'
    if function_profile:
        await admin.execute(f'ALTER DATABASE "{name}" SET track_functions=\'all\'')
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
        if function_profile:
            profile = await asyncpg.connect(root + '/' + name)
            try:
                rows = await profile.fetch('''SELECT funcname,calls,round(total_time::numeric,3) AS total_ms,
                    round(self_time::numeric,3) AS self_ms FROM pg_stat_user_functions
                    WHERE schemaname='life_financial' AND calls>0 ORDER BY self_time DESC LIMIT 30''')
                import json
                print('bounded_native_function_profile=' + json.dumps([dict(r) for r in rows], default=str))
            finally:
                await profile.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
