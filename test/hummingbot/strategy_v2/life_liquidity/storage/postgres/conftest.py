"""Opt-in disposable PostgreSQL only; never consume the repository .env."""
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


class _ImmediateNativeFailures:
    def __init__(self, config):
        self.config = config

    def pytest_runtest_logreport(self, report):
        # Preserve the ordinary final summary/XML, but make the original
        # failure visible while the remaining long aggregate gate continues.
        if report.failed:
            terminal = self.config.pluginmanager.get_plugin('terminalreporter')
            if terminal is not None:
                terminal.write_sep('!', 'PostgreSQL failure: ' + report.nodeid)
                terminal.write_line(str(report.longrepr))
                for name, body in report.sections:
                    if name in ('PostgreSQL bounded physical diagnostics', 'PostgreSQL normalized reason codes', 'PostgreSQL UID freshness timings'):
                        terminal.write_sep('-', name)
                        terminal.write_line(body)


def pytest_configure(config):
    config.pluginmanager.register(_ImmediateNativeFailures(config), 'life-native-failure-report')


@pytest.fixture
def reference_migrations_82(tmp_path):
    """Exact original destination of retained v58-v68 historical upgrades."""
    directory = tmp_path / 'reference_migrations_82'
    directory.mkdir()
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) <= 82:
            (directory / path.name).write_bytes(path.read_bytes())
    return directory


@pytest.fixture
def reference_migrations_100(tmp_path):
    """Exact target of retained pre-101 differential proofs, not latest schema."""
    directory = tmp_path / 'reference_migrations_100'
    directory.mkdir()
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) <= 100:
            (directory / path.name).write_bytes(path.read_bytes())
    return directory


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def migration_template():
    """Migrate once, then clone an isolated empty DB for current-schema cases.

    Runtime connections and financial plans remain cold. Explicit old-schema
    upgrade cases still execute migrations directly in their own fresh DB.
    """
    dsn = os.environ.get('LIFE_PG_TEST_DSN')
    if not dsn:
        pytest.skip('Requires run_postgres.py disposable database')
    asyncpg = pytest.importorskip('asyncpg')
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_pg2_test')
    root = dsn.rsplit('/', 1)[0]
    name = 'life_pg2_template_' + uuid4().hex
    admin = await asyncpg.connect(dsn)
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        c = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        try:
            await migrate(c, DEFAULT_DIRECTORY, action='up')
        finally:
            await c.close()
        # No runtime/admin connection may mutate or hold the template open.
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        await admin.close()
        yield name
    finally:
        if admin.is_closed():
            admin = await asyncpg.connect(dsn)
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.failed:
        samples = [repr(sample) for store in getattr(item, '_pg_diagnostic_runtimes', ()) for sample in store.diagnostics
                   if sample.error_kind is not None or sample.outcome not in ('success',)]
        samples = samples[-12:]
        if samples:
            report.sections.append(('PostgreSQL bounded physical diagnostics', '\n'.join(samples)))
            # report.sections alone is not reliably retained by JUnit's
            # default logging mode. Properties survive without raw exceptions.
            item.user_properties.append(('life_pg_diagnostics', json.dumps(samples)))
            report.user_properties = list(item.user_properties)
        codes = getattr(item, '_pg_reason_codes', ())
        if codes:
            report.sections.append(('PostgreSQL normalized reason codes', '\n'.join(codes)))
        timings = getattr(item, '_pg_uid_timings', ())
        if timings:
            encoded = json.dumps(list(timings))
            report.sections.append(('PostgreSQL UID freshness timings', encoded))
            item.user_properties.append(('life_pg_uid_timings', encoded))
            report.user_properties = list(item.user_properties)
    if report.when == 'teardown':
        # Pytest retains collected items until session end. Do not retain every
        # closed runtime/pool/source graph through these diagnostic references.
        for name in ('_pg_diagnostic_runtimes', '_pg_reason_codes', '_pg_uid_timings'):
            if hasattr(item, name):
                delattr(item, name)


@pytest_asyncio.fixture
async def database(request, monkeypatch, tmp_path, migration_template):
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
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
    original_fresh = PgQuoteAccountSource._uid_fresh
    request.node._pg_uid_timings = deque(maxlen=12)

    def timed_uid(source):
        fresh = original_fresh(source)
        if not fresh and source._uid_at is not None:
            try:
                # Do not call an injected clock twice: some clock fault tests
                # deliberately advance on every read. This is an approximate
                # real-wall sample, explicitly not the source-clock verdict.
                age = (datetime.now(timezone.utc) - source._uid_at).total_seconds() * 1000
                elapsed = (time.monotonic() - source._uid_monotonic) * 1000
                request.node._pg_uid_timings.append(dict(wall_age_ms=round(age, 3), elapsed_ms=round(elapsed, 3),
                                                         wall_skew_ms=round(abs(age - elapsed), 3),
                                                         max_age_ms=source.max_uid_age_ms,
                                                         max_skew_ms=source.max_clock_skew_ms))
            except Exception:
                # Diagnostic failure must not change the original verdict.
                pass
        return fresh
    monkeypatch.setattr(PgQuoteAccountSource, '_uid_fresh', timed_uid)
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_pg2_test')
    admin = await asyncpg.connect(dsn)
    name = 'life_pg2_' + uuid4().hex
    explicit_upgrade = hasattr(request, 'param')
    template = '' if explicit_upgrade else f' TEMPLATE "{migration_template}"'
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator{template}')
    function_profile = os.environ.get('LIFE_PG_TEST_FUNCTION_PROFILE') == '1'
    if function_profile:
        await admin.execute(f'ALTER DATABASE "{name}" SET track_functions=\'all\'')
    root = dsn.rsplit('/', 1)[0]
    migration_dsn = root.replace('postgres@', 'life_pg2_migrator@') + '/' + name
    c = await asyncpg.connect(migration_dsn)
    try:
        directory = DEFAULT_DIRECTORY
        if explicit_upgrade:
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
