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
    assert settings.enable_auto_migration is True
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
