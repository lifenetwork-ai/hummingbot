"""Explicit integration job: absence of a test DB is a failure, never a skip."""
import os

import pytest
from sqlalchemy import text

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine


@pytest.fixture
def pg_engine():
    url = os.environ.get("HBOT_TEST_POSTGRES_URL")
    if not url:
        pytest.fail("HBOT_TEST_POSTGRES_URL is required; run make test-postgres for a disposable database.")
    settings = RuntimeSettings.from_environment({"DATABASE_URL": url})
    if not settings.database_url.database.startswith("hbot_test_"):
        pytest.fail("Refusing destructive integration tests: database name must start with hbot_test_.")
    engine = create_postgres_engine(settings)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    try:
        yield engine
    finally:
        engine.dispose()
