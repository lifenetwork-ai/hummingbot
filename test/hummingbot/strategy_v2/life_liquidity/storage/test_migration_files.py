"""Migration validation runs without PostgreSQL or an optional driver."""
from pathlib import Path

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import MigrationError, load_migrations


def test_order_and_exact_bytes_digest(tmp_path):
    (tmp_path / '000002_second.sql').write_text('SELECT 2;\n')
    (tmp_path / '000001_first.sql').write_text('SELECT 1;\n')
    migrations = load_migrations(tmp_path)
    assert [m.version for m in migrations] == [1, 2]
    assert migrations[0].name == '000001_first.sql'
    assert len(migrations[0].checksum) == 64
    (tmp_path / '000001_first.sql').write_text('SELECT 1;\n\n')
    assert load_migrations(tmp_path)[0].checksum != migrations[0].checksum


@pytest.mark.parametrize('name', ['first.sql', '000000_zero.sql', '000001-Bad.sql'])
def test_invalid_sql_filename_is_not_silently_ignored(tmp_path, name):
    (tmp_path / name).write_text('SELECT 1;')
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)


def test_duplicate_version_empty_and_symlink_denied(tmp_path):
    first = tmp_path / '000001_first.sql'
    first.write_text('SELECT 1;')
    duplicate = tmp_path / '000001_duplicate.sql'
    duplicate.write_text('SELECT 2;')
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)
    duplicate.unlink()
    first.write_text('-- comments only\n')
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)
    first.unlink()
    first.symlink_to(Path(__file__))
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)


@pytest.mark.parametrize('sql', [
    'CREATE TABLE x(a int); COMMIT;', '/* nested /* x */ comment */ ROLLBACK;',
    '-- comment\nSTART TRANSACTION;', 'BEGIN;', 'END;', 'ABORT;',
    'SAVEPOINT s;', 'RELEASE SAVEPOINT s;', 'PREPARE TRANSACTION \'x\';',
    "SET search_path TO public;", 'RESET ALL;', 'DISCARD ALL;',
])
def test_runner_owns_transaction_and_session_settings(tmp_path, sql):
    (tmp_path / '000001_unsafe.sql').write_text(sql)
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)


def test_quoted_body_and_comment_are_not_transaction_commands(tmp_path):
    (tmp_path / '000001_function.sql').write_text('''
        -- COMMIT is not executable here
        CREATE FUNCTION example() RETURNS text LANGUAGE plpgsql AS $body$
        BEGIN RETURN 'COMMIT; SET'; END;
        $body$;
        SELECT 'ROLLBACK', E'quote\\\'COMMIT', "BEGIN" FROM "SET";
    ''')
    assert len(load_migrations(tmp_path)) == 1


@pytest.mark.parametrize('sql', ["SELECT 'unterminated", '/* open', 'DO $tag$ BEGIN'])
def test_unterminated_sql_rejected_before_connect(tmp_path, sql):
    (tmp_path / '000001_invalid.sql').write_text(sql)
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)


@pytest.mark.parametrize('dsn,insecure', [
    ('postgresql://user@database.internal/db', True),
    ('postgresql://user@127.0.0.1/db?host=remote', True),
    ('postgresql://user@localhost/db', True),
    ('not-a-postgres-url', False),
])
def test_insecure_cli_cannot_escape_loopback_or_override_connection(dsn, insecure):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import _ssl_context
    with pytest.raises(MigrationError):
        _ssl_context(dsn, insecure, None)


def test_cli_tls_default_validates_peer_and_local_bypass_is_explicit():
    import ssl

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import _ssl_context
    context = _ssl_context('postgresql://user@database.internal/db', False, None)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert _ssl_context('postgresql://user@127.0.0.1/db', True, None) is False
