"""Opt-in real PostgreSQL tests; use run_migrations.py, never a user's database."""
import asyncio
import os
import sys
import uuid

import asyncpg
import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import (
    DEFAULT_DIRECTORY,
    LOCK_KEY,
    MigrationError,
    migrate,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    dsn = os.environ['LIFE_PG_MIGRATION_TEST_DSN']
    assert dsn.startswith('postgresql://postgres@127.0.0.1:') and dsn.endswith('/life_migrations_test')
    admin = await asyncpg.connect(dsn, command_timeout=5)
    assert await admin.fetchval('SELECT current_database()') == 'life_migrations_test'
    name = 'migration_case_' + uuid.uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_migrator_test')
    migration_dsn = dsn.rsplit('/', 1)[0].replace('postgres@', 'life_migrator_test@') + '/' + name
    connection = await asyncpg.connect(migration_dsn, command_timeout=5)
    try:
        yield connection, migration_dsn, tmp_path
    finally:
        await connection.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


def write(directory, version, sql, suffix='change'):
    path = directory / f'{version:06d}_{suffix}.sql'
    path.write_text(sql)
    return path


async def versions(connection):
    return await connection.fetch('SELECT version FROM life_migrations.schema_migrations ORDER BY version')


@pytest.mark.asyncio
async def test_status_has_no_ddl_and_repeat_up_does_not_reexecute(db):
    c, _, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int); INSERT INTO sample.counter VALUES (1);')
    write(directory, 2, 'UPDATE sample.counter SET n=n+1;')
    status = await migrate(c, directory)
    assert len(status['pending']) == 2
    assert not await c.fetchval("SELECT to_regnamespace('life_migrations') IS NOT NULL")
    assert len((await migrate(c, directory, action='up'))['applied']) == 2
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    assert await c.fetchval('SELECT n FROM sample.counter') == 2
    assert [r['version'] for r in await versions(c)] == [1, 2]


@pytest.mark.asyncio
async def test_packaged_lineage_is_stable_and_not_financial_bootstrap(db):
    c, _, _ = db
    await migrate(c, action='up')
    before = await c.fetchrow('SELECT * FROM life_storage.storage_metadata')
    await migrate(c, action='up')
    assert before == await c.fetchrow('SELECT * FROM life_storage.storage_metadata')
    assert await c.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='life_storage'") == 1
    assert DEFAULT_DIRECTORY.is_dir()


@pytest.mark.asyncio
async def test_receipt_proof_upgrade_preserves_provisioned_snapshot_acl_and_repeat_startup(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 15:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000015_spot_prepare_receipt_proof.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state_v14(uuid)','EXECUTE')", principal)
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_prepare_receipts_verified(uuid)','EXECUTE')", principal)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_ddl_data_and_history_then_resumes(db):
    c, _, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int); INSERT INTO sample.counter VALUES (1);')
    bad = write(directory, 2, 'UPDATE sample.counter SET n=99; CREATE TABLE sample.bad(n int); SELECT 1/0;')
    with pytest.raises(asyncpg.DivisionByZeroError):
        await migrate(c, directory, action='up')
    assert await c.fetchval('SELECT n FROM sample.counter') == 1
    assert not await c.fetchval("SELECT to_regclass('sample.bad') IS NOT NULL")
    assert len(await versions(c)) == 1
    # Only an unapplied migration may be corrected.
    bad.write_text('UPDATE sample.counter SET n=2;')
    assert len((await migrate(c, directory, action='up'))['applied']) == 1
    assert await c.fetchval('SELECT n FROM sample.counter') == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bytes', 'delete', 'rename', 'insert_older'])
async def test_history_drift_refuses_every_pending_change(db, change):
    c, _, directory = db
    first = write(directory, 2, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int);')
    await migrate(c, directory, action='up')
    write(directory, 3, 'INSERT INTO sample.counter VALUES (100);')
    if change == 'bytes':
        first.write_text(first.read_text() + '\n')
    elif change == 'delete':
        first.unlink()
    elif change == 'rename':
        first.rename(directory / '000002_renamed.sql')
    else:
        write(directory, 1, 'SELECT 1;')
    with pytest.raises(MigrationError):
        await migrate(c, directory, action='up')
    assert await c.fetchval('SELECT count(*) FROM sample.counter') == 0


@pytest.mark.asyncio
async def test_database_ahead_of_bundle_and_unregistered_schema_fail_closed(db):
    c, _, directory = db
    write(directory, 1, 'SELECT 1;')
    second = write(directory, 2, 'SELECT 2;')
    await migrate(c, directory, action='up')
    second.unlink()
    with pytest.raises(MigrationError):
        await migrate(c, directory)
    await c.execute('DROP TABLE life_migrations.schema_migrations')
    with pytest.raises(MigrationError):
        await migrate(c, directory, action='up')


