"""Forward-only, serialized migrations for trusted, reviewed repository SQL.

Run separately from workers: python -m ...storage.postgres.migrations status|up.
No automatic rollback/retry: an ambiguous COMMIT is resolved by a fresh run.
"""
import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import DEFAULT_DIRECTORY, load_config
from hummingbot.strategy_v2.life_liquidity.storage.postgres.errors import MigrationError

LOCK_KEY = 0x4C4946454D494752  # LIFE MIGR, database-wide cooperative session lock.
FILENAME = re.compile(r'([0-9]{6})_([a-z][a-z0-9_]*)\.sql\Z')


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def _unquoted(sql):
    """Mask PostgreSQL quotes/comments; reject incomplete input.

    This is a conservative transaction-command guard, not a SQL validator or
    sandbox. Migration authors/credentials are trusted schema administrators.
    """
    result, i = [], 0
    while i < len(sql):
        if sql.startswith('--', i):
            end = sql.find('\n', i + 2)
            i = len(sql) if end < 0 else end + 1
            result.append(' ')
        elif sql.startswith('/*', i):
            depth, i = 1, i + 2
            while i < len(sql) and depth:
                if sql.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise MigrationError('Unterminated SQL comment')
            result.append(' ')
        elif sql[i] in "'\"":
            quote = sql[i]
            escaped = quote == "'" and i > 0 and sql[i - 1] in 'eE' and (
                i == 1 or not (sql[i - 2].isalnum() or sql[i - 2] == '_'))
            i += 1
            while i < len(sql):
                if escaped and sql[i] == '\\':
                    i += 2
                elif sql[i] == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            else:
                raise MigrationError('Unterminated SQL quote')
            result.append(' ')
        elif sql[i] == '$' and (match := re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', sql[i:])):
            delimiter = match.group()
            end = sql.find(delimiter, i + len(delimiter))
            if end < 0:
                raise MigrationError('Unterminated SQL dollar quote')
            i = end + len(delimiter)
            result.append(' ')
        else:
            result.append(sql[i])
            i += 1
    return ''.join(result)


def load_migrations(directory):
    directory = Path(directory)
    migrations, versions = [], set()
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() != '.sql':
            continue
        match = FILENAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            raise MigrationError('Invalid migration filename or file type')
        version = int(match[1])
        if version < 1 or version in versions:
            raise MigrationError('Invalid or duplicate migration version')
        versions.add(version)
        if path.stat().st_size > 1024 * 1024:
            raise MigrationError('Migration exceeds 1 MiB; use a separate bounded backfill')
        raw = path.read_bytes()
        sql = raw.decode('utf-8')
        visible = _unquoted(sql)
        if not visible.strip(' ;\n\r\t'):
            raise MigrationError('Empty migration')
        for statement in visible.split(';'):
            words = re.findall(r'[A-Za-z_]+', statement.upper())
            if words and words[0] in {'BEGIN', 'START', 'COMMIT', 'END', 'ABORT', 'ROLLBACK',
                                      'SAVEPOINT', 'RELEASE', 'PREPARE', 'SET', 'RESET', 'DISCARD'}:
                raise MigrationError('Runner owns transaction and session settings')
        migrations.append(Migration(version, path.name, hashlib.sha256(raw).hexdigest(), sql))
    if not migrations:
        raise MigrationError('No migrations found')
    return tuple(sorted(migrations, key=lambda item: item.version))


async def _history(connection, migrations):
    exists = await connection.fetchval("SELECT to_regclass('life_migrations.schema_migrations') IS NOT NULL")
    if not exists:
        if await connection.fetchval("SELECT to_regnamespace('life_migrations') IS NOT NULL"):
            raise MigrationError('Unregistered migration schema; manual inspection required')
        return False, 0
    rows = await connection.fetch('SELECT version,name,checksum FROM life_migrations.schema_migrations ORDER BY version')
    if len(rows) > len(migrations):
        raise MigrationError('Database is ahead of this migration bundle')
    for row, migration in zip(rows, migrations):
        if (row['version'], row['name'], row['checksum']) != (migration.version, migration.name, migration.checksum):
            raise MigrationError('Applied migration missing, reordered, renamed or checksum changed')
    return True, len(rows)


async def _settings(connection, statement_timeout, ddl_lock_timeout):
    await connection.execute("SELECT set_config('statement_timeout',$1,true)", f'{statement_timeout}ms')
    await connection.execute("SELECT set_config('lock_timeout',$1,true)", f'{ddl_lock_timeout}ms')


async def migrate(connection, directory=DEFAULT_DIRECTORY, *, action='status', lock_timeout=10.0,
                  statement_timeout=30000, ddl_lock_timeout=5000):
    """Use one dedicated, non-pooled connection; caller owns its lifecycle.

    Only cooperative migration concurrency is fenced. No exchange or worker
    ownership is granted. Timings are migration CLI defaults, not trading values.
    """
    if action not in {'status', 'up'} or not 0 < lock_timeout <= 600:
        raise MigrationError('Invalid action or advisory lock timeout')
    if not 0 < ddl_lock_timeout <= statement_timeout <= 600000:
        raise MigrationError('Invalid SQL timeouts')
    if connection.is_in_transaction():
        raise MigrationError('Migrations require a dedicated connection outside a transaction')
    migrations = load_migrations(directory)  # Freeze SQL bytes before acquiring the lock.
    if await connection.fetchval('SELECT pg_is_in_recovery()'):
        raise MigrationError('Migrations require the primary')
    deadline, locked = time.monotonic() + lock_timeout, False
    try:
        while not locked:
            locked = await connection.fetchval('SELECT pg_try_advisory_lock($1)', LOCK_KEY)
            if not locked:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MigrationError('Migration advisory lock timeout')
                await asyncio.sleep(min(.05, remaining))
        await connection.execute("SET standard_conforming_strings = on")
        exists, applied = await _history(connection, migrations)
        pending = migrations[applied:]
        if action == 'up':
            if not exists:
                async with connection.transaction():
                    await _settings(connection, statement_timeout, ddl_lock_timeout)
                    await connection.execute('''CREATE SCHEMA life_migrations;
                        REVOKE ALL ON SCHEMA life_migrations FROM PUBLIC;
                        CREATE TABLE life_migrations.schema_migrations (
                            version bigint PRIMARY KEY CHECK(version > 0), name text NOT NULL UNIQUE,
                            checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
                            applied_at timestamptz NOT NULL DEFAULT clock_timestamp());
                        REVOKE ALL ON life_migrations.schema_migrations FROM PUBLIC;''')
            for migration in pending:
                async with connection.transaction():
                    await _settings(connection, statement_timeout, ddl_lock_timeout)
                    await connection.execute('SET LOCAL search_path = pg_catalog')
                    await connection.execute(migration.sql)
                    await connection.execute('''INSERT INTO life_migrations.schema_migrations(version,name,checksum)
                        VALUES ($1,$2,$3)''', migration.version, migration.name, migration.checksum)
        return {'action': action, 'applied': [m.name for m in pending] if action == 'up' else [],
                'pending': [m.name for m in pending] if action == 'status' else [],
                'verified': [m.name for m in migrations[:applied]], 'production_permission': False}
    finally:
        if locked and not connection.is_closed():
            await connection.execute('SELECT pg_advisory_unlock($1)', LOCK_KEY)


def _ssl_context(dsn, local_insecure, ca_file):
    url = urlsplit(dsn)
    if url.scheme not in {'postgres', 'postgresql'} or not url.hostname or url.query or url.fragment:
        raise MigrationError('Use a PostgreSQL URL without query options in LIFE_MIGRATION_DSN')
    if local_insecure:
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise MigrationError('Insecure mode requires a literal loopback IP')
        return False
    return ssl.create_default_context(cafile=str(ca_file) if ca_file else None)


async def run_configured_migrations(config, *, action='up'):
    config.validate()
    if not config.dsn:
        raise MigrationError('Set LIFE_MIGRATION_DSN for the migration role')
    load_migrations(config.directory)
    context = _ssl_context(config.dsn, config.local_insecure, config.ssl_ca_file)
    # Optional dependency: disabled startup/file-backend imports need no PG driver.
    try:
        import asyncpg
    except ImportError:
        raise MigrationError('Install setup/life_postgres_requirements.txt in the migration environment') from None
    connection = await asyncpg.connect(config.dsn, ssl=context, timeout=10,
                                       command_timeout=config.statement_timeout / 1000,
                                       server_settings={'application_name': 'life_schema_migrations'})
    try:
        return await migrate(connection, config.directory, action=action, lock_timeout=config.lock_timeout,
                             statement_timeout=config.statement_timeout, ddl_lock_timeout=config.ddl_lock_timeout)
    finally:
        await connection.close(timeout=5)


async def auto_migrate(config=None):
    """Await optional startup migrations before any application/connector exists.

    A failed or cancelled migration blocks startup. Never return a trading grant.
    Remove an inherited migration DSN before the application can spawn children.
    """
    config = load_config() if config is None else config.validate()
    if not config.auto_migrate:
        return None
    try:
        if not config.dsn:
            raise MigrationError('LIFE_AUTO_MIGRATE requires LIFE_MIGRATION_DSN')
        return await run_configured_migrations(config)
    except MigrationError:
        raise
    except Exception:
        raise MigrationError('Startup migration failed; inspect status before restarting') from None
    finally:
        os.environ.pop('LIFE_MIGRATION_DSN', None)


async def _cli(args):
    config = load_config(env_file=args.env_file, no_env_file=args.no_env_file)
    overrides = {name: getattr(args, name) for name in
                 ('directory', 'local_insecure', 'ssl_ca_file', 'lock_timeout',
                  'statement_timeout', 'ddl_lock_timeout') if getattr(args, name) is not None}
    config = replace(config, **overrides).validate()
    result = await run_configured_migrations(config, action=args.action)
    print(json.dumps(result, sort_keys=True))
    return 2 if result['pending'] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'up'])
    parser.add_argument('--directory', type=Path, default=None)
    parser.add_argument('--lock-timeout', type=float, default=None, help='Advisory lock wait in seconds')
    parser.add_argument('--statement-timeout', type=int, default=None, help='SQL timeout in milliseconds')
    parser.add_argument('--ddl-lock-timeout', type=int, default=None, help='DDL lock wait in milliseconds')
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument('--local-insecure', action='store_true', default=None, help='Disable TLS for literal loopback test DB only')
    tls.add_argument('--require-tls', action='store_false', dest='local_insecure', default=None)
    parser.add_argument('--ssl-ca-file', type=Path)
    files = parser.add_mutually_exclusive_group()
    files.add_argument('--env-file', type=Path)
    files.add_argument('--no-env-file', action='store_true')
    args = parser.parse_args()
    try:
        return asyncio.run(_cli(args))
    except (Exception, KeyboardInterrupt) as exc:
        # SQL/driver exceptions can contain DSNs, SQL literals or secrets. Do not print them.
        print(json.dumps({'error': type(exc).__name__, 'sqlstate': getattr(exc, 'sqlstate', None),
                          'message': str(exc) if isinstance(exc, MigrationError) else
                          'Migration failed; inspect database state and rerun status before up'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
