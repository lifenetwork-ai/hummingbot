import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hummingbot.client.config.runtime_config import (
    RuntimeConfigurationError,
    RuntimeSettings,
    load_environment,
    parse_boolean,
    postgres_requested,
)


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), (" TRUE ", True), ("False", False)])
def test_explicit_boolean(value, expected):
    assert parse_boolean(value) is expected


@pytest.mark.parametrize("value", ["", "1", "yes", "0", "not-a-boolean"])
def test_invalid_boolean(value):
    with pytest.raises(RuntimeConfigurationError, match="true or false"):
        parse_boolean(value)


def test_url_escaping_defaults_and_redaction():
    settings = RuntimeSettings.from_environment({
        "DATABASE_URL": "postgresql://bot:p%40ss%23%24@localhost:5432/bot?sslmode=require",
    })
    assert settings.database_url.password == "p@ss#$"
    assert settings.database_url.query["sslmode"] == "require"
    assert settings.database_url.drivername == "postgresql+psycopg2"
    assert settings.enable_auto_migration is False
    assert settings.port == 8080
    assert "p@ss" not in repr(settings)
    assert "DATABASE_URL" not in repr(settings)


@pytest.mark.parametrize("url", ["", "sqlite:///secret", "postgresql+asyncpg://a:secret@localhost/db", "postgresql://secret", "postgresql://a:secret@host:bad/db"])
def test_bad_urls_never_expose_input(url):
    with pytest.raises(RuntimeConfigurationError) as error:
        RuntimeSettings.from_environment({"DATABASE_URL": url})
    assert "secret" not in str(error.value)


def test_dotenv_literal_values_and_process_precedence(tmp_path):
    file = tmp_path / "settings.env"
    file.write_text("DATABASE_URL=postgresql://file:pass@host/db\nTOKEN='p${HOME}#$'\nEMPTY=file\nENABLE_AUTO_MIGRATION=false\n")
    with patch.dict(os.environ, {"HBOT_ENV_FILE": str(file), "DATABASE_URL": "process", "EMPTY": ""}, clear=True):
        load_environment()
        assert os.environ["DATABASE_URL"] == "process"
        assert os.environ["EMPTY"] == ""
        assert os.environ["TOKEN"] == "p${HOME}#$"
        assert os.environ["ENABLE_AUTO_MIGRATION"] == "false"


def test_explicit_missing_dotenv_fails(tmp_path):
    with patch.dict(os.environ, {"HBOT_ENV_FILE": str(tmp_path / "missing")}, clear=True):
        with pytest.raises(RuntimeConfigurationError, match="does not exist"):
            load_environment()


def test_missing_default_dotenv_allowed(tmp_path):
    with patch.dict(os.environ, {}, clear=True), patch.object(Path, "is_file", return_value=False):
        load_environment()
        assert not postgres_requested()


def test_cloud_ignores_dotenv_and_rejects_explicit_file():
    with patch.dict(os.environ, {"K_SERVICE": "bot"}, clear=True), patch.object(Path, "is_file") as read:
        load_environment()
        read.assert_not_called()
        assert postgres_requested()
        os.environ["HBOT_ENV_FILE"] = "/any/file"
        with pytest.raises(RuntimeConfigurationError, match="Cloud Run"):
            load_environment()


def test_empty_database_url_selects_pg_and_fails():
    with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=True):
        assert postgres_requested()
        with pytest.raises(RuntimeConfigurationError):
            RuntimeSettings.from_environment()


@pytest.mark.parametrize("mode,expected", [("file", False), ("postgres", True), (" POSTGRES ", True)])
def test_explicit_storage_backend_overrides_local_database_detection(mode, expected):
    with patch.dict(os.environ, {"STORAGE_BACKEND": mode, "DATABASE_URL": ""}, clear=True):
        assert postgres_requested() is expected