@pytest.mark.asyncio
async def test_unregistered_storage_is_not_adopted_or_overwritten(db):
    c, _, _ = db
    await c.execute('CREATE SCHEMA life_storage; CREATE TABLE life_storage.existing(n int)')
    with pytest.raises(asyncpg.DuplicateSchemaError):
        await migrate(c, action='up')
    assert len(await versions(c)) == 0
    assert await c.fetchval("SELECT to_regclass('life_storage.existing') IS NOT NULL")


@pytest.mark.asyncio
async def test_two_runners_apply_each_script_once(db):
    c, dsn, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int); SELECT pg_sleep(.1);')
    write(directory, 2, 'INSERT INTO sample.counter VALUES (1);')
    other = await asyncpg.connect(dsn, command_timeout=5)
    try:
        a, b = await asyncio.gather(migrate(c, directory, action='up'), migrate(other, directory, action='up'))
        assert sorted([len(a['applied']), len(b['applied'])]) == [0, 2]
        assert await c.fetchval('SELECT count(*) FROM sample.counter') == 1
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_advisory_lock_wait_is_bounded_and_released_after_failure(db):
    c, dsn, directory = db
    write(directory, 1, 'SELECT 1;')
    blocker = await asyncpg.connect(dsn)
    try:
        await blocker.execute('SELECT pg_advisory_lock($1)', LOCK_KEY)
        with pytest.raises(MigrationError, match='lock timeout'):
            await migrate(c, directory, action='up', lock_timeout=.05)
        assert not await c.fetchval("SELECT to_regnamespace('life_migrations') IS NOT NULL")
        await blocker.execute('SELECT pg_advisory_unlock($1)', LOCK_KEY)
        assert len((await migrate(c, directory, action='up'))['applied']) == 1
    finally:
        await blocker.close()


