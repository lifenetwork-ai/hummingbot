"""Real server cancellation must roll back before a complete reporting retry."""
import asyncio
import json
from decimal import Decimal
from test.integration.postgres.test_admin_daily import add_fill, millis
from types import SimpleNamespace

import pytest
from multidict import MultiDict
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.mark.parametrize('persistent', [False, True])
@pytest.mark.asyncio
async def test_daily_server_timeout_retries_complete_snapshot_without_extending_deadlines(pg_engine, persistent):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, 'hummingbot')
    state.ensure()
    store = AdminStore(state)
    timestamp = millis('2026-01-01T12:00:00+00:00')
    add_fill(pg_engine, 'first', 'okx', timestamp, '1.25', Decimal('1.25'))
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC', statistics_cache_ttl_seconds=.01), store=store)
    request = SimpleNamespace(query=MultiDict({'from': '2026-01-01', 'to': '2026-01-02', 'market': 'spot'}))
    attempts, failures, transactions = [], [], []
    injecting = [False]
    original = store.daily

    def aggregate(**filters):
        attempts.append(True)
        if len(attempts) == 2 and not persistent:
            # The failed attempt's main aggregate saw only the first fill.
            # A complete retry must include this late fill in counts AND fees.
            assert pg_engine.pool.checkedout() == 0
            add_fill(pg_engine, 'late', 'okx', timestamp, '.25', Decimal('.25'))
        try:
            return original(**filters)
        except DBAPIError as error:
            failures.append((error.orig.pgcode, error.orig.diag.message_primary, error.connection_invalidated))
            raise

    def fault(connection, cursor, statement, params, *args):
        if 'AS currency' not in statement or not injecting[0]:
            return
        assert connection.exec_driver_sql('SHOW statement_timeout').scalar_one() == '2s'
        assert connection.connection.driver_connection.io_timeout == 3
        transactions.append(connection.exec_driver_sql('SELECT txid_current()').scalar_one())
        if persistent or not failures:
            connection.exec_driver_sql('SELECT pg_sleep(3)')

    store.daily = aggregate
    event.listen(pg_engine, 'before_cursor_execute', fault)
    try:
        warm = json.loads((await api.daily(request)).text) if persistent else None
        attempts.clear()
        await asyncio.sleep(.02)
        injecting[0] = True
        body = json.loads((await api.daily(request)).text)
        assert len(attempts) == 2
        assert len(transactions) == 2 and transactions[0] != transactions[1]
        assert all(row == ('57014', 'canceling statement due to statement timeout', False) for row in failures)
        if persistent:
            assert len(failures) == 2
            assert body['freshness'] == 'stale' and body['server_time'] == warm['server_time']
            await api.daily(request)
            assert len(attempts) == 2  # Existing five-second stale cooldown.
        else:
            assert len(failures) == 1 and body['freshness'] == 'fresh'
            day = body['markets'][0]['days'][0]
            assert day['fill_count'] == 2
            assert Decimal(day['fees'][0]['paid']) == Decimal('1.5')
            assert Decimal(day['fee_in_usdt']) == Decimal('1.5')
        assert not api._daily_flights and api._database_executor.pending == 0
    finally:
        event.remove(pg_engine, 'before_cursor_execute', fault)
        await api.close(None)
    assert pg_engine.pool.checkedout() == 0
    with pg_engine.connect() as connection:
        assert connection.exec_driver_sql('SHOW statement_timeout').scalar_one() == '2s'
        assert connection.exec_driver_sql('SELECT 1').scalar_one() == 1
