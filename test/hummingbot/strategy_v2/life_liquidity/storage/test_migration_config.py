"""Local env parsing and startup gating; never connect to a configured database."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import MigrationConfig, load_config
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import MigrationError, auto_migrate


def test_safe_defaults_without_env_file():
    config = load_config(environ={'LIFE_ENV_FILE': ''})
    assert not config.auto_migrate and not config.local_insecure
    assert config.dsn == '' and config.statement_timeout == 30000


def test_dotenv_literal_values_os_precedence_and_relative_paths(tmp_path):
    file = tmp_path / '.env'
    file.write_text('''
        # local configuration
        LIFE_AUTO_MIGRATE=true
        LIFE_MIGRATION_DSN='postgresql://user:literal$()@127.0.0.1/db'
        LIFE_MIGRATION_DIRECTORY=sql
        export LIFE_MIGRATION_LOCAL_INSECURE=true
        LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS=2.5
        UNRELATED_PASSWORD='not imported'
    ''')
    environ = {'LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS': '3'}
    config = load_config(env_file=file, environ=environ)
    assert config.auto_migrate and config.local_insecure
    assert config.dsn == 'postgresql://user:literal$()@127.0.0.1/db'
    assert config.directory == tmp_path / 'sql' and config.lock_timeout == 3
    assert environ == {'LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS': '3'}
    assert config.dsn not in repr(config)


@pytest.mark.parametrize('key,value', [
    ('LIFE_AUTO_MIGRATE', 'tru'), ('LIFE_MIGRATION_LOCAL_INSECURE', 'yes'),
    ('LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS', 'nan'), ('LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS', '0'),
    ('LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS', 'inf'), ('LIFE_MIGRATION_STATEMENT_TIMEOUT_MS', '0'),
    ('LIFE_MIGRATION_DDL_LOCK_TIMEOUT_MS', '40000'), ('LIFE_MIGRATION_STATEMENT_TIMEOUT_MS', 'secret'),
])
def test_bad_config_rejected_without_echoing_values(key, value):
    with pytest.raises(MigrationError) as error:
        load_config(environ={'LIFE_ENV_FILE': '', key: value})
    assert value not in str(error.value)


def test_explicit_missing_file_and_duplicate_key_rejected(tmp_path):
    file = tmp_path / '.env'
    with pytest.raises(MigrationError):
        load_config(env_file=file, environ={})
    file.write_text('LIFE_AUTO_MIGRATE=false\nLIFE_AUTO_MIGRATE=true\n')
    with pytest.raises(MigrationError):
        load_config(env_file=file, environ={})


def test_dotenv_no_interpolation_or_shell_execution(tmp_path):
    file = tmp_path / '.env'
    marker = tmp_path / 'should_not_exist'
    literal = f'$(touch {marker})'
    file.write_text(f'LIFE_MIGRATION_DSN="{literal}"\n')
    assert load_config(env_file=file, environ={}).dsn == literal
    assert not marker.exists()


@pytest.mark.asyncio
async def test_disabled_auto_migration_never_opens_database():
    with patch('hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations.run_configured_migrations',
               new_callable=AsyncMock) as run:
        assert await auto_migrate(MigrationConfig()) is None
        run.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_gate_awaits_success_and_scrubs_migration_env(monkeypatch):
    gate = asyncio.Event()
    monkeypatch.setenv('LIFE_MIGRATION_DSN', 'synthetic-secret')

    async def delayed(*args):
        await gate.wait()
        return {'applied': ['000001_example.sql']}

    with patch('hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations.run_configured_migrations',
               new=AsyncMock(side_effect=delayed)) as run:
        task = asyncio.create_task(auto_migrate(replace(MigrationConfig(), auto_migrate=True, dsn='synthetic-secret')))
        await asyncio.sleep(0)
        assert not task.done()
        gate.set()
        assert await task == {'applied': ['000001_example.sql']}
        run.assert_awaited_once()
    import os
    assert 'LIFE_MIGRATION_DSN' not in os.environ


@pytest.mark.asyncio
async def test_auto_migration_failure_redacts_driver_exception(monkeypatch):
    monkeypatch.setenv('LIFE_MIGRATION_DSN', 'synthetic-secret')
    with patch('hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations.run_configured_migrations',
               new=AsyncMock(side_effect=RuntimeError('synthetic-secret'))) as run:
        with pytest.raises(MigrationError) as error:
            await auto_migrate(replace(MigrationConfig(), auto_migrate=True, dsn='synthetic-secret'))
        assert 'synthetic-secret' not in str(error.value)
        assert error.value.__suppress_context__
        run.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_migration_requires_dsn_before_attempting_sql():
    with patch('hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations.run_configured_migrations',
               new_callable=AsyncMock) as run:
        with pytest.raises(MigrationError):
            await auto_migrate(replace(MigrationConfig(), auto_migrate=True))
        run.assert_not_awaited()
