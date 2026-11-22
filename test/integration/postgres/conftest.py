"""Explicit integration job: absence of a test DB is a failure, never a skip."""
import os
from argparse import ArgumentTypeError

import pytest
from sqlalchemy import text

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine


def _parse_shard(value):
    try:
        index, count = map(int, value.split('/'))
    except ValueError:
        raise ArgumentTypeError('Expected a PostgreSQL shard in INDEX/COUNT format, e.g. 1/4.') from None
    if not 1 <= index <= count:
        raise ArgumentTypeError('PostgreSQL shard index must be between 1 and the shard count.')
    return index, count


def pytest_addoption(parser):
    parser.addoption('--postgres-shard', type=_parse_shard, metavar='INDEX/COUNT',
                     help='Run one shard of the PostgreSQL integration tests.')


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    shard = config.getoption('--postgres-shard')
    if shard is None:
        return
    index, count = shard
    # Distribute parameterized cases within each file across shards while
    # preserving the collected execution order on each isolated database.
    ordered = sorted(items, key=lambda item: item.nodeid)
    selected = set(ordered[index - 1::count])
    removed = [item for item in items if item not in selected]
    items[:] = [item for item in items if item in selected]
    config.hook.pytest_deselected(items=removed)


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