@pytest.mark.parametrize("mode", ["", "sqlite", "mysql", "true"])
def test_invalid_storage_backend_fails_without_echoing_values(mode):
    with patch.dict(os.environ, {"STORAGE_BACKEND": mode}, clear=True):
        with pytest.raises(RuntimeConfigurationError, match="STORAGE_BACKEND"):
            postgres_requested()


def test_file_backend_is_not_allowed_on_cloud_run():
    with patch.dict(os.environ, {"STORAGE_BACKEND": "file", "K_SERVICE": "test"}, clear=True):
        with pytest.raises(RuntimeConfigurationError, match="Cloud Run"):
            postgres_requested()


def test_env_file_uses_process_over_file_over_defaults(tmp_path):
    file = tmp_path / "selected.env"
    file.write_text("STORAGE_BACKEND=postgres\nPORT=9000\nBOT_ID=file-bot\n")
    with patch.dict(os.environ, {"ENV_FILE": str(file), "PORT": "9001"}, clear=True):
        load_environment()
        assert postgres_requested()
        settings = RuntimeSettings.from_environment({**os.environ, "DATABASE_URL": "postgresql://host/db"})
        assert settings.port == 9001
        assert settings.bot_id == "file-bot"
        assert settings.enable_auto_migration is False


@pytest.mark.parametrize("flag,expected", [("true", True), ("false", False)])
def test_explicit_migration_flag_overrides_central_default(flag, expected):
    settings = RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://host/db", "ENABLE_AUTO_MIGRATION": flag})
    assert settings.enable_auto_migration is expected


def test_env_file_precedes_legacy_alias(tmp_path):
    file = tmp_path / "selected.env"
    file.write_text("PORT=9000\n")
    with patch.dict(os.environ, {"ENV_FILE": str(file), "HBOT_ENV_FILE": str(tmp_path / "missing")}, clear=True):
        load_environment()
        assert os.environ["PORT"] == "9000"


@pytest.mark.parametrize("values", [{"ENV_FILE": ""}, {"ENV_FILE": "/missing/test.env"},
                                    {"K_SERVICE": "test", "ENV_FILE": "/any/test.env"}])
def test_invalid_or_cloud_env_file_is_rejected(values):
    with patch.dict(os.environ, values, clear=True):
        with pytest.raises(RuntimeConfigurationError, match="ENV_FILE"):
            load_environment()


@pytest.mark.parametrize('value,expected', [(None, 5), ('5', 5), ('10', 10), (' 50 ', 50)])
def test_database_pool_size_reaches_engine_with_no_overflow(value, expected):
    from hummingbot.model.db_migration.postgres.connection import create_postgres_engine

    values = {'DATABASE_URL': 'postgresql://host/db'}
    if value is not None:
        values['DB_POOL_SIZE'] = value
    settings = RuntimeSettings.from_environment(values)
    assert settings.database_pool_size == expected
    with patch('hummingbot.model.db_migration.postgres.connection.create_engine') as create:
        create_postgres_engine(settings)
    assert create.call_args.kwargs['pool_size'] == expected
    assert create.call_args.kwargs['max_overflow'] == 0


@pytest.mark.parametrize('value', ['', '0', '4', '-1', '51', '1.5', 'nan', 'secret-value'])
def test_invalid_database_pool_size_fails_without_echoing_input(value):
    with pytest.raises(RuntimeConfigurationError, match='DB_POOL_SIZE') as error:
        RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://host/db', 'DB_POOL_SIZE': value})
    assert 'secret-value' not in str(error.value)


def test_database_pool_size_follows_dotenv_process_precedence(tmp_path):
    file = tmp_path / 'pool.env'
    file.write_text('DB_POOL_SIZE=10\n')
    with patch.dict(os.environ, {'ENV_FILE': str(file), 'DB_POOL_SIZE': '7'}, clear=True):
        load_environment()
        assert RuntimeSettings.from_environment({**os.environ, 'DATABASE_URL': 'postgresql://host/db'}).database_pool_size == 7
