"""Real SQL cancellation degrades daily charts without changing DB deadlines."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from multidict import MultiDict
from sqlalchemy import event
from sqlalchemy.exc import ProgrammingError

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def api_for(engine):
    bootstrap_postgres(engine, auto_migrate=True)
    store = BotStateStore(engine, 'hummingbot')
    store.ensure()
    return AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=.001), store=AdminStore(store))


@pytest.mark.asyncio
async def test_actual_statement_timeout_returns_qualified_snapshot_and_recovers(pg_engine):
    api = api_for(pg_engine)
    request = SimpleNamespace(query=MultiDict())

    def slow_fee(connection, cursor, statement, parameters, context, executemany):
        return ('SELECT pg_sleep(3)', {}) if 'AS currency' in statement else (statement, parameters)

    installed = False
    try:
        warm = json.loads((await api.daily(request)).text)
        await asyncio.sleep(.01)
        event.listen(pg_engine, 'before_cursor_execute', slow_fee, retval=True)
        installed = True
        stale = json.loads((await api.daily(request)).text)
        assert stale['freshness'] == 'stale' and stale['server_time'] == warm['server_time']
        assert stale['markets'] == warm['markets'] and stale['balances'] == warm['balances']
        assert 0 < stale['observation_age_seconds'] < 60
        event.remove(pg_engine, 'before_cursor_execute', slow_fee)
        installed = False
        with pg_engine.connect() as connection:
            assert connection.exec_driver_sql('SHOW statement_timeout').scalar_one() == '2s'
        await asyncio.sleep(5.05)
        recovered = json.loads((await api.daily(request)).text)
        assert recovered['freshness'] == 'fresh' and recovered['server_time'] != warm['server_time']
        assert recovered['message'] is recovered['retry_after_seconds'] is None
    finally:
        if installed:
            event.remove(pg_engine, 'before_cursor_execute', slow_fee)
        await api.close(None)
    assert pg_engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_actual_missing_schema_is_not_hidden_by_a_last_good_chart(pg_engine):
    api = api_for(pg_engine)
    request = SimpleNamespace(query=MultiDict())

    def bad_fee(connection, cursor, statement, parameters, context, executemany):
        return ('SELECT * FROM deliberately_missing_fee_source', {}) if 'AS currency' in statement else (statement, parameters)

    installed = False
    try:
        await api.daily(request)
        await asyncio.sleep(.01)
        event.listen(pg_engine, 'before_cursor_execute', bad_fee, retval=True)
        installed = True
        with pytest.raises(ProgrammingError) as caught:
            await api.daily(request)
        assert caught.value.orig.pgcode == '42P01'
    finally:
        if installed:
            event.remove(pg_engine, 'before_cursor_execute', bad_fee)
        await api.close(None)
    assert pg_engine.pool.checkedout() == 0
