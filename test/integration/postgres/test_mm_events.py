"""Events share existing control/session transactions; no second session engine."""
import asyncio
import random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_session import MMSessionRequest
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.mm_event import MMEvent, session_events


@pytest.fixture
def history(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    return store, AdminStore(store), asyncio.run(MMProfilesConfig.load(CATALOGUE))


def start(history, *, selection='fixed'):
    store, admin, catalogue = history
    request = MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=600) if selection == 'fixed' else MMSessionRequest(
        market_mode='spot', selection='random', profile_ids=['sideways'], step_duration_seconds=60, duration_seconds=600)
    payload = dict(action='start', expected_version=0, **request.model_dump(exclude_none=True))
    receipt = admin.command('start', payload, catalogue=catalogue, request=request, principal='operator')
    return receipt, payload, request


def test_atomic_receipt_rollback_retry_and_snapshot_survives_catalogue_edits(history):
    store, admin, catalogue = history
    with patch.object(admin, '_insert_command', side_effect=ValueError('receipt fails')):
        with pytest.raises(ValueError):
            start(history)
    assert store.read()['control_version'] == 0
    with store.engine.connect() as connection:
        assert not connection.execute(select(MMEvent.id)).first()
    receipt, payload, request = start(history)
    assert admin.command('start', payload, catalogue=catalogue, request=request, principal='another')['id'] == receipt['id']
    catalogue.markets['spot'].total_amount_quote = 999
    result = session_events(store, receipt['session_id'])
    assert result['history_complete'] and len(result['items']) == 1
    created = result['items'][0]
    assert created['principal'] == 'operator' and created['source'] == 'admin_api'
    assert created['payload']['configuration']['catalogue']['markets']['spot']['total_amount_quote'] == '100'


def test_random_repeated_draw_activation_expiry_and_takeover_are_not_duplicated(history):
    store, _, _ = history
    receipt, _, _ = start(history, selection='random')
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    identity = receipt['session_id']
    anchors = {'life_spot': dict(life='.1', sources={})}

    def change(fn, generation=0):
        return store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                                       session_id=identity, generation=generation, change=fn)
    try:
        current = change(lambda session, now: session.ready(now=now, anchors=anchors))
        change(lambda session, now: session.ready(now=now, anchors=anchors))

        def rotate(session, now):
            session.advance(now=session.started_at + 61, rng=random.Random(1))
        current = change(rotate)
        assert current.profile_id == 'sideways' and current.generation == 1
        result = session_events(store, identity)
        assert [row['event_type'] for row in reversed(result['items'])] == ['session_created', 'step_started', 'step_changed']
        assert result['items'][0]['old_profile'] == 'sideways' == result['items'][0]['new_profile']
        owner.release()
        replacement = BotOwnership(store.engine, store.bot_id)
        assert replacement.try_acquire()
        replacement.release()
        assert len(session_events(store, identity)['items']) == 3
    finally:
        owner.release()


def test_cursor_filter_binding_bounded_reads_and_missing_legacy_coverage(history):
    store, _, _ = history
    receipt, _, _ = start(history)
    result = session_events(store, receipt['session_id'], limit=1)
    assert result['next_cursor'] is None
    assert not session_events(store, 'legacy-session')['history_complete']
    with pytest.raises(ValueError):
        session_events(store, receipt['session_id'], limit=501)
    with pytest.raises(ValueError):
        session_events(store, receipt['session_id'], cursor='invalid')
    queries = []

    def count(*args):
        queries.append(1)
    event.listen(store.engine, 'before_cursor_execute', count)
    try:
        session_events(store, receipt['session_id'])
    finally:
        event.remove(store.engine, 'before_cursor_execute', count)
    assert len(queries) == 2


def test_guard_transitions_publish_atomically_and_do_not_generate_tick_events(history):
    store, _, _ = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    try:
        session = store.read()['mm_session']
        status = dict(mm_session={key: value for key, value in session.items() if key != 'catalogue'},
                      mm_markets=[dict(market='spot', ready=False, reason='market_price_unavailable', quote_guard=None)])
        for _ in range(4):
            assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=dict(status), save_balances=False)
        result = session_events(store, session['id'], event_type='guard_changed')
        assert len(result['items']) == 1
        status['mm_markets'][0].update(ready=True, reason=None)
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status, save_balances=False)
        page = session_events(store, session['id'], limit=1, event_type='guard_changed')
        assert page['next_cursor']
        second = session_events(store, session['id'], limit=1, event_type='guard_changed', cursor=page['next_cursor'])
        assert len(second['items']) == 1 and second['items'][0]['id'] != page['items'][0]['id']
        with pytest.raises(ValueError):
            session_events(store, 'other', cursor=page['next_cursor'], event_type='guard_changed')
        with pytest.raises(ValueError):
            session_events(store, session['id'], cursor=page['next_cursor'], event_type='step_started')
        assert not store.publish(owner_uuid='stale', owner_generation=owner.generation, version=1, status=status, save_balances=False)
        assert len(session_events(store, session['id'], event_type='guard_changed')['items']) == 2
    finally:
        owner.release()


