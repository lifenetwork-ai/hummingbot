"""Real reporting SQL cannot occupy the operational/control DB lane.

This verifies HTTP/callback/owner-fence progress, not full exchange quote latency.
The packaged load harness supplies the separate trading-progress qualification.
"""
import asyncio
import json
import threading
import time
from test.hummingbot.cli.test_admin_overview import row as sample_row
from test.hummingbot.cli.test_admin_quote_activity import quote_state
from test.integration.postgres.test_overview_fees import add_attempt, add_fill
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event, func, update

from hummingbot.cli.admin_api import AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.core.overview_recovery import PostFillRecoveryObserver
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state import BotState

owned = _owned


@pytest.mark.asyncio
@pytest.mark.parametrize('delay', [1, 2], ids=['held-query', 'real-statement-timeout'])
async def test_status_stop_and_owner_fence_progress_during_actual_reporting_sql(owned, delay):
    catalogue, source = await quote_state()
    now = time.time()
    # Persist a full rolling checkpoint, so cold JSONB read/restore/serialization
    # is included in the same status/control deadlines as the held SQL case.
    frame = source['status']['mm_liquidity']
    first = int(now) - 1800
    frame.update(started_at=first, coverage_started_at=first, observed_at=now)
    frame['_coverage_history'] = {}
    frame['_coverage_buckets'] = {'spot': {
        str(second): [second, second + 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
        for second in range(first, int(now))}}
    session = {**source['mm_session'], 'catalogue': catalogue.model_dump(mode='json')}
    session['request'] = {**session['request'], 'duration_seconds': 2400}
    session.update(started_at=first, expires_at=first + 2400, step_expires_at=first + 2400)
    recovery = PostFillRecoveryObserver({'markets': {
        market: dict(session_id=session['id'], generation=session['generation'], owner_generation=owned.owner.generation,
                     window_start=first, last_wall=now, last_monotonic=0, last_valid=True, pending=[], history_complete=False,
                     recovered=[dict(key=f'{market}-' + 'x' * 64 + str(index), timestamp=now - index / 1000, seconds=1.)
                                for index in range(2000)])
        for market in ('spot', 'perpetual')}})
    frame['_overview_recovery'] = recovery.checkpoint
    assert all(len(state['recovered']) == 2000 for state in recovery.checkpoint['markets'].values())
    assert len(json.dumps(frame, default=str)) > 500_000
    source['status'].update(mm_session={key: value for key, value in session.items() if key != 'catalogue'})
    supplement = sample_row()['status']
    for account in supplement['reporting']['accounts']:
        for balance in account['balances']:
            balance['observed_at'] = now
    runtime = supplement['mm_overview']
    runtime.update(session_id=session['id'], generation=session['generation'], observed_at=now)
    source['status'].update(reporting=supplement['reporting'], mm_overview=runtime)
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == 'hummingbot').values(
            mm_session=session, status=json.loads(json.dumps(source['status'], default=str)),
            acknowledged_version=1, heartbeat=func.clock_timestamp()))
    identity = add_fill(owned.engine, fee='.25', timestamp=int((now - .5) * 1000))
    add_attempt(owned.engine, identity, session_id=session['id'], account='www:17', environment='demo')
    entered, finished = threading.Event(), threading.Event()

    def slow(connection, cursor, statement, parameters, context, executemany):
        if '/* overview_fee_source_batch */' in statement and not entered.is_set():
            entered.set()
            try:
                connection.exec_driver_sql(f'SELECT pg_sleep({delay})')
            finally:
                finished.set()

    settings = AdminSettings(True, 'operator', 'offline-password', basic_auth_enabled=True)
    app = web.Application(middlewares=[admin_middleware(settings)])
    api = AdminAPI(SimpleNamespace(store=owned.store), settings, store=AdminStore(owned.store), catalogue=catalogue)
    api.register(app)
    ticks, done = [], asyncio.Event()

    async def pulse():
        while not done.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(.01)

    event.listen(owned.engine, 'before_cursor_execute', slow)
    pulse_task = asyncio.create_task(pulse())
    try:
        async with TestClient(TestServer(app), headers={
                'Authorization': BasicAuth('operator', 'offline-password').encode()}) as client:
            assert await asyncio.to_thread(entered.wait, 2)
            start = time.monotonic()
            response = await client.get('/api/v1/admin/status')
            assert response.status == 200, await response.text()
            status = await response.json()
            assert '_overview_recovery' not in await response.text()
            assert '_coverage_buckets' not in await response.text()
            assert status['actions']['stop']['allowed']
            assert time.monotonic() - start < .5
            with owned.manager.get_new_session() as native:
                owned.owner.fence_session(native)
            response = await client.post('/api/v1/admin/commands', headers={'Idempotency-Key': 'stop-during-overview'},
                                         json={'action': 'stop', 'expected_version': 1, 'session_id': session['id']})
            assert response.status == 202, await response.text()
            assert owned.store.read()['desired_state'] == 'stopped'
            assert not finished.is_set(), 'Control waited until the heavy query ended'
            assert len(ticks) >= 5
            assert not owned.owner.lost and owned.manager.persistence_healthy
    finally:
        done.set()
        await pulse_task
        event.remove(owned.engine, 'before_cursor_execute', slow)
