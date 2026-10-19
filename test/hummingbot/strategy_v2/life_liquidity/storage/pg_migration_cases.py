"""Opt-in real PostgreSQL tests; use run_migrations.py, never a user's database."""
import asyncio
import hashlib
import json
import os
import random
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


@pytest.mark.asyncio
async def test_funding_cohort_upgrade_retains_single_parent_oids_acl_and_private_members(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 45:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_read(uuid,integer,integer,text)', 'life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)',
                  'life_financial.joint_repair_verified(uuid)', 'life_financial.joint_repair_approval_integrity(uuid)',
                  'life_financial.funding_pending_unresolved(uuid,text,bigint,boolean)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000045_funding_repair_cohorts.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[1])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in (*signatures[2:], 'life_financial.joint_repair_sources(uuid,jsonb)',
                          'life_financial.joint_repair_cohort_verified(uuid)', 'life_financial.joint_repair_profile_single(uuid,jsonb,jsonb,bigint)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        assert not await c.fetchval("SELECT has_table_privilege($1,'life_financial.joint_repair_members','SELECT,INSERT,UPDATE,DELETE')", principal)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'joint_repair_members', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_swap_repair_upgrade_keeps_original_oids_and_private_capabilities(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 46:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_read(uuid,integer,integer,text)', 'life_financial.joint_repair_origin(uuid,text)',
                  'life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)', 'life_financial.joint_financial_pending(uuid)',
                  'life_financial.swap_snapshot_current(uuid,jsonb)', 'life_financial.swap_accounting_verified(uuid)',
                  'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)', 'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)',
                  'life_financial.funding_pending_unresolved(uuid,text,bigint,boolean)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000046_swap_fill_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, signatures[1])
        assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in (*signatures[2:], 'life_financial.joint_source_unresolved(uuid,text,bigint,boolean)',
                          'life_financial.joint_repair_profile_before_swap(uuid,jsonb,jsonb,bigint)',
                          'life_financial.joint_repair_profile_sources(uuid,jsonb,jsonb,bigint,jsonb)'):
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'swap_pending_fills', 'joint_repair_members', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_spot_repair_upgrade_preserves_oids_acl_and_keeps_pending_proofs_private(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 47:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_read(uuid,integer,integer,text)', 'life_financial.joint_repair_origin(uuid,text)',
                  'life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)', 'life_financial.joint_financial_pending(uuid)',
                  'life_financial.spot_accounting_verified(uuid)', 'life_financial.spot_state_v7(uuid)',
                  'life_financial.spot_observe(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                  'life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)',
                  'life_financial.spot_observation_context(uuid,integer,integer)',
                  'life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)',
                  'life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
                  'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000047_spot_fill_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[1])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in (*signatures[2:], 'life_financial.spot_pending_verified(uuid)',
                          'life_financial.spot_accounting_verified_before_repair(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'spot_pending_observations', 'joint_repair_members', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_cashflow_repair_upgrade_preserves_gate_oids_acl_and_private_application_table(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 48:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_read(uuid,integer,integer,text)', 'life_financial.joint_repair_origin(uuid,text)',
                  'life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)', 'life_financial.joint_financial_pending(uuid)',
                  'life_financial.spot_capital_verified_v19(uuid)', 'life_financial.spot_state_v7(uuid)',
                  'life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                  'life_financial.joint_stage_cashflow(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                  'life_financial.spot_observation_context(uuid,integer,integer)', 'life_financial.portfolio_money_at(uuid,bigint)',
                  'life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
                  'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000048_cashflow_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[1])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in (*signatures[2:], 'life_financial.cashflow_effective(uuid)',
                          'life_financial.cashflow_history_verified(uuid)', 'life_financial.cashflow_commit_guard()'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        assert not await c.fetchval("SELECT has_table_privilege($1,'life_financial.spot_cashflow_applications','SELECT,INSERT,UPDATE,DELETE')", principal)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'spot_cashflow_facts', 'spot_cashflow_applications', 'joint_repair_staging'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_terminal_repair_upgrade_preserves_existing_gate_oids_acl_and_repeat_startup(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 49:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.swap_terminal_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.swap_terminal_read(uuid,integer,integer,text)', 'life_financial.swap_terminal_verified(uuid)',
                  'life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)', 'life_financial.spot_state_before_shared_release(uuid)',
                  'life_financial.joint_financial_pending(uuid)', 'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)',
                  'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.portfolio_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.spot_state(uuid)', 'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.swap_terminal_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.swap_terminal_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000049_terminal_repair.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', value) for value in signatures] == oids
    for principal in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[1])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signatures[0]) is (principal == 'life_runtime_test')
        for signature in ('life_financial.terminal_repair_reason(uuid,jsonb,bigint,bigint,jsonb)',
                          'life_financial.terminal_repair_verified(uuid)', 'life_financial.terminal_only_recovery(uuid,jsonb)',
                          'life_financial.terminal_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                          'life_financial.terminal_repair_read(uuid,integer,integer,text)',
                          'life_financial.terminal_pending_unresolved(uuid,text,bigint)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", principal, signature)
        for table in ('terminal_repair_policies', 'terminal_repair_approvals', 'terminal_repair_facts', 'terminal_repair_members'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')", principal, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'terminal_repair_approvals', 'terminal_repair_facts', 'terminal_repair_members'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_legacy_applying_upgrade_preserves_existing_gate_oids_and_keeps_source_approval_private(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 50:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.joint_batch_verified(uuid)', 'life_financial.spot_state(uuid)',
                  'life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_batch_read(uuid,integer,integer,text)',
                  'life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.commit_owner_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_batch_read(uuid,integer,integer,text) TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000050_legacy_applying_import.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for role in ('life_runtime_test', 'life_reporter_test'):
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signatures[3])
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signatures[2]) is (role == 'life_runtime_test')
        for signature in ('life_financial.legacy_applying_verified(uuid)', 'life_financial.legacy_applying_fresh(uuid,text)',
                          'life_financial.legacy_applying_commit_guard()',
                          'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        for table in ('legacy_applying_policies', 'legacy_applying_imports'):
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')", role, 'life_financial.' + table)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'legacy_applying_policies', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_legacy_joint_basis_upgrade_keeps_old_oids_acl_and_adds_only_private_proofs(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 51:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.legacy_applying_verified(uuid)', 'life_financial.joint_batch_verified(uuid)',
                  'life_financial.spot_state(uuid)', 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.legacy_applying_fresh(uuid,text)', 'life_financial.legacy_applying_publication_guard()')
    oids = [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures]
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO life_runtime_test')
    path = DEFAULT_DIRECTORY / '000051_legacy_joint_basis.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchval("SELECT indisunique FROM pg_index WHERE indexrelid='life_financial.legacy_applying_one_source'::regclass")
    assert [await c.fetchval('SELECT to_regprocedure($1)::oid', signature) for signature in signatures] == oids
    for role in ('life_runtime_test', 'life_reporter_test'):
        for signature in (*signatures[:2], *signatures[5:], 'life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)', 'life_financial.legacy_joint_verified(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        assert await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signatures[3]) is (role == 'life_runtime_test')
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'legacy_applying_policies', 'legacy_applying_imports', 'swap_preparations', 'swap_fill_policies'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_legacy_cashflow_upgrade_preserves_original_gates_and_private_approval_proof(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 52:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signatures = ('life_financial.legacy_applying_verified(uuid)',
                  'life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                  'life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                  'life_financial.joint_stage_cashflow(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)')
    before = await c.fetch('SELECT oid,proacl FROM pg_proc WHERE oid=ANY($1::oid[]) ORDER BY oid',
                           [await c.fetchval('SELECT to_regprocedure($1)::oid', s) for s in signatures])
    path = DEFAULT_DIRECTORY / '000052_legacy_cashflow_basis.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetch('SELECT oid,proacl FROM pg_proc WHERE oid=ANY($1::oid[]) ORDER BY oid', [r['oid'] for r in before]) == before
    for role in ('life_runtime_test', 'life_reporter_test'):
        for signature in ('life_financial.legacy_cashflow_basis_valid(uuid,jsonb)', 'life_financial.legacy_cashflow_verified(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'bill_approvals', 'cashflows', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_canonical_upgrade_preserves_historical_bytes_hashes_strictness_and_acl(db):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint

    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 53:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.canonical_json_v1(jsonb)'
    before = await c.fetchrow('''SELECT oid,proacl,provolatile,proisstrict,prosecdef,proparallel,proleakproof,proconfig
        FROM pg_proc WHERE oid=to_regprocedure($1)''', signature)
    # Freeze the actual pre-upgrade implementation as an independent oracle,
    # including its numeric rendering and C-collation object key order.
    await c.execute('CREATE SCHEMA canonical_reference')
    original = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
    await c.execute(original.replace('life_financial.canonical_json_v1', 'canonical_reference.historical_v1'))

    rng = random.Random(53)
    strings = ['', 'a', 'é', 'e\u0301', '中', '😀', '\n\r\t\b\f', '\\"', '\u2028']

    def sample(depth):
        leaf = rng.choice([None, True, False, rng.randrange(-10**12, 10**12), rng.choice(strings)])
        if depth == 0 or rng.randrange(3) == 0:
            return leaf
        if rng.randrange(2):
            return [sample(depth - 1) for _ in range(rng.randrange(5))]
        return {key: sample(depth - 1) for key in rng.sample(strings, rng.randrange(5))}

    values = [None, True, False, 0, -1, *strings, [], {}, [[], {}, None],
              {'aa': 1, 'b': 2, 'A': 3, 'é': 4, '中': 5, '😀': 6}]
    values += [sample(4) for _ in range(256)]
    raw = [json.dumps(value, ensure_ascii=False, separators=(',', ':')) for value in values]
    raw += ['{"scaled":1.000,"zero":-0.000,"large":1e30,"small":-1e-30}',
            '{"n":' + '9' * 1200 + '}', '[' * 64 + '"deep"' + ']' * 64,
            '{"duplicate":1,"duplicate":2}', ' { "x" : [ true , null , 0 ] } ']
    old = await c.fetch('''SELECT canonical_reference.historical_v1(value::jsonb) AS body,
        encode(sha256(convert_to(canonical_reference.historical_v1(value::jsonb),'UTF8')),'hex') AS digest
        FROM unnest($1::text[]) WITH ORDINALITY AS e(value,ordinal) ORDER BY ordinal''', raw)
    path = DEFAULT_DIRECTORY / '000053_canonical_scalar_fast_path.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    assert await c.fetchrow('''SELECT oid,proacl,provolatile,proisstrict,prosecdef,proparallel,proleakproof,proconfig
        FROM pg_proc WHERE oid=to_regprocedure($1)''', signature) == before
    current = await c.fetch('''SELECT life_financial.canonical_json_v1(value::jsonb) AS body,
        encode(sha256(convert_to(life_financial.canonical_json_v1(value::jsonb),'UTF8')),'hex') AS digest
        FROM unnest($1::text[]) WITH ORDINALITY AS e(value,ordinal) ORDER BY ordinal''', raw)
    assert current == old
    for value, row in zip(values, current):
        expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        assert row['body'] == expected
        assert row['digest'] == hashlib.sha256(expected.encode()).hexdigest()
        # Existing native v1 uses UTF-8 JSON; the Python fingerprint codec
        # escapes non-ASCII. This optimization must preserve v1 history, not
        # silently change that separate codec/domain contract.
        if expected.isascii():
            assert row['digest'] == fingerprint(value)
    assert await c.fetchval('SELECT life_financial.canonical_json_v1(NULL::jsonb)') is None
    assert await c.fetchval("SELECT life_financial.canonical_json_v1('null'::jsonb)") == 'null'
    for role in ('life_runtime_test', 'life_reporter_test', 'public'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'bill_approvals', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_carry_reuse_upgrade_preserves_original_guards_and_every_existing_acl(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 54:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile,p.prosecdef,p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    # An owner's function defaults must not turn the private boolean helper
    # into a runtime shortcut around the complete proof.
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    path = DEFAULT_DIRECTORY / '000054_snapshot_carry_proof_reuse.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.carry_ready_after_verification(uuid,boolean)'
    assert set(after) - set(before) == {helper}
    for signature, row in before.items():
        expected = dict(row)
        if signature == 'life_financial.spot_state_before_swap(uuid)':
            expected['prosrc'] = row['prosrc'].replace('ready:=life_financial.carry_current_ready(p_account);',
                                                       'ready:=life_financial.carry_ready_after_verification(p_account,verified);')
        assert after[signature] == expected
    expected_body = before['life_financial.carry_current_ready(uuid)']['prosrc'].replace(
        'IF NOT life_financial.carry_verified(p_account) THEN', 'IF p_verified IS DISTINCT FROM true THEN')
    assert after[helper]['prosrc'] == expected_body
    assert after[helper]['provolatile'] == before['life_financial.carry_current_ready(uuid)']['provolatile']
    assert after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    for role in ('life_runtime_test', 'life_reporter_test', 'public'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'carry_checks', 'carry_payments', 'shared_capital_claims'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_chain', [False, True])
async def test_snapshot_body_upgrade_preserves_old_oids_acl_and_atomic_shape_refusal(db, unexpected_chain):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 55:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('GRANT USAGE ON SCHEMA life_financial TO life_runtime_test,life_reporter_test')
    await c.execute('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO life_runtime_test,life_reporter_test')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    if unexpected_chain:
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure)")
        await c.execute(definition.replace("(p_account)-'digest'-'observed_at'", "(p_account) - 'digest' - 'observed_at'"))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile,p.prosecdef,
        p.proconfig,p.proisstrict,p.proparallel,p.proleakproof,p.proowner,p.prorettype,p.proargnames
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    path = DEFAULT_DIRECTORY / '000055_snapshot_body_pipeline.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_chain:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_SNAPSHOT_BODY_CHAIN_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 54
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    states = {s for s in before if s.startswith('life_financial.spot_state')}
    bodies = {s.replace('spot_state', 'spot_body') for s in states}
    assert len(states) == 16 and set(after) - set(before) == bodies
    for signature, row in before.items():
        if signature not in states:
            assert after[signature] == row  # Every financial/COMMIT proof stays exact.
            continue
        assert {k: v for k, v in after[signature].items() if k != 'prosrc'} == {k: v for k, v in row.items() if k != 'prosrc'}
        body_signature = signature.replace('spot_state', 'spot_body')
        body = after[body_signature]
        for key in ('provolatile', 'prosecdef', 'proconfig', 'proisstrict', 'proparallel', 'proleakproof', 'proowner', 'prorettype', 'proargnames'):
            assert body[key] == row[key]
        assert body_signature.split('(')[0] + '(p_account)' in after[signature]['prosrc']
        assert "'digest'" in after[signature]['prosrc'] and 'clock_timestamp()' in after[signature]['prosrc']
        for role in ('life_runtime_test', 'life_reporter_test', 'public'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, body_signature)
    assert (await migrate(c, directory, action='up'))['applied'] == []
    for table in ('accounts', 'balances', 'operations', 'carry_checks', 'shared_capital_claims'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
async def test_legacy_spot_basis_upgrade_is_private_preserves_old_functions_and_bootstraps_nothing(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 56:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    if unexpected_shape:
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.legacy_applying_verified(uuid)'::regprocedure)")
        await c.execute(definition.replace('legacy_cashflow_verified(p_account)', 'legacy_cashflow_verified( p_account )'))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    path = DEFAULT_DIRECTORY / '000056_legacy_spot_basis.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_SPOT_BASIS_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 55
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    new = {'life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)', 'life_financial.legacy_spot_verified(uuid)'}
    assert set(after) - set(before) == new
    for signature, original in before.items():
        actual = after[signature]
        if signature == 'life_financial.legacy_applying_verified(uuid)':
            assert {k: v for k, v in actual.items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
            assert 'legacy_spot_verified(p_account)' in actual['prosrc']
        else:
            assert actual == original
    for signature in new:
        for role in ('public', 'life_runtime_test', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    for table in ('accounts', 'balances', 'operations', 'spot_preparations', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
async def test_collateral_context_upgrade_is_private_read_only_and_preserves_existing_financial_functions(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 57:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    if unexpected_shape:
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_provider_route(text,jsonb)'::regprocedure)")
        await c.execute(definition.replace("THEN '{}'::jsonb ELSE NULL END;", "THEN  '{}'::jsonb ELSE NULL END;"))
        before = {r['signature']: dict(r) for r in await c.fetch(query)}
    path = DEFAULT_DIRECTORY / '000057_collateral_source_context.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_COLLATERAL_SOURCE_ROUTE_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 56
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.collateral_reconciliation_context(uuid,integer,integer)'
    assert set(after) - set(before) == {helper}
    for key, value in before.items():
        if key == 'life_financial.spot_provider_route(text,jsonb)':
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in value.items() if k != 'prosrc'}
        else:
            assert after[key] == value
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'balances', 'operations', 'shared_capital_claims', 'spot_provider_pages'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'pending', 'proof'])
async def test_legacy_spot_prefix_upgrade_preserves_original_functions_acl_and_rolls_back_unknown_shapes(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 58:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    pending = 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    proof = 'life_financial.legacy_spot_verified(uuid)'
    if unexpected_shape != 'none':
        signature = pending if unexpected_shape == 'pending' else proof
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        needle = 'financial_events WHERE account_id' if unexpected_shape == 'pending' else 'WHERE i.account_id=p_account'
        await c.execute(definition.replace(needle, needle.replace('WHERE', 'WHERE ')))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000058_legacy_spot_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_SPOT_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 57
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for signature, original in before.items():
        actual = after[signature]
        if signature in (pending, proof):
            assert {k: v for k, v in actual.items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
            assert 'legacy_spot_prefix_valid' in actual['prosrc']
        else:
            assert actual == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'prefix', 'pending'])
async def test_legacy_cashflow_prefix_upgrade_is_private_idempotent_and_atomic(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 59:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    prefix = 'life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'
    pending = 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    if unexpected_shape != 'none':
        signature = prefix if unexpected_shape == 'prefix' else pending
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        needle = 'IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts' if unexpected_shape == 'prefix' else "e.product<>'SPOT'"
        await c.execute(definition.replace(needle, needle.replace(' ', '  ', 1) if unexpected_shape == 'prefix' else "e.product <> 'SPOT'"))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000059_legacy_cashflow_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_CASHFLOW_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 58
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_cashflow_prefix_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        actual = after[key]
        if key in (prefix, pending):
            assert {k: v for k, v in actual.items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert actual == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'prefix', 'pending'])
async def test_legacy_derivative_prefix_upgrade_is_private_idempotent_and_atomic(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 60:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    prefix = 'life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'
    pending = 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    if unexpected_shape != 'none':
        signature = prefix if unexpected_shape == 'prefix' else pending
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        needle = 'OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts' if unexpected_shape == 'prefix' else 'ELSE true END'
        await c.execute(definition.replace(needle, needle.replace(' ', '  ', 1)))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000060_legacy_derivative_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_DERIVATIVE_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 59
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_derivative_prefix_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        actual = after[key]
        if key in (prefix, pending):
            assert {k: v for k, v in actual.items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert actual == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'prefix', 'pending'])
async def test_legacy_parent_prefix_upgrade_is_private_idempotent_and_atomic(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 61:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    prefix = 'life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'
    pending = 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    if unexpected_shape != 'none':
        signature = prefix if unexpected_shape == 'prefix' else pending
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        needle = 'OR NOT life_financial.legacy_derivative_prefix_valid' if unexpected_shape == 'prefix' else 'OR EXISTS(SELECT 1 FROM life_financial.joint_financial_batches'
        await c.execute(definition.replace(needle, needle.replace(' ', '  ', 1)))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000061_legacy_parent_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_PARENT_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 60
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_parent_prefix_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        actual = after[key]
        if key in (prefix, pending):
            assert {k: v for k, v in actual.items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert actual == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
async def test_legacy_unsent_spot_upgrade_preserves_catalog_and_rejects_unknown_predecessor(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 62:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    signature = 'life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)'
    if unexpected_shape:
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace("operation_id='arm:'", "operation_id = 'arm:'"))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000062_legacy_unsent_spot.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_UNSENT_SPOT_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 61
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_spot_unsent_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        if key == signature:
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert after[key] == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'pending', 'source', 'batch'])
async def test_original_ack_prefix_upgrade_is_private_and_atomic_without_money_or_table_changes(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 63:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    pending = 'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    source = 'life_financial.legacy_spot_verified(uuid)'
    batch = 'life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    if unexpected_shape != 'none':
        signature, needle = {
            'pending': (pending, 'OR NOT life_financial.legacy_parent_prefix_valid'),
            'source': (source, 'AND NOT life_financial.legacy_spot_prefix_valid'),
            'batch': (batch, "RAISE EXCEPTION 'LIFE_JOINT_MEMBER_PENDING'"),
        }[unexpected_shape]
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace(needle, needle.replace(' ', '  ', 1)))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000063_legacy_spot_ack_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_ACK_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 62
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.legacy_ack_prefix_valid(uuid,jsonb,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        if key in (pending, source, batch):
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert after[key] == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
async def test_repaired_parent_upgrade_keeps_all_oids_acls_tables_and_is_atomic_and_idempotent(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 64:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.legacy_parent_prefix_valid(uuid,jsonb,bigint)'
    if unexpected_shape:
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace("IF r.state IS DISTINCT FROM 'APPLIED'", "IF  r.state IS DISTINCT FROM 'APPLIED'"))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000064_legacy_repaired_parent_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_REPAIRED_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 63
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) == set(before)
    for key, original in before.items():
        if key == signature:
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert after[key] == original
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
@pytest.mark.parametrize('version,profile', [(65, 'cancel'), (66, 'terminal'), (67, 'settlement')])
async def test_lifecycle_history_upgrade_is_private_atomic_and_idempotent_without_money_or_table_changes(db, unexpected_shape, version, profile):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < version:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    signature = 'life_financial.legacy_spot_verified(uuid)'
    if unexpected_shape:
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace('SELECT NOT EXISTS', 'SELECT  NOT EXISTS'))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / f'{version:06d}_legacy_{profile}_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match=f'LIFE_LEGACY_{profile.upper()}_PREFIX_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == version - 1
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = f'life_financial.legacy_{profile}_prefix_valid(uuid,jsonb,bigint)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        if key == signature:
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert after[key] == original
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
    assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, 'basis', 'cancel', 'verified'])
async def test_abort_nav_upgrade_is_private_atomic_and_idempotent_without_money_or_table_changes(db, unexpected_shape):
    version = 68
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < version:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    await c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA life_financial GRANT EXECUTE ON FUNCTIONS TO life_runtime_test,life_reporter_test')
    signature = 'life_financial.legacy_spot_verified(uuid)'
    if unexpected_shape:
        target, needle = {
            'basis': ('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)', 'CASE WHEN COALESCE'),
            'cancel': ('life_financial.legacy_cancel_prefix_valid(uuid,jsonb,bigint)', 'WHERE e.account_id=p_account AND (o.committed_version'),
            'verified': (signature, 'SELECT NOT EXISTS'),
        }[unexpected_shape]
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        assert needle in definition
        await c.execute(definition.replace(needle, needle.replace(' ', '  ')))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000068_legacy_abort_nav_history.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_LIFECYCLE_HISTORY_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == version - 1
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helpers = {f'life_financial.legacy_{profile}_prefix_valid(uuid,jsonb,bigint)' for profile in ('abort', 'nav')}
    assert set(after) - set(before) == helpers
    for key, original in before.items():
        if key in {signature, 'life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)', 'life_financial.legacy_cancel_prefix_valid(uuid,jsonb,bigint)'}:
            assert {k: v for k, v in after[key].items() if k != 'prosrc'} == {k: v for k, v in original.items() if k != 'prosrc'}
        else:
            assert after[key] == original
    for helper in helpers:
        for role in ('public', 'life_runtime_test', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
        assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef']
        assert after[helper]['proconfig'] == ['search_path=pg_catalog']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, 'joint', 'prepare', 'arm'])
async def test_mixed_outer_claim_upgrade_changes_only_original_private_proof_without_seeding_money(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 69:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)'
    if unexpected_shape:
        target, needle = {
            'joint': (signature, "OR c.product<>'SWAP'"),
            'prepare': ('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)', "AND o.command->>'kind'='SPOT_PREPARE'"),
            'arm': ('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)', "AND o.command->>'prepare'=d.operation_id::text AND o.committed_version<=p_version"),
        }[unexpected_shape]
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        assert needle in definition
        await c.execute(definition.replace(needle, needle.replace(' ', '  ')))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000069_legacy_joint_spot_claims.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_LEGACY_JOINT_SPOT_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 68
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) == set(before)
    for key, original in before.items():
        assert after[key] == (original if key not in {signature, 'life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)'} else {**original, 'prosrc': after[key]['prosrc']})
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, 'batch', 'view', 'repair', 'capital', 'carry', 'intervening'])
async def test_prewrite_repair_view_upgrade_is_private_atomic_and_preserves_public_contracts(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 70:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    batch = 'life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
    repair = batch.replace('joint_batch_command', 'joint_repair_command')
    capital = batch.replace('joint_batch_command', 'shared_capital_command')
    carry = batch.replace('joint_batch_command', 'carry_check')
    if unexpected_shape:
        target, needle = {
            'batch': (batch, "IF NOT life_financial.joint_batch_verified(p_account)"),
            'view': (batch, 'view:=life_financial.spot_state(p_account);'),
            'repair': (repair, 'child:=life_financial.joint_batch_command('),
            'intervening': (repair, 'view:=life_financial.spot_state(p_account);'),
            'capital': (capital, 'IF NOT life_financial.shared_capital_verified(p_account)'),
            'carry': (carry, 'IF NOT life_financial.carry_verified(p_account)'),
        }[unexpected_shape]
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        assert definition.count(needle) == 1
        changed = needle + '\n    PERFORM 1;' if unexpected_shape == 'intervening' else needle.replace(' ', '  ') if ' ' in needle else needle.replace(':=', ':= ')
        await c.execute(definition.replace(needle, changed))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000070_repair_view_reuse.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_REPAIR_VIEW_.*_MISMATCH|LIFE_ADMISSION_VIEW_.*_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 69
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helper = 'life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        assert after[key] == (original if key not in {repair, capital, carry} else {**original, 'prosrc': after[key]['prosrc']})
    assert after[helper]['provolatile'] == 'v' and after[helper]['prosecdef'] and after[helper]['proconfig'] == ['search_path=pg_catalog']
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', [False, True])
async def test_required_original_ack_upgrade_is_private_atomic_and_preserves_old_diagnostics(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 71:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.legacy_ack_prefix_valid(uuid,jsonb,jsonb,bigint)'
    if unexpected_shape:
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        needle = 'FOR o IN SELECT * FROM life_financial.operations'
        assert definition.count(needle) == 1
        await c.execute(definition.replace(needle, 'FOR o IN  SELECT * FROM life_financial.operations'))
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000071_required_ack_completion.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    helper = 'life_financial.legacy_required_ack_prefix_valid(uuid,jsonb,bigint)'
    if unexpected_shape:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_REQUIRED_ACK_PREFIX_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 70
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) - set(before) == {helper}
    for key, original in before.items():
        assert after[key] == (original if key != signature else {**original, 'prosrc': after[key]['prosrc']})
    assert after[helper]['provolatile'] == 's' and after[helper]['prosecdef'] and after[helper]['proconfig'] == ['search_path=pg_catalog']
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    assert await c.fetchval('SELECT life_financial.legacy_required_ack_prefix_valid(gen_random_uuid(),$1::jsonb,0)', '{}')
    for basis in ('{"spot_required_ack_ids":["missing"]}', '{"spot_required_ack_ids":["x","x"]}',
                  '{"spot_required_ack_ids":null}', '{"spot_required_ack_ids":[1]}'):
        assert not await c.fetchval('SELECT life_financial.legacy_required_ack_prefix_valid(gen_random_uuid(),$1::jsonb,0)', basis)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'body', 'search_path', 'security'])
async def test_unfilled_swap_prefix_upgrade_preserves_original_oids_acl_tables_and_refuses_unknown_program(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 72:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)'
    if unexpected_shape == 'body':
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        assert 'RETURN false;END IF;' in definition
        await c.execute(definition.replace('RETURN false;END IF;', 'RETURN true;END IF;', 1))
    elif unexpected_shape == 'search_path':
        await c.execute('ALTER FUNCTION ' + signature + ' SET search_path=pg_catalog,public')
    elif unexpected_shape == 'security':
        await c.execute('ALTER FUNCTION ' + signature + ' SECURITY INVOKER')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000072_unfilled_swap_prefix.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_UNFILLED_SWAP_PREFIX_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 71
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) == set(before)
    for key, original in before.items():
        assert after[key] == (original if key != signature else {**original, 'prosrc': after[key]['prosrc']})
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'legacy_applying_imports'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'body', 'search_path', 'security', 'volatility'])
async def test_joint_provider_routes_upgrade_preserves_oids_acl_and_refuses_unknown_predecessor(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 73:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    signature = 'life_financial.spot_provider_route(text,jsonb)'
    if unexpected_shape == 'body':
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
        await c.execute(definition.replace("extra:='{}'::jsonb;", "extra:= '{}'::jsonb;", 1))
    elif unexpected_shape == 'search_path':
        await c.execute('ALTER FUNCTION ' + signature + ' SET search_path=pg_catalog,public')
    elif unexpected_shape == 'security':
        await c.execute('ALTER FUNCTION ' + signature + ' SECURITY DEFINER')
    elif unexpected_shape == 'volatility':
        await c.execute('ALTER FUNCTION ' + signature + ' STABLE')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000073_joint_provider_routes.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_JOINT_SOURCE_ROUTE_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 72
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) == set(before)
    for key, original in before.items():
        assert after[key] == (original if key != signature else {**original, 'prosrc': after[key]['prosrc']})
    for role in ('public', 'life_runtime_test', 'life_reporter_test'):
        assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    for params, accepted in [
        ({'limit': '100'}, True),
        ({'limit': '100', 'after': '123'}, True),
        ({'limit': '100', 'instId': 'LIFE-USDT'}, False),
        ({'instType': 'SWAP', 'limit': '100'}, True),
        ({'instType': 'SWAP', 'limit': '100', 'after': '123'}, True),
        ({'instType': 'SWAP', 'limit': '100', 'instId': 'LIFE-USDT-SWAP'}, False),
        ({'instType': 'SWAP', 'limit': '100', 'after': '1&instId=LIFE-USDT-SWAP'}, False),
        ({'instType': 'FUTURES', 'limit': '100'}, False),
        ({'instType': 'SWAP', 'limit': 100}, False),
    ]:
        assert await c.fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)',
                                '/api/v5/trade/orders-pending', json.dumps(params)) is accepted
    for product, accepted in [('SPOT', False), ('SWAP', True)]:
        assert await c.fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)', '/api/v5/trade/orders-algo-pending',
                                json.dumps(dict(instType=product, limit='100', ordType='chase'))) is accepted
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'spot_provider_heads', 'spot_provider_pages'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
async def test_joint_source_deadline_upgrade_adds_only_private_checks_and_defers_after_existing_guards(db):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 74:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000074_joint_source_deadline.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    added = {'life_financial.check_joint_source_deadline(uuid,text)', 'life_financial.joint_source_commit_guard()'}
    assert set(after) - set(before) == added
    assert all(after[key] == value for key, value in before.items())
    for helper in added:
        assert after[helper]['prosecdef'] and after[helper]['proconfig'] == ['search_path=pg_catalog']
        for role in ('public', 'life_runtime_test', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, helper)
    triggers = await c.fetch('''SELECT tgname,tgdeferrable,tginitdeferred FROM pg_trigger
        WHERE tgrelid='life_financial.accounts'::regclass AND NOT tgisinternal AND tgdeferrable ORDER BY tgname''')
    assert triggers[-1]['tgname'] == 'zz_joint_source_account_commit'
    assert triggers[-1]['tgdeferrable'] and triggers[-1]['tginitdeferred']
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'financial_events', 'spot_provider_heads', 'spot_provider_pages'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'order_body', 'inventory_body', 'security', 'search_path'])
async def test_joint_trade_id_set_upgrade_is_atomic_preserves_port_oids_acl_and_does_not_rekey_fills(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 75:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    order = 'life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)'
    inventory = 'life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)'
    for port in (order, inventory):
        await c.execute('GRANT EXECUTE ON FUNCTION ' + port + ' TO life_runtime_test')
    if unexpected_shape in ('order_body', 'inventory_body'):
        target = order if unexpected_shape == 'order_body' else inventory
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        assert 'jsonb_agg(trade_id ORDER BY trade_id' in definition
        await c.execute(definition.replace('jsonb_agg(trade_id ORDER BY trade_id', 'jsonb_agg(trade_id  ORDER BY trade_id', 1))
    elif unexpected_shape == 'security':
        await c.execute('ALTER FUNCTION ' + order + ' SECURITY INVOKER')
    elif unexpected_shape == 'search_path':
        await c.execute('ALTER FUNCTION ' + inventory + ' SET search_path=pg_catalog,public')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000075_joint_trade_id_sets.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_JOINT_TRADE_SET_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert (await versions(c))[-1]['version'] == 74
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    assert set(after) == set(before)
    for key, original in before.items():
        assert after[key] == (original if key not in {order, inventory} else {**original, 'prosrc': after[key]['prosrc']})
    for port in (order, inventory):
        assert await c.fetchval("SELECT has_function_privilege('life_runtime_test',$1,'EXECUTE')", port)
        for role in ('public', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, port)
    assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    for table in ('accounts', 'operations', 'fills', 'fees', 'financial_events', 'budget_events'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'body', 'security', 'search_path', 'volatility'])
async def test_signed_native_control_upgrade_preserves_existing_guards_and_revokes_no_existing_acl(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 76:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    target = 'life_financial.guard_account(uuid,jsonb,jsonb,bigint)'
    await c.execute('GRANT EXECUTE ON FUNCTION ' + target + ' TO life_runtime_test')
    if unexpected_shape == 'body':
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        await c.execute(definition.replace('at := clock_timestamp();', 'at :=  clock_timestamp();', 1))
    elif unexpected_shape == 'security':
        await c.execute('ALTER FUNCTION ' + target + ' SECURITY INVOKER')
    elif unexpected_shape == 'search_path':
        await c.execute('ALTER FUNCTION ' + target + ' SET search_path=pg_catalog,public')
    elif unexpected_shape == 'volatility':
        await c.execute('ALTER FUNCTION ' + target + ' STABLE')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000076_signed_native_control.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_CONTROL_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
        assert (await versions(c))[-1]['version'] == 75
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    patched = {target, 'life_financial.guard_spot_observation(uuid,jsonb,jsonb,bigint)',
               'life_financial.commit_owner_guard()', 'life_financial.spot_state(uuid)'}
    helpers = {'life_financial.control_owner(jsonb)', 'life_financial.control_verified(uuid)',
               'life_financial.control_facts_qualified(uuid,jsonb)', 'life_financial.control_commit_check(uuid,text)'}
    assert set(after) - set(before) == helpers
    for signature, original in before.items():
        assert after[signature] == (original if signature not in patched else {**original, 'prosrc': after[signature]['prosrc']})
    assert await c.fetchval("SELECT has_function_privilege('life_runtime_test',$1,'EXECUTE')", target)
    for signature in helpers:
        for role in ('public', 'life_runtime_test', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    for table in ('accounts', 'operations', 'activation_grants', 'grant_consumptions', 'control_policies', 'control_records', 'financial_events'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unexpected_shape', ['none', 'body', 'security', 'search_path', 'volatility'])
async def test_native_successor_upgrade_preserves_control_oids_acl_and_rejects_changed_predecessor(db, unexpected_shape):
    c, _, directory = db
    for path in DEFAULT_DIRECTORY.glob('*.sql'):
        if int(path.name.split('_', 1)[0]) < 77:
            (directory / path.name).write_bytes(path.read_bytes())
    await migrate(c, directory, action='up')
    target = 'life_financial.control_verified(uuid)'
    await c.execute('GRANT EXECUTE ON FUNCTION ' + target + ' TO life_reporter_test')
    if unexpected_shape == 'body':
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', target)
        await c.execute(definition.replace('BEGIN', 'BEGIN\n-- unexpected predecessor', 1))
    elif unexpected_shape == 'security':
        await c.execute('ALTER FUNCTION ' + target + ' SECURITY INVOKER')
    elif unexpected_shape == 'search_path':
        await c.execute('ALTER FUNCTION ' + target + ' SET search_path=pg_catalog,public')
    elif unexpected_shape == 'volatility':
        await c.execute('ALTER FUNCTION ' + target + ' VOLATILE')
    query = '''SELECT p.oid,p.oid::regprocedure::text AS signature,p.proacl,p.prosrc,p.provolatile::text AS provolatile,p.prosecdef,p.proconfig,p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='life_financial' ORDER BY p.oid'''
    before = {r['signature']: dict(r) for r in await c.fetch(query)}
    tables = await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
    path = DEFAULT_DIRECTORY / '000077_reconciled_session_successors.sql'
    (directory / path.name).write_bytes(path.read_bytes())
    if unexpected_shape != 'none':
        with pytest.raises(asyncpg.RaiseError, match='LIFE_SUCCESSOR_CONTROL_MIGRATION_MISMATCH'):
            await migrate(c, directory, action='up')
        assert {r['signature']: dict(r) for r in await c.fetch(query)} == before
        assert tables == await c.fetch("SELECT oid,relname FROM pg_class WHERE relnamespace='life_financial'::regnamespace ORDER BY oid")
        assert (await versions(c))[-1]['version'] == 76
        return
    assert (await migrate(c, directory, action='up'))['applied'] == [path.name]
    after = {r['signature']: dict(r) for r in await c.fetch(query)}
    helpers = {'life_financial.control_successor_verified(uuid,text)', 'life_financial.control_successor_commit(uuid,text)'}
    assert set(after) - set(before) == helpers
    patched = {target, 'life_financial.control_commit_check(uuid,text)'}
    for signature, original in before.items():
        assert after[signature] == (original if signature not in patched else {**original, 'prosrc': after[signature]['prosrc']})
    for signature in helpers:
        for role in ('public', 'life_runtime_test', 'life_reporter_test'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    for table in ('accounts', 'operations', 'activation_grants', 'grant_consumptions', 'control_policies', 'control_records', 'session_successors'):
        assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    assert (await migrate(c, directory, action='up'))['applied'] == []
