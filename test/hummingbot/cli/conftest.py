"""CLI tests must not load a developer's bot credentials or deployment settings."""
import os

import pytest

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG


@pytest.fixture(autouse=True)
def isolated_cli_environment(tmp_path_factory, monkeypatch):
    for key in set(DEFAULT_ENV_CONFIG) | {'DATABASE_URL', 'K_SERVICE', 'K_REVISION', 'HBOT_ENV_FILE'}:
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith('HBOT_OKX'):
            monkeypatch.delenv(key, raising=False)
    # Keep the harness file outside the test's working directory: shutdown
    # contracts deliberately assert that the runtime creates no local files.
    empty = tmp_path_factory.mktemp('cli-environment') / 'isolated-cli.env'
    empty.write_text('')
    # Tests exercising dotenv behavior may explicitly replace this selected file.
    monkeypatch.setenv('ENV_FILE', str(empty))