def test_event_insert_failure_rolls_back_owner_transition_and_migration_replay_keeps_history(history):
    store, _, _ = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    try:
        before = store.read()['mm_session']
        with patch('hummingbot.model.mm_event.append_event', side_effect=ValueError('event unavailable')):
            with pytest.raises(ValueError):
                store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                                        session_id=receipt['session_id'], generation=0,
                                        change=lambda session, now: session.ready(now=now, anchors={'life_spot': dict(life='.1', sources={})}))
        assert store.read()['mm_session'] == before
        bootstrap_postgres(store.engine, auto_migrate=True)
        assert len(session_events(store, receipt['session_id'])['items']) == 1
    finally:
        owner.release()


@pytest.mark.asyncio
async def test_history_and_liquidity_http_are_authenticated_bounded_and_use_only_db(history):
    from types import SimpleNamespace

    from aiohttp import BasicAuth, web
    from aiohttp.test_utils import TestClient, TestServer

    from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
    from hummingbot.client.config.admin_config import AdminSettings
    store, admin, catalogue = history
    receipt, _, _ = start(history)
    settings = AdminSettings(True, 'operator', 'offline-admin')
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=store), settings, catalogue=catalogue).register(app)
    async with TestClient(TestServer(app)) as client:
        path = ADMIN_PREFIX + '/sessions/' + receipt['session_id'] + '/events'
        assert (await client.get(path)).status == 401
        auth = {'Authorization': BasicAuth('operator', 'offline-admin').encode()}
        response = await client.get(path, headers=auth)
        body = await response.json()
        assert response.status == 200 and body['items'][0]['created_at'].endswith('Z')
        assert (await client.get(path + '?limit=501', headers=auth)).status == 422
        response = await client.get(ADMIN_PREFIX + '/statistics/liquidity', headers=auth)
        assert response.status == 200 and (await response.json())['state'] == 'unavailable'


def test_stale_observer_snapshot_preserves_durable_signature_without_a_false_event(history):
    from hummingbot.model.mm_event import record_guards
    store, _, _ = history
    receipt, _, _ = start(history)
    session = store.read()['mm_session']
    previous = {'mm_guard_observation': dict(sequence=4, identity=[session['id'], session['generation']], markets={})}
    stale = dict(mm_session={**session, 'generation': 99}, mm_markets=[dict(market='spot', ready=True)])
    with store.engine.begin() as connection:
        record_guards(connection, store.bot_id, session, previous, stale, 1)
    assert stale['mm_guard_observation'] == previous['mm_guard_observation']
    assert not session_events(store, receipt['session_id'], event_type='guard_changed')['items']


def test_malformed_optional_guard_observation_does_not_rollback_heartbeat_and_recovers(history):
    store, _, _ = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    try:
        session = store.read()['mm_session']
        status = dict(mm_session=session, mm_markets=[None])
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status, save_balances=False)
        assert store.read()['status']['mm_history_error'] == 'guard_history_unavailable'
        status['mm_markets'] = [dict(market='spot', ready=True)]
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status, save_balances=False)
        assert not store.read()['status'].get('mm_history_error')
        assert len(session_events(store, receipt['session_id'], event_type='guard_changed')['items']) == 1
    finally:
        owner.release()