@pytest.mark.asyncio
async def test_cancelled_script_rolls_back_and_releases_session_lock(db):
    c, dsn, directory = db
    file = write(directory, 1, 'CREATE SCHEMA sample; SELECT pg_sleep(10);')
    task = asyncio.create_task(migrate(c, directory, action='up'))
    observer = await asyncpg.connect(dsn)
    try:
        for _ in range(100):
            waiting = await observer.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                WHERE pid=$1 AND wait_event='PgSleep')''', c.get_server_pid())
            if waiting:
                break
            await asyncio.sleep(.01)
        assert waiting
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await c.fetchval("SELECT to_regnamespace('sample') IS NOT NULL")
        assert len(await versions(c)) == 0
        file.write_text('CREATE SCHEMA sample;')
        assert len((await migrate(observer, directory, action='up', lock_timeout=.2))['applied']) == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await observer.close()


@pytest.mark.asyncio
async def test_statement_timeout_has_no_partial_schema(db):
    c, _, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; SELECT pg_sleep(1);')
    with pytest.raises(asyncpg.QueryCanceledError):
        await migrate(c, directory, action='up', statement_timeout=50, ddl_lock_timeout=25)
    assert not await c.fetchval("SELECT to_regnamespace('sample') IS NOT NULL")
    assert len(await versions(c)) == 0


class LostCommitReply:
    """Test proxy loses the reply after a real successful script/history COMMIT."""

    def __init__(self, connection):
        self.connection = connection
        self.transactions = 0

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def transaction(self):
        self.transactions += 1
        real = self.connection.transaction()
        lose_reply = self.transactions == 2  # Bootstrap, then first script.
        connection = self.connection

        class Transaction:
            async def __aenter__(self):
                return await real.__aenter__()

            async def __aexit__(self, *args):
                result = await real.__aexit__(*args)
                if lose_reply and args[0] is None:
                    await connection.close()
                    raise ConnectionError('simulated COMMIT reply loss')
                return result

        return Transaction()


@pytest.mark.asyncio
async def test_unknown_commit_fresh_run_reads_history_without_reexecuting(db):
    c, dsn, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int); INSERT INTO sample.counter VALUES (1);')
    with pytest.raises(ConnectionError):
        await migrate(LostCommitReply(c), directory, action='up')
    fresh = await asyncpg.connect(dsn)
    try:
        assert (await migrate(fresh, directory))['pending'] == []
        assert (await migrate(fresh, directory, action='up'))['applied'] == []
        assert await fresh.fetchval('SELECT count(*) FROM sample.counter') == 1
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_runtime_denied_and_reporting_can_only_read_history(db):
    c, dsn, directory = db
    write(directory, 1, 'CREATE SCHEMA sample;')
    await migrate(c, directory, action='up')
    await c.execute('GRANT USAGE ON SCHEMA life_migrations TO life_reporter_test')
    await c.execute('GRANT SELECT ON life_migrations.schema_migrations TO life_reporter_test')
    write(directory, 2, 'CREATE TABLE sample.new_table(n int);')
    for role in ['life_runtime_test', 'life_reporter_test']:
        other = await asyncpg.connect(dsn.replace('life_migrator_test@', role + '@'))
        try:
            if role == 'life_reporter_test':
                assert len((await migrate(other, directory))['pending']) == 1
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await migrate(other, directory, action='up')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await other.execute("UPDATE life_migrations.schema_migrations SET checksum=repeat('0',64)")
        finally:
            await other.close()
    assert not await c.fetchval("SELECT to_regclass('sample.new_table') IS NOT NULL")


@pytest.mark.asyncio
async def test_cli_exit_codes_and_secret_redaction(db):
    _, dsn, directory = db
    secret = 'DO_NOT_LOG_THIS_SQL_LITERAL'
    file = write(directory, 1, "SELECT '" + secret + "'::integer;")

    async def cli(action):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations',
            action, '--directory', str(directory), '--local-insecure',
            env={**os.environ, 'LIFE_MIGRATION_DSN': dsn},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        assert dsn.encode() not in stdout + stderr and secret.encode() not in stdout + stderr
        return process.returncode, stdout, stderr

    assert (await cli('status'))[0] == 2
    error = await cli('up')
    assert error[0] == 1 and b'InvalidTextRepresentationError' in error[2]
    file.write_text('SELECT 1;')
    assert (await cli('up'))[0] == 0
    assert (await cli('status'))[0] == 0


@pytest.mark.asyncio
async def test_nested_procedure_cannot_commit_outside_runner_transaction(db):
    c, _, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; DO $$ BEGIN COMMIT; END; $$;')
    with pytest.raises(asyncpg.InvalidTransactionTerminationError):
        await migrate(c, directory, action='up')
    assert not await c.fetchval("SELECT to_regnamespace('sample') IS NOT NULL")
    assert len(await versions(c)) == 0


@pytest.mark.asyncio
async def test_ddl_lock_timeout_leaves_applied_prefix_unchanged(db):
    c, dsn, directory = db
    write(directory, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int);')
    await migrate(c, directory, action='up')
    blocker = await asyncpg.connect(dsn)
    try:
        await blocker.execute('BEGIN; LOCK TABLE sample.counter IN ACCESS EXCLUSIVE MODE;')
        write(directory, 2, 'ALTER TABLE sample.counter ADD COLUMN next_value int;')
        with pytest.raises(asyncpg.LockNotAvailableError):
            await migrate(c, directory, action='up', statement_timeout=500, ddl_lock_timeout=25)
        assert len(await versions(c)) == 1
    finally:
        await blocker.execute('ROLLBACK')
        await blocker.close()


@pytest.mark.asyncio
async def test_caller_transaction_cannot_turn_commit_into_a_savepoint(db):
    c, _, directory = db
    write(directory, 1, 'SELECT 1;')
    async with c.transaction():
        with pytest.raises(MigrationError, match='outside a transaction'):
            await migrate(c, directory, action='up')
    assert not await c.fetchval("SELECT to_regnamespace('life_migrations') IS NOT NULL")


@pytest.mark.asyncio
async def test_auto_migration_from_env_file_is_idempotent_and_disabled_is_noop(db):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import load_config
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import auto_migrate

    c, dsn, directory = db
    scripts = directory / 'sql'
    scripts.mkdir()
    write(scripts, 1, 'CREATE SCHEMA sample; CREATE TABLE sample.counter(n int); INSERT INTO sample.counter VALUES (1);')
    file = directory / '.env'
    text = f'LIFE_AUTO_MIGRATE=false\nLIFE_MIGRATION_DSN={dsn}\n'
    text += 'LIFE_MIGRATION_LOCAL_INSECURE=true\nLIFE_MIGRATION_DIRECTORY=sql\n'
    file.write_text(text)
    assert await auto_migrate(load_config(env_file=file, environ={})) is None
    assert not await c.fetchval("SELECT to_regnamespace('life_migrations') IS NOT NULL")
    file.write_text(text.replace('LIFE_AUTO_MIGRATE=false', 'LIFE_AUTO_MIGRATE=true'))
    config = load_config(env_file=file, environ={})
    assert len((await auto_migrate(config))['applied']) == 1
    assert (await auto_migrate(config))['applied'] == []
    assert await c.fetchval('SELECT count(*) FROM sample.counter') == 1


@pytest.mark.asyncio
async def test_cli_reads_dotenv_while_manual_up_ignores_auto_flag(db):
    _, dsn, directory = db
    scripts = directory / 'sql'
    scripts.mkdir()
    write(scripts, 1, 'CREATE SCHEMA sample;')
    file = directory / '.env'
    file.write_text(f'LIFE_AUTO_MIGRATE=false\nLIFE_MIGRATION_DSN={dsn}\n'
                    'LIFE_MIGRATION_LOCAL_INSECURE=true\nLIFE_MIGRATION_DIRECTORY=sql\n')
    env = {key: value for key, value in os.environ.items() if not key.startswith('LIFE_MIGRATION_')}
    for action, expected in [('status', 2), ('up', 0), ('up', 0), ('status', 0)]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations',
            action, '--env-file', str(file), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        assert process.returncode == expected, (stdout, stderr)
        assert dsn.encode() not in stdout + stderr


@pytest.mark.asyncio
async def test_executor_outbox_upgrade_preserves_snapshot_acl_and_is_repeatable(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 16:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000016_recorder_executor_outbox.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('spot_state_v15(uuid)', 'recorder_executor_integrity(uuid)',
                          'recorder_executor_verified(uuid)',
                          'recorder_stage_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                          'recorder_apply_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)'):
            # Upgrade never provisions new private/command rights implicitly.
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    assert await c.fetchval('SELECT count(*) FROM life_financial.recorder_outbox') == 0
    assert await c.fetchval('SELECT count(*) FROM life_financial.projection_receipts') == 0


@pytest.mark.asyncio
async def test_configuration_upgrade_keeps_scoped_reader_acl_and_repeats_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 17:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid),life_financial.recorder_executor_verified(uuid)
            TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000017_recorder_configuration.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        for signature in ('spot_state(uuid)', 'recorder_executor_verified(uuid)'):
            assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for signature in ('recorder_executor_integrity(uuid)', 'recorder_executor_only_integrity(uuid)',
                          'recorder_config_integrity(uuid)',
                          'recorder_config_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'recorder_config_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert await c.fetchval('SELECT count(*) FROM life_financial.recorder_outbox') == 0


@pytest.mark.asyncio
async def test_event_reporting_upgrade_preserves_acl_and_repeats_without_money_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 18:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid),life_financial.recorder_executor_verified(uuid)
            TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000018_recorder_events.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        for signature in ('spot_state(uuid)', 'recorder_executor_verified(uuid)'):
            assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for signature in ('recorder_event_integrity(uuid)', 'recorder_identity_text(jsonb)',
                          'recorder_native_put(text,jsonb,jsonb,jsonb)',
                          'recorder_event_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity,jsonb)',
                          'recorder_event_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for table in ('recorder_projection_effects', 'recorder_projection_heads'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'recorder_outbox', 'recorder_projection_effects', 'recorder_projection_heads'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_position_reporting_upgrade_preserves_acl_and_repeats_without_money_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 19:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid),life_financial.recorder_executor_verified(uuid)
            TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000019_recorder_positions.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        for signature in ('spot_state(uuid)', 'recorder_executor_verified(uuid)'):
            assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for signature in ('recorder_position_integrity(uuid)', 'recorder_native_get_v18(text,jsonb)',
                          'recorder_native_put(text,jsonb,jsonb,jsonb)',
                          'recorder_position_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'recorder_position_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for table in ('recorder_projection_effects', 'recorder_projection_heads'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'recorder_outbox', 'recorder_projection_effects', 'recorder_projection_heads'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_operator_approval_upgrade_preserves_acl_and_repeats_without_money_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 20:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid),life_financial.recorder_executor_verified(uuid)
            TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000020_spot_operator_approvals.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        for signature in ('spot_state(uuid)', 'recorder_executor_verified(uuid)'):
            assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for signature in ('spot_operator_approval_integrity(uuid)', 'spot_capital_verified_v19(uuid)',
                          'spot_operator_approval_commit_guard()'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, 'life_financial.' + signature)
        for table in ('bill_approvals', 'spot_operator_approvals'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'bill_approvals', 'spot_operator_approvals'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_ack_ingress_upgrade_preserves_scoped_acl_and_repeat_startup_without_money_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 21:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid)
            TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000021_spot_ack_ingress.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('spot_ack_ingress_verified(uuid)', 'spot_ack_ingress_commit_guard()',
                          'spot_capture_ack_ingress(uuid,jsonb)', 'spot_mark_ack_ingress(uuid,life_financial.identity)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + signature)
        for table in ('spot_ack_ingress', 'spot_ack_ingress_receipts'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'spot_ack_ingress', 'spot_ack_ingress_receipts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_observation_context_upgrade_keeps_existing_acl_and_repeats_without_financial_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 22:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000022_spot_observation_context.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('spot_observation_context(uuid,integer,integer)', 'spot_prepare_receipts_verified(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + signature)
    function = await c.fetchrow("SELECT provolatile::text AS volatility,prosecdef FROM pg_proc WHERE oid='life_financial.spot_observation_context(uuid,integer,integer)'::regprocedure")
    assert function['volatility'] == 's' and function['prosecdef']
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'spot_preparations', 'spot_fill_facts', 'spot_cashflow_facts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_financial_intake_upgrade_preserves_received_ack_baseline_without_financial_bootstrap(db):
    import json
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 23:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    account, lineage, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_lineage = await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')
    at = await c.fetchval('SELECT clock_timestamp()')
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', lineage, 1, 'a' * 64, 'b' * 64)
    owner = OwnerToken(uuid.uuid4(), uuid.uuid4(), 1, session, at - timedelta(seconds=1), at + timedelta(seconds=30))
    async with c.transaction():
        await c.execute('''INSERT INTO life_financial.accounts(account_id,database_lineage,environment,exchange,account_uid,risk_pool,
            lineage,schema_version,policy_hash,config_hash) VALUES($1,$2,'OFFLINE_SIMULATION','okx','12345','usdt',$3,1,$4,$5)''',
                        account, db_lineage, lineage, binding.policy_hash, binding.config_hash)
        await c.execute("INSERT INTO life_financial.policies VALUES($1,$2,'{}')", account, binding.policy_hash)
        await c.execute("INSERT INTO life_financial.configurations VALUES($1,1,$2,$3,'{}')", account, binding.config_hash, binding.policy_hash)
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(session_user,$1,true)', account)
    body = dict(binding=binding.json(), owner=owner.json(), request_sha256='f' * 64, command=dict(operation_id='baseline-ack',
                prepare_operation_id='unqualified-original', intent_id='unknown', client_order_id='wire1', market='LIFE-USDT', side='BUY',
                session_id=str(session), epoch=1, exchange_order_id='123'))
    original = await c.fetchval('SELECT life_financial.spot_capture_ack_ingress($1,$2::jsonb)', account, json.dumps(body))
    before = await c.fetchrow('SELECT * FROM life_financial.accounts')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    path = DEFAULT_DIRECTORY / '000023_spot_financial_ingress.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    row = await c.fetchrow('SELECT * FROM life_financial.spot_ingress_progress')
    assert row['ack_captured'] == row['version'] == 1 and row['ack_delivered'] == row['financial_captured'] == row['financial_delivered'] == 0
    assert await c.fetchval('SELECT life_financial.spot_ack_ingress_verified($1)', account)
    assert json.loads(await c.fetchval('SELECT life_financial.spot_read_ack_ingress($1,$2)', account, json.loads(original)['ingress_id']))['body'] == json.loads(original)['body']
    assert await c.fetchrow('SELECT * FROM life_financial.accounts') == before
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('spot_ingress_increment(uuid,text,text)', 'spot_ingress_counts_verified(uuid)',
                          'spot_capture_financial_ingress(uuid,jsonb)', 'spot_mark_financial_ingress(uuid,life_financial.identity,life_financial.identity)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('balances', 'operations', 'sessions', 'spot_financial_ingress', 'spot_financial_ingress_receipts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_inventory_settlement_upgrade_preserves_acl_and_unarmed_money_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 24:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    account, lineage = uuid.uuid4(), uuid.uuid4()
    db_lineage = await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')
    async with c.transaction():
        await c.execute('''INSERT INTO life_financial.accounts(account_id,database_lineage,environment,exchange,account_uid,risk_pool,
            lineage,schema_version,policy_hash,config_hash) VALUES($1,$2,'OFFLINE_SIMULATION','okx','12345','usdt',$3,1,$4,$5)''',
                        account, db_lineage, lineage, 'a' * 64, 'b' * 64)
        await c.execute("INSERT INTO life_financial.policies VALUES($1,$2,'{}')", account, 'a' * 64)
        await c.execute("INSERT INTO life_financial.configurations VALUES($1,1,$2,$3,'{}')", account, 'b' * 64, 'a' * 64)
        await c.execute("INSERT INTO life_financial.balances(account_id,asset,total) VALUES($1,'LIFE',5),($1,'USDT',10)", account)
    before_account = await c.fetchrow('SELECT * FROM life_financial.accounts')
    before_money = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    oid = await c.fetchval("SELECT 'life_financial.spot_state(uuid)'::regprocedure::oid")
    path = DEFAULT_DIRECTORY / '000024_spot_inventory_settlement.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval("SELECT 'life_financial.spot_state(uuid)'::regprocedure::oid") == oid
    assert await c.fetchrow('SELECT * FROM life_financial.accounts') == before_account
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == before_money
    assert before_account['mode'] == 'UNARMED'
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('canonical_json_v1(jsonb)', 'spot_inventory_evidence(uuid,jsonb)',
                          'spot_inventory_settlements_verified(uuid)', 'spot_inventory_commit_guard(uuid,jsonb)',
                          'spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + signature)
        for table in ('spot_settlement_policies', 'spot_inventory_settlements', 'spot_inventory_members'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('operations', 'sessions', 'spot_settlement_policies', 'spot_inventory_settlements', 'spot_inventory_members'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_markout_upgrade_preserves_acl_and_unarmed_money_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 25:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    account, lineage = uuid.uuid4(), uuid.uuid4()
    db_lineage = await c.fetchval('SELECT lineage FROM life_storage.storage_metadata')
    async with c.transaction():
        await c.execute('''INSERT INTO life_financial.accounts(account_id,database_lineage,environment,exchange,account_uid,risk_pool,
            lineage,schema_version,policy_hash,config_hash) VALUES($1,$2,'OFFLINE_SIMULATION','okx','12345','usdt',$3,1,$4,$5)''',
                        account, db_lineage, lineage, 'a' * 64, 'b' * 64)
        await c.execute("INSERT INTO life_financial.policies VALUES($1,$2,'{}')", account, 'a' * 64)
        await c.execute("INSERT INTO life_financial.configurations VALUES($1,1,$2,$3,'{}')", account, 'b' * 64, 'a' * 64)
        await c.execute("INSERT INTO life_financial.balances(account_id,asset,total) VALUES($1,'LIFE',5),($1,'USDT',10)", account)
    before_account = await c.fetchrow('SELECT * FROM life_financial.accounts')
    before_money = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    await c.execute('''GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test;
        GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test''')
    oid = await c.fetchval("SELECT 'life_financial.spot_state(uuid)'::regprocedure::oid")
    path = DEFAULT_DIRECTORY / '000025_spot_markout_observations.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval("SELECT 'life_financial.spot_state(uuid)'::regprocedure::oid") == oid
    assert await c.fetchrow('SELECT * FROM life_financial.accounts') == before_account
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == before_money
    assert before_account['mode'] == 'UNARMED'
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,'life_financial.spot_state(uuid)','EXECUTE')", principal)
        for signature in ('spot_markout_policy_valid(bigint[],bigint,jsonb)', 'spot_markout_command_valid(jsonb)',
                          'spot_markouts_verified(uuid)', 'spot_markout_commit_guard(uuid,jsonb)',
                          'spot_observe_markout(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'spot_markout_frame(uuid,integer,integer)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + signature)
        for table in ('spot_markout_policies', 'spot_markout_facts'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('operations', 'sessions', 'spot_markout_policies', 'spot_markout_facts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_capture_reason_upgrade_preserves_private_guard_oid_acl_and_has_no_financial_effect(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 26:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.guard_quote_admission(uuid,jsonb,jsonb,jsonb,jsonb)'
    oid = await c.fetchval('SELECT to_regprocedure($1)::oid', signature)
    path = DEFAULT_DIRECTORY / '000026_spot_capture_time_reasons.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval('SELECT to_regprocedure($1)::oid', signature) == oid
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'sessions', 'spot_markout_facts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_provider_journal_upgrade_repeats_without_new_financial_authority(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 27:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.spot_state(uuid)'
    oid = await c.fetchval('SELECT to_regprocedure($1)::oid', signature)
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000027_spot_provider_journal.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval('SELECT to_regprocedure($1)::oid', signature) == oid
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for fn in ('spot_capture_provider_page(uuid,jsonb)', 'spot_read_provider_collection(uuid,life_financial.identity,integer,integer)',
                   'spot_provider_receipt(uuid,text,integer,boolean)', 'spot_provider_route(text,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + fn)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'sessions', 'spot_provider_heads', 'spot_provider_pages'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_shared_capital_upgrade_preserves_old_snapshot_core_acl_and_empty_account_scope(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 28:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)',
                  'life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000028_shared_capital.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for function in (signatures[1], 'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                         'life_financial.shared_capital_frame(uuid,integer,integer)', 'life_financial.shared_capital_verified(uuid)',
                         'life_financial.shared_operation_verified(uuid,text)', 'life_financial.shared_capital_read_operation(uuid,text,integer,integer)',
                         'life_financial.shared_evaluate(jsonb,jsonb,jsonb)', 'life_financial.shared_margin(numeric,jsonb,integer)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, function)
        for table in ('shared_capital_policies', 'shared_capital_claims', 'shared_capital_heads', 'shared_capital_observations'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'sessions', 'shared_capital_policies', 'shared_capital_claims', 'shared_capital_heads', 'shared_capital_observations'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_carry_upgrade_preserves_snapshot_acl_and_adds_no_funding_or_account_defaults(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 29:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.spot_state(uuid)'
    oid = await c.fetchval('SELECT to_regprocedure($1)::oid', signature)
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000029_carry_streams.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval('SELECT to_regprocedure($1)::oid', signature) == oid
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for fn in ('spot_state_before_carry(uuid)', 'carry_read(uuid,integer,integer,text)',
                   'carry_check(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                   'carry_verified(uuid)', 'carry_current_ready(uuid)', 'carry_check_verified(uuid,text)',
                   'carry_evaluate(jsonb,jsonb,jsonb,jsonb,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + fn)
        for table in ('carry_policies', 'carry_heads', 'carry_checks', 'carry_payments'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'sessions', 'carry_policies', 'carry_heads', 'carry_checks', 'carry_payments'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_swap_upgrade_preserves_snapshot_acl_and_retains_private_spot_core(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 30:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.spot_state(uuid)'
    oid = await c.fetchval('SELECT to_regprocedure($1)::oid', signature)
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000030_swap_intents.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval('SELECT to_regprocedure($1)::oid', signature) == oid
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for fn in ('spot_state_v7(uuid)', 'spot_state_before_swap(uuid)', 'swap_read(uuid,integer,integer,text)',
                   'swap_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                   'swap_verified(uuid)', 'swap_budget_ready(uuid)', 'swap_config_valid(jsonb,jsonb,jsonb,bigint)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, 'life_financial.' + fn)
        for table in ('swap_policies', 'swap_preparations', 'swap_intent_events'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'sessions', 'swap_policies', 'swap_preparations', 'swap_intent_events'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_swap_accounting_upgrade_preserves_existing_entrypoint_oids_acls_and_adds_no_money(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 32:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.spot_state_v7(uuid)',
                  'life_financial.spot_accounting_verified(uuid)', 'life_financial.spot_capital_replay(uuid)',
                  'life_financial.spot_capital_verified_v19(uuid)', 'life_financial.spot_capital_verified(uuid)',
                  'life_financial.carry_current_ready(uuid)',
                  'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000032_swap_fill_accounting.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        private_signatures = signatures[1:] + (
            'life_financial.swap_fill_read(uuid,integer,integer,text)',
            'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
            'life_financial.swap_accounting_verified(uuid)', 'life_financial.swap_snapshot_current(uuid,jsonb)',
            'life_financial.swap_position_replay(jsonb,jsonb,jsonb,text,bigint,integer)')
        for signature in private_signatures:
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('swap_fill_policies', 'swap_fill_facts', 'swap_fill_progress', 'swap_pending_fills'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'swap_fill_policies', 'swap_fill_facts', 'swap_fill_progress', 'swap_pending_fills'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_paid_funding_upgrade_preserves_public_and_private_proof_oids_and_is_not_money_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 33:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.spot_state_v7(uuid)',
                  'life_financial.spot_accounting_verified(uuid)', 'life_financial.spot_capital_replay(uuid)',
                  'life_financial.carry_current_ready(uuid)', 'life_financial.swap_snapshot_current(uuid,jsonb)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000033_paid_funding.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        private_signatures = signatures[1:] + (
            'life_financial.funding_read(uuid,integer,integer,text)',
            'life_financial.funding_verified(uuid)', 'life_financial.funding_carry_current(uuid,jsonb)',
            'life_financial.funding_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)')
        for signature in private_signatures:
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('funding_accounting_policies', 'funding_observations', 'funding_cash_payments'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    assert (await migrate(c, directory))['pending'] == []
    for table in ('accounts', 'balances', 'operations', 'funding_accounting_policies', 'funding_observations', 'funding_cash_payments'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [34, 35])
async def test_portfolio_upgrades_preserve_snapshot_acl_and_add_no_money_or_source_defaults(db, version):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < version:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()',
                  'life_financial.spot_capital_verified(uuid)', 'life_financial.spot_capital_replay(uuid)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = next(p for p in DEFAULT_DIRECTORY.glob('*.sql') if int(p.name.split('_', 1)[0]) == version)
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.portfolio_measure(numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric)','EXECUTE')", principal)
        if version == 35:
            for signature in ('life_financial.portfolio_verified(uuid)', 'life_financial.portfolio_read(uuid,integer,integer,text)',
                              'life_financial.portfolio_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'):
                assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
            for table in ('portfolio_policies', 'portfolio_observations'):
                assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
                assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_swap_terminal_upgrade_preserves_original_proof_oids_and_adds_no_release_or_money(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 36:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()',
                  'life_financial.swap_verified(uuid)', 'life_financial.swap_accounting_verified(uuid)',
                  'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000036_swap_terminal_observations.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.swap_terminal_verified(uuid)', 'life_financial.swap_terminal_read(uuid,integer,integer,text)',
                          'life_financial.swap_terminal_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('swap_terminal_policies', 'swap_terminal_facts'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'shared_capital_claims', 'swap_terminal_policies', 'swap_terminal_facts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_shared_release_upgrade_retains_proof_oids_and_never_bootstraps_or_removes_a_claim(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 37:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.shared_capital_verified(uuid)',
                  'life_financial.shared_operation_verified(uuid,text)', 'life_financial.shared_capital_frame(uuid,integer,integer)',
                  'life_financial.carry_check_verified(uuid,text)', 'life_financial.carry_current_ready(uuid)',
                  'life_financial.shared_capital_attempt_guard()', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000037_shared_claim_release.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.shared_release_verified(uuid)', 'life_financial.shared_release_read(uuid,integer,integer,text)',
                          'life_financial.shared_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('shared_release_policies', 'shared_claim_releases'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'shared_capital_claims', 'shared_release_policies', 'shared_claim_releases'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_joint_batch_upgrade_keeps_public_proof_oids_and_private_staging_has_no_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 38:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()',
                  'life_financial.spot_capital_verified(uuid)', 'life_financial.swap_accounting_verified(uuid)',
                  'life_financial.funding_verified(uuid)', 'life_financial.shared_release_verified(uuid)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000038_joint_financial_batches.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.joint_batch_verified(uuid)', 'life_financial.joint_stage_guard(uuid,text,jsonb)',
                          'life_financial.joint_stage_swap(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('joint_batch_policies', 'joint_financial_batches', 'joint_batch_staging'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'joint_batch_policies', 'joint_financial_batches', 'joint_batch_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_late_capital_upgrade_preserves_command_owner_oids_and_runtime_acl_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 39:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    signatures = (signature, 'life_financial.commit_owner_guard()', 'life_financial.shared_capital_verified(uuid)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute(f'GRANT USAGE ON SCHEMA life_financial TO life_runtime_test;GRANT EXECUTE ON FUNCTION {signature} TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000039_late_capital_observations.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    assert await c.fetchval("SELECT has_function_privilege('life_runtime_test',$1,'EXECUTE')", signature)
    assert not await c.fetchval("SELECT has_function_privilege('life_reporter_test',$1,'EXECUTE')", signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'shared_capital_claims', 'shared_capital_observations'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_joint_repair_upgrade_keeps_public_oids_and_private_approval_capabilities_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 40:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()',
                  'life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_pending_finance_guard()', 'life_financial.joint_financial_pending(uuid)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000040_joint_source_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)',
                          'life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'life_financial.joint_repair_approval_integrity(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('joint_repair_policies', 'joint_repair_approvals', 'joint_repair_facts', 'joint_repair_staging'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'joint_repair_policies', 'joint_repair_approvals', 'joint_repair_facts', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_hedge_upgrade_preserves_existing_oids_acl_and_private_transaction_context_without_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 41:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()',
                  'life_financial.swap_verified(uuid)', 'life_financial.joint_repair_verified(uuid)')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    assert all(value is not None for value in oids)
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000041_hedge_coordination.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.hedge_market_captured(uuid,text)', 'life_financial.hedge_verified(uuid)', 'life_financial.hedge_spot_ready(uuid)',
                          'life_financial.hedge_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('hedge_policies', 'hedge_targets', 'hedge_plan_facts', 'hedge_settlements', 'hedge_staging'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
        assert not await c.fetchval("SELECT has_table_privilege($1,'life_financial.hedge_staging','SELECT')", principal)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'hedge_policies', 'hedge_targets', 'hedge_plan_facts', 'hedge_settlements', 'hedge_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_swap_boundary_upgrade_preserves_public_oids_acl_and_has_no_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 42:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.spot_state(uuid)', 'life_financial.swap_verified(uuid)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000042_swap_request_boundary.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0])
        for signature in ('life_financial.swap_ack_verified(uuid)', 'life_financial.swap_capture_ack(uuid,jsonb)',
                          'life_financial.swap_mark_ack(uuid,text)', 'life_financial.swap_read_ack(uuid,integer,integer,text)',
                          'life_financial.swap_boundary_verified(uuid)', 'life_financial.swap_verified_before_boundary(uuid)',
                          'life_financial.spot_state_before_swap_boundary(uuid)', 'life_financial.swap_wire_matches(text,jsonb)',
                          'life_financial.swap_boundary_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'life_financial.swap_boundary_read(uuid,integer,integer,text)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('swap_boundary_policies', 'swap_boundaries', 'swap_ack_ingress', 'swap_ack_deliveries'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'swap_boundary_policies', 'swap_boundaries', 'swap_ack_ingress', 'swap_ack_deliveries'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_collateral_upgrade_preserves_original_financial_gate_oids_acl_and_adds_no_bootstrap(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 43:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_batch_verified(uuid)', 'life_financial.portfolio_basis(uuid,bigint)',
                  'life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()', 'life_financial.swap_snapshot_current(uuid,jsonb)',
                  'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.carry_current_ready(uuid)', 'life_financial.shared_capital_commit_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000043_joint_collateral_reconciliation.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[3])
        for signature in (signatures[1], signatures[2], 'life_financial.joint_collateral_qualified(jsonb,jsonb,jsonb)',
                          'life_financial.current_collateral_qualified(uuid,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'joint_financial_batches', 'joint_repair_approvals', 'joint_repair_facts'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_direct_funding_repair_upgrade_preserves_oids_acl_and_repeat_startup(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 44:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_read(uuid,integer,integer,text)',
                  'life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)',
                  'life_financial.joint_financial_pending(uuid)', 'life_financial.spot_state_before_portfolio(uuid)',
                  'life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
                  'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)',
                  'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000044_direct_funding_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[1])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in (*signatures[2:], 'life_financial.joint_repair_origin(uuid,text)',
                          'life_financial.funding_pending_unresolved(uuid,text,bigint,boolean)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'joint_repair_approvals', 'joint_repair_facts', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
