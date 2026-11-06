"""Cleanup snapshot payload measurements; latency is evidence, never a gate."""
import json
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture
from test.integration.postgres.test_disconnect_coordinator import Runtime
from test.integration.postgres.test_performance import measure, queries, report
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.exc import OperationalError

from hummingbot.cli.credential_disconnect import DisconnectCoordinator
from hummingbot.model.bot_state import BotState
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order

managed = managed_fixture


def large_tracking_snapshot(managed):
    store, actor = loaded(managed)
    root = disconnect(store, actor)
    saved = {'order-' + str(i): {'payload': 'x' * 4096} for i in range(256)}
    with store.engine.begin() as connection:
        connection.execute(Order.__table__.insert().values(
            id='tracked', config_file_path='large.yml', strategy='test', market='okx', symbol='BTC-USDT',
            base_asset='BTC', quote_asset='USDT', creation_timestamp=1, order_type='LIMIT', amount=1, price=100,
            last_status='BuyOrderCreated', last_update_timestamp=1, creation_pending=False))
        connection.execute(MarketState.__table__.insert().values(
            config_file_path='large.yml', market='okx', timestamp=1, saved_state=saved))
    return store, root


def test_cleanup_full_snapshot_tracking_baseline(managed):
    store, root = large_tracking_snapshot(managed)

    def read():
        return store.disconnect_snapshot(root['id'], 'owner', 1)

    with queries(store.engine) as statements:
        snapshot = read()
    payload_bytes = len(json.dumps(snapshot['tracking_states']).encode())
    assert len(statements) == 5 and payload_bytes > 1024 * 1024
    assert any('FOR UPDATE' in query and 'bot_state' in query for query in statements)
    report('cleanup_full_tracking_snapshot', dict(**measure(read), queries=len(statements), tracking_bytes=payload_bytes,
                                                  order_rows=len(snapshot['orders']), market_state_rows=len(snapshot['tracking_states'])))


def test_cleanup_snapshot_can_omit_unused_tracking_json_without_changing_authority_or_ledger(managed):
    store, root = large_tracking_snapshot(managed)
    full = store.disconnect_snapshot(root['id'], 'owner', 1)

    def read():
        return store.disconnect_snapshot(root['id'], 'owner', 1, include_tracking_states=False)

    with queries(store.engine) as statements:
        projected = read()
    assert len(statements) == 4 and not any('market_states' in sql for sql in statements)
    assert any('FOR UPDATE' in sql and 'bot_state' in sql for sql in statements)
    assert projected['tracking_states'] == []
    assert {key: value for key, value in projected.items() if key not in ('state', 'tracking_states')} == {
        key: value for key, value in full.items() if key not in ('state', 'tracking_states')}
    assert {key: value for key, value in projected['state'].items() if key != 'database_now'} == {
        key: value for key, value in full['state'].items() if key != 'database_now'}
    report('cleanup_projected_tracking_snapshot', dict(**measure(read), queries=len(statements), tracking_bytes=0,
                                                       order_rows=len(projected['orders']), market_state_rows=0))


@pytest.mark.parametrize('include_tracking_states', [True, False], ids=['full', 'projected'])
@pytest.mark.parametrize('change', ['owner', 'control', 'desired_state', 'operation'])
def test_cleanup_snapshot_projection_keeps_all_authority_fences(managed, include_tracking_states, change):
    store, root = large_tracking_snapshot(managed)
    values = {'owner': dict(owner_uuid='replacement', owner_generation=2), 'control': dict(control_version=3),
              'desired_state': dict(desired_state='running'), 'operation': dict(recovery_state={})}[change]
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(**values))
    with queries(store.engine) as statements:
        result = store.disconnect_snapshot(root['id'], 'owner', 1, include_tracking_states=include_tracking_states)
    assert result is None and not any('market_states' in sql for sql in statements)


@pytest.mark.parametrize('include_tracking_states', [True, False], ids=['full', 'projected'])
def test_cleanup_snapshot_projection_holds_control_lock_through_ledger_read(managed, include_tracking_states):
    store, root = large_tracking_snapshot(managed)
    entered, release = Event(), Event()

    def pause_ledger_read(connection, cursor, statement, *args):
        if 'FROM positions' in statement:
            entered.set()
            assert release.wait(10), 'Controlled ledger-read barrier was not released'

    event.listen(store.engine, 'before_cursor_execute', pause_ledger_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(store.disconnect_snapshot, root['id'], 'owner', 1,
                                   include_tracking_states=include_tracking_states)
            try:
                assert entered.wait(5), 'Snapshot did not reach ledger read'
                with pytest.raises(OperationalError) as error:
                    with store.engine.begin() as other:
                        other.execute(text('SELECT bot_id FROM bot_state FOR UPDATE NOWAIT'))
                assert error.value.orig.pgcode == '55P03'
            finally:
                release.set()
            snapshot = future.result(timeout=10)
    finally:
        release.set()
        event.remove(store.engine, 'before_cursor_execute', pause_ledger_read)
    assert snapshot is not None and len(snapshot['orders']) == 1
    with store.engine.begin() as other:
        assert other.execute(text('SELECT bot_id FROM bot_state FOR UPDATE NOWAIT')).scalar_one() == store.bot_id


@pytest.mark.asyncio
async def test_cleanup_coordinator_uses_projected_snapshots_for_progress_and_proofs(managed):
    store, root = large_tracking_snapshot(managed)
    runtime = Runtime(store, {})

    async def cancel():
        with store.engine.begin() as connection:
            connection.execute(update(Order).where(Order.id == 'tracked').values(last_status='OrderCancelled'))

    runtime.cancel_orders = cancel
    with queries(store.engine) as statements:
        assert await DisconnectCoordinator(store, SimpleNamespace(owner_uuid='owner', generation=1), runtime).run(root['id'])
    assert not any('market_states' in sql for sql in statements)
    assert store.operation(root['id'])['status'] == 'succeeded'
