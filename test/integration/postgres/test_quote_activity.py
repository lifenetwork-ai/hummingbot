import json
from test.hummingbot.cli.test_admin_quote_activity import quote_state
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event, select, update

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.mark.asyncio
async def test_persisted_quote_warning_is_read_only_and_stale_owner_revokes_it(pg_engine, monkeypatch):
    from datetime import datetime, timezone

    catalogue, row = await quote_state()
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    # Rebase fixture timestamps into the DB clock so the public reader's freshness
    # checks, rather than mocked timestamps, decide whether the warning is current.
    with pg_engine.begin() as connection:
        from sqlalchemy import text
        now = float(connection.execute(text('SELECT EXTRACT(EPOCH FROM clock_timestamp())')).scalar())
        frame = row['status']['mm_liquidity']
        frame['observed_at'] = now
        frame['started_at'] = frame['coverage_started_at'] = now - 6
        row['mm_session'].update(started_at=now - 6, expires_at=now + 594, step_expires_at=now + 594)
        frame['markets']['spot']['quote_activity']['proposal_observed_at'] = now
        row['status']['availability'] = dict(observed_at=now, state_source='postgres', persisted_state_available=True,
                                             phase='running', incidents=[])
        connection.execute(update(BotState).where(BotState.bot_id == 'hummingbot').values(
            desired_state='running', owner_uuid='owner', heartbeat=datetime.now(timezone.utc),
            mm_session=row['mm_session'], status=json.loads(json.dumps(row['status'], default=str))))
    settings = AdminSettings(True, 'operator', 'test-password')
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=store, revision='test'), settings, catalogue=catalogue).register(app)
    with pg_engine.connect() as connection:
        before = connection.execute(select(BotState)).mappings().one()
    async with TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'test-password').encode()}) as client:
        for path in ('/status', '/statistics/liquidity'):
            statements = []

            def count(*args):
                statements.append(1)
            event.listen(pg_engine, 'before_cursor_execute', count)
            try:
                response = await client.get(ADMIN_PREFIX + path)
            finally:
                event.remove(pg_engine, 'before_cursor_execute', count)
            assert len(statements) == 1
            assert response.status == 200
            body = await response.json()
            activity = body['quote_activity']['spot'] if path == '/status' else body['markets']['spot']['quote_activity']
            assert activity['warning_active'] and activity['no_quote_seconds'] == 6
            assert activity['proposal_observed_at'].endswith('Z')
        with pg_engine.connect() as connection:
            after = connection.execute(select(BotState)).mappings().one()
        assert dict(before) == dict(after)
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(heartbeat=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        body = await (await client.get(ADMIN_PREFIX + '/status')).json()
        assert body['quote_activity']['spot']['state'] == 'unavailable'
        assert not body['quote_activity']['spot']['warning_active']
