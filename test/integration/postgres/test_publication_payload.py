"""The reporting DB boundary must not transfer private checkpoint histories."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session

from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.mm_event import MMEvent

owned = _owned


def large_status():
    return dict(phase='running', mm_overview=dict(observed_at=123, markets=[]),
                mm_liquidity=dict(session_id='session', generation=7, markets={},
                                  _coverage_history={'spot': []},
                                  _coverage_buckets={market: {
                                      str(second): [second, second + 1, 1., 1., 1., 1., 0., 0., 0., 0., 0.]
                                      for second in range(1800)} for market in ('spot', 'perpetual')},
                                  _overview_recovery={'markets': {market: {'recovered': [
                                      dict(key=f'{market}-{index}-' + 'x' * 64, timestamp=index, seconds=1.)
                                      for index in range(2000)]} for market in ('spot', 'perpetual')}},
                                  extra={'retained': True}), reporting={'accounts': []})


def prepare(engine, status):
    bootstrap_postgres(engine, auto_migrate=True)
    store = BotStateStore(engine, 'hummingbot')
    store.ensure()
    with engine.begin() as connection:
        connection.execute(update(BotState).values(status=status))
    return store


def test_service_publication_worker_retains_full_restart_checkpoint(owned):
    from unittest.mock import MagicMock

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings

    status = large_status()
    status['mm_liquidity']['markets'] = {'spot': {'best_bid': Decimal('123.4500')}}
    expected = json.loads(json.dumps(status, default=str))
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.owner = owned.owner
    host.snapshot = lambda: status
    owned.loop.run_until_complete(host.publish_status(1))

    stored = owned.store.read()
    assert stored['acknowledged_version'] == 1
    assert stored['status']['mm_liquidity'] == expected['mm_liquidity']
    assert stored['status']['mm_overview'] == expected['mm_overview']
    # Normalization/DB metadata cannot mutate the engine's resident snapshot.
    assert status['mm_liquidity']['markets']['spot']['best_bid'] == Decimal('123.4500')
    assert 'mm_guard_observation' not in status


def test_publication_excludes_private_history_before_driver_json_decode(pg_engine):
    status = large_status()
    assert len(json.dumps(status)) > 500_000
    store = prepare(pg_engine, status)
    decoded_bytes, statements = [], []

    def decode(raw):
        decoded_bytes.append(len(raw.encode()))
        return json.loads(raw)

    # This observes the actual DBAPI JSON decoder, before Python projection or
    # HTTP filtering can hide the bytes transferred through the hot read lane.
    reader = create_engine(pg_engine.url, json_deserializer=decode)
    event.listen(reader, 'before_cursor_execute', lambda *args: statements.append(args[2]))
    try:
        row = BotStateStore(reader, 'hummingbot').read(publication=True)
    finally:
        reader.dispose()
    assert len(statements) == 1
    assert decoded_bytes and max(decoded_bytes) < 64_000
    expected = large_status()
    for key in ('_coverage_history', '_coverage_buckets', '_overview_recovery'):
        expected['mm_liquidity'].pop(key)
    assert row['status'] == expected
    # The financial/owner read still receives the exact full checkpoint, and a
    # publication read does not rewrite or truncate durable restart evidence.
    assert store.read()['status'] == status


@pytest.mark.parametrize('liquidity', [None, 'legacy', [], [1], {}, {'public': 3}])
def test_publication_preserves_missing_and_non_object_checkpoints(pg_engine, liquidity):
    status = dict(phase='stopped', mm_liquidity=liquidity, other={'_coverage_buckets': 'unrelated'})
    store = prepare(pg_engine, status)
    assert store.read(publication=True)['status'] == status
    assert store.read()['status'] == status


def test_publication_preserves_status_without_liquidity(pg_engine):
    status = dict(phase='idle', unrelated={'_overview_recovery': 'retain'})
    store = prepare(pg_engine, status)
    assert store.read(publication=True)['status'] == status
    assert store.read()['status'] == status


@pytest.mark.parametrize('writers', [1, 2])
def test_guard_writeback_does_not_decode_private_previous_checkpoint(owned, writers):
    session = dict(id='session', generation=7, profile_id='follow_market')
    public = dict(phase='running', mm_session=dict(id='session', generation=7),
                  mm_markets=[dict(market='spot', ready=True, reason=None)])
    args = dict(owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                version=1, save_balances=False)
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).values(mm_session=session))
    assert owned.store.publish(**args, status=deepcopy(public))
    guard = owned.store.read()['status']['mm_guard_observation']
    old = large_status()
    old.update(public)
    old['mm_guard_observation'] = guard
    assert len(json.dumps(old)) > 500_000
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).values(status=old))

    def events():
        with owned.engine.connect() as connection:
            return list(connection.execute(select(MMEvent.payload).where(
                MMEvent.event_type == 'guard_changed').order_by(MMEvent.id)).scalars())

    before = events()
    new = deepcopy(old)
    new['mm_markets'][0].update(ready=False, reason='inventory_limit')
    # The database observation must win over missing/stale incoming metadata.
    new.pop('mm_guard_observation')
    decoded_bytes = []

    def decode(raw):
        decoded_bytes.append(len(raw.encode()))
        return json.loads(raw)

    reader = create_engine(owned.engine.url, json_deserializer=decode)
    try:
        with ThreadPoolExecutor(max_workers=writers) as pool:
            futures = [pool.submit(BotStateStore(reader, 'hummingbot').publish,
                                   **args, status=deepcopy(new)) for _ in range(writers)]
            published = [future.result(timeout=10) for future in futures]
            assert any(published)
            # Concurrent optimistic publications may lose their final basis or
            # yield to a locked row. Retry the skipped observation after both
            # transactions finish; duplicate guard history must still be absent.
            for committed in published:
                if not committed:
                    assert BotStateStore(reader, 'hummingbot').publish(**args, status=deepcopy(new))
        after = events()
        assert len(after) == len(before) + 1
        assert after[-1]['market'] == 'spot'
        assert after[-1]['previous']['ready'] is True
        assert after[-1]['current']['ready'] is False
        assert after[-1]['current']['reason'] == 'inventory_limit'
        stored = owned.store.read()['status']
        assert stored['mm_guard_observation']['sequence'] == guard['sequence'] + 1
        assert stored['mm_liquidity'] == new['mm_liquidity']
        repeated = deepcopy(new)
        repeated['mm_guard_observation'] = {'sequence': 99999}
        assert BotStateStore(reader, 'hummingbot').publish(**args, status=repeated)
        assert len(events()) == len(after)
        assert repeated['mm_guard_observation'] == stored['mm_guard_observation']
        assert owned.store.read()['status']['mm_liquidity'] == old['mm_liquidity']
        assert decoded_bytes and max(decoded_bytes) < 64_000
    finally:
        reader.dispose()


@pytest.mark.parametrize('stale', ['owner', 'generation', 'version'])
def test_guard_writeback_projection_retains_authority_fences(owned, stale):
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).values(status=large_status()))
    args = dict(owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                version=1, save_balances=False)
    if stale == 'owner':
        args['owner_uuid'] += '-stale'
    elif stale == 'generation':
        args['owner_generation'] += 1
    else:
        args['version'] += 1
    with owned.engine.connect() as connection:
        before = dict(connection.execute(select(BotState.__table__)).mappings().one())
    assert owned.store.publish(**args, status={'phase': 'running'}) is False
    with owned.engine.connect() as connection:
        after = dict(connection.execute(select(BotState.__table__)).mappings().one())
    assert after == before


@pytest.mark.parametrize('concurrent_action', ['financial_fence', 'stop'])
def test_large_publication_encoding_does_not_hold_trading_authority_lock(owned, concurrent_action):
    entered, release = threading.Event(), threading.Event()
    previous_status = owned.store.read()['status']
    status = large_status()
    status['encoding_witness'] = True

    def encode(value):
        if isinstance(value, dict) and value.get('encoding_witness'):
            entered.set()
            assert release.wait(5), 'Publication encoding was not released'
        return json.dumps(value)

    writer = create_engine(owned.engine.url, json_serializer=encode)
    args = dict(owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                version=1, status=status, save_balances=False)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(BotStateStore(writer, 'hummingbot').publish, **args)
            try:
                assert entered.wait(5)
                # This is the actual financial transaction's ownership fence,
                # not an unrelated-row write or a mock of lock acquisition.
                if concurrent_action == 'financial_fence':
                    with Session(owned.engine) as session, session.begin():
                        owned.owner.fence_session(session)
                else:
                    assert owned.store.control(expected_version=1, desired_state='stopped') == 2
            finally:
                release.set()
            assert future.result(timeout=10) is (concurrent_action == 'financial_fence')
        stored = owned.store.read()
        if concurrent_action == 'financial_fence':
            assert stored['status']['mm_liquidity'] == status['mm_liquidity']
            assert stored['acknowledged_version'] == 1
        else:
            assert stored['control_version'] == 2 and stored['acknowledged_version'] == 0
            assert stored['status'] == previous_status
    finally:
        writer.dispose()