def test_large_page_has_constant_query_count(history):
    from sqlalchemy import text
    store, _, _ = history
    receipt, _, _ = start(history)
    with store.engine.begin() as connection:
        connection.execute(text("""INSERT INTO mm_events
            (bot_id,session_id,event_key,event_type,source,generation,control_version,payload,created_at)
            SELECT :bot,:session,'bulk-'||n,'guard_changed','observer',0,1,'{}'::jsonb,clock_timestamp()
            FROM generate_series(1,1000) n"""), {'bot': store.bot_id, 'session': receipt['session_id']})
    counts = []

    def count(*args):
        counts.append(1)
    event.listen(store.engine, 'before_cursor_execute', count)
    try:
        page = session_events(store, receipt['session_id'], limit=500)
    finally:
        event.remove(store.engine, 'before_cursor_execute', count)
    assert len(page['items']) == 500 and len(counts) == 2 and page['next_cursor']
    with store.engine.begin() as connection:
        connection.execute(text("ANALYZE mm_events"))
        plan = connection.execute(text("""EXPLAIN (FORMAT JSON) SELECT id FROM mm_events
            WHERE bot_id=:bot AND session_id=:session AND event_type='session_created' ORDER BY id DESC LIMIT 1"""),
                                  {'bot': store.bot_id, 'session': receipt['session_id']}).scalar()[0]['Plan']

    def indexed(node):
        return 'event_type' in node.get('Index Cond', '') or any(indexed(child) for child in node.get('Plans', []))
    assert indexed(plan)


@pytest.mark.asyncio
async def test_live_liquidity_http_uses_published_snapshot_and_marks_owner_gap_unknown(history):
    import json
    import time
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import order
    from types import SimpleNamespace

    from aiohttp import BasicAuth, web
    from aiohttp.test_utils import TestClient, TestServer
    from sqlalchemy import text

    from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
    from hummingbot.client.config.admin_config import AdminSettings
    from hummingbot.core.data_type.common import TradeType
    from hummingbot.core.mm_liquidity import LiquidityObserver, own_quotes
    store, _, catalogue = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    session = store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                                      session_id=receipt['session_id'], generation=0,
                                      change=lambda session, now: session.ready(now=now, anchors={'life_spot': dict(life='.1', sources={})}))
    now = time.time()
    market = SimpleNamespace(in_flight_orders={
        'bid': order(identity='bid', price='.099', amount='10'),
        'ask': order(identity='ask', side=TradeType.SELL, price='.101', amount='10')})
    quotes = dict(own_quotes(market, 'LIFE-USDT', max_orders=20), valid=True, eligible=True, reason_code=None, connector='okx', pair='LIFE-USDT')
    frame = LiquidityObserver().observe(dict(session_id=session.id, generation=0, profile_id='sideways', started_at=session.started_at),
                                        {'spot': quotes}, now=now, monotonic_now=0, owner_generation=owner.generation)
    status = json.loads(json.dumps(dict(mm_session=session.model_dump(mode='json'), mm_liquidity=frame), default=str))
    assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status, save_balances=False)
    settings = AdminSettings(True, 'operator', 'offline-admin')
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=store), settings, catalogue=catalogue).register(app)
    try:
        async with TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'offline-admin').encode()}) as client:
            for _ in range(10):
                response = await client.get(ADMIN_PREFIX + '/statistics/liquidity?market=spot')
                body = await response.json()
                assert response.status == 200 and body['state'] == 'observed' and body['markets']['spot']['bid_notional'] == '0.990'
            with store.engine.begin() as connection:
                connection.execute(text("UPDATE bot_state SET heartbeat=clock_timestamp()-interval '10 seconds'"))
            response = await client.get(ADMIN_PREFIX + '/statistics/liquidity')
            body = await response.json()
            assert body['state'] == 'unavailable' and body['markets']['spot']['bid_notional'] is None
    finally:
        owner.release()


def test_quiet_publication_does_not_open_an_optional_event_savepoint(history):
    store, _, _ = history
    start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    status = dict(mm_session=store.read()['mm_session'], mm_markets=[dict(market='spot', ready=True)])
    assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                         version=1, status=status, save_balances=False)
    statements = []

    def capture(connection, cursor, statement, *args):
        statements.append(statement)

    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                             version=1, status=dict(status), save_balances=False)
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)
        owner.release()
    assert not any(statement.startswith('SAVEPOINT') for statement in statements)


def test_guard_reason_is_recorded_after_controllers_have_stopped(history):
    store, _, _ = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    try:
        status = dict(mm_session=store.read()['mm_session'], phase='halted',
                      availability=dict(reason_code='recovery_required'), mm_markets=[])
        for _ in range(3):
            assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                                 version=1, status=dict(status), save_balances=False)
        events = session_events(store, receipt['session_id'], event_type='guard_changed')['items']
        assert len(events) == 1 and events[0]['payload']['market'] == 'all'
        assert events[0]['payload']['current']['runtime_reason'] == 'recovery_required'
    finally:
        owner.release()
