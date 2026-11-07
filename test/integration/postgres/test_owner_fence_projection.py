"""Fresh write authority does not need to deserialize recovery/session payloads."""
import json
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import event, text

from hummingbot.core.bot_ownership import OwnershipLost

owned = _owned


def test_write_fence_reads_identity_without_unused_checkpoint_or_catalogue(owned):
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET recovery_state=CAST(:recovery AS jsonb), "
                                "mm_session=CAST(:session AS jsonb) WHERE bot_id=:bot_id"),
                           {'recovery': json.dumps({'strategy_checkpoints': {'payload': 'x' * 65536}}),
                            'session': json.dumps({'catalogue': {'payload': 'y' * 65536}}),
                            'bot_id': owned.store.bot_id})
    observed = []

    def selected(conn, cursor, statement, *args):
        if conn is owned.owner.connection and 'pg_backend_pid() AS backend_pid' in statement:
            observed.append(dict(columns=[column.name for column in cursor.description], statement=statement,
                                 autocommit=conn.connection.driver_connection.autocommit))

    event.listen(owned.engine, 'after_cursor_execute', selected)
    try:
        with owned.manager.get_new_session() as session, session.begin():
            owned.owner.fence_session(session)
    finally:
        event.remove(owned.engine, 'after_cursor_execute', selected)
    assert len(observed) == 1
    assert observed[0]['columns'] == ['backend_pid']
    assert observed[0]['autocommit'] is True
    assert 'pg_locks' in observed[0]['statement']
    assert 'recovery_state' not in observed[0]['statement']
    assert 'mm_session' not in observed[0]['statement']
    assert not owned.owner.connection.connection.driver_connection.autocommit
    assert owned.owner.check_owner()['recovery_state']['strategy_checkpoints']['payload'] == 'x' * 65536
    assert owned.owner.check_owner()['mm_session']['catalogue']['payload'] == 'y' * 65536


def test_each_write_fence_rechecks_physical_owner_even_after_transaction_is_fenced(owned):
    selects = []

    def selected(conn, cursor, statement, *args):
        if conn is owned.owner.connection and 'pg_backend_pid() AS backend_pid' in statement:
            selects.append(statement)

    event.listen(owned.engine, 'after_cursor_execute', selected)
    try:
        with owned.manager.get_new_session() as session, session.begin():
            owned.owner.fence_session(session)
            owned.owner.fence_session(session)
            assert len(selects) == 2
            # Losing the actual lock must defeat the per-transaction row fence.
            owned.owner.connection.execute(text('SELECT pg_advisory_unlock_all()'))
            owned.owner.connection.commit()
            with pytest.raises(OwnershipLost):
                owned.owner.fence_session(session)
            assert len(selects) == 3
    finally:
        event.remove(owned.engine, 'after_cursor_execute', selected)
    assert owned.owner.lost and not owned.owner.allow_new_orders


@pytest.mark.parametrize('state', ['closed', 'invalidated', 'lost'])
def test_write_fence_rejects_dead_owner_without_opening_a_replacement_session(owned, state):
    connection = owned.owner.connection
    if state == 'closed':
        connection.close()
    elif state == 'invalidated':
        connection.invalidate()
    else:
        owned.owner.lost = True
    connections = []

    def opened(*args):
        connections.append(True)

    event.listen(owned.engine, 'connect', opened)
    try:
        with owned.manager.get_new_session() as session:
            with pytest.raises(OwnershipLost):
                owned.owner.fence_session(session)
    finally:
        event.remove(owned.engine, 'connect', opened)
    assert not connections and owned.owner.lost and not owned.owner.allow_new_orders


def test_identity_query_failure_blocks_actual_recorder_flush_and_preserves_database(owned):
    from hummingbot.model.order import Order
    attempted = []

    def fail(conn, cursor, statement, *args):
        if conn is owned.owner.connection and 'pg_backend_pid() AS backend_pid' in statement:
            attempted.append(True)
            raise OSError('Injected identity read failure')

    event.listen(owned.engine, 'before_cursor_execute', fail)
    try:
        with owned.manager.get_new_session() as session:
            session.add(Order(id='blocked', config_file_path='test.yml', strategy='test', market='okx',
                              symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT', creation_timestamp=1,
                              order_type='LIMIT', amount=1, price=1, last_status='created', last_update_timestamp=1))
            with pytest.raises(OwnershipLost):
                session.flush()
            session.rollback()
    finally:
        event.remove(owned.engine, 'before_cursor_execute', fail)
    with owned.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM orders WHERE id='blocked'")).scalar() == 0
    assert attempted == [True]
    assert owned.owner.lost and not owned.owner.allow_new_orders
    assert not owned.manager.persistence_healthy


def test_identity_projection_rejects_a_superseding_owner_generation(owned):
    with owned.engine.begin() as connection:
        connection.execute(text('UPDATE bot_state SET owner_generation=owner_generation+1 WHERE bot_id=:bot_id'),
                           {'bot_id': owned.store.bot_id})
    with owned.manager.get_new_session() as session:
        with pytest.raises(OwnershipLost):
            owned.owner.fence_session(session)
    assert owned.owner.lost and not owned.owner.allow_new_orders


@pytest.mark.parametrize('payload_size', [0, 65536])
def test_paired_fresh_projection_reads_keep_queries_and_bound_payload_and_bookkeeping(owned, payload_size):
    import statistics
    import time
    from pathlib import Path

    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET recovery_state=CAST(:recovery AS jsonb), "
                                "mm_session=CAST(:session AS jsonb) WHERE bot_id=:bot_id"),
                           {'recovery': json.dumps({'strategy_checkpoints': {'payload': 'x' * payload_size}}),
                            'session': json.dumps({'catalogue': {'payload': 'y' * payload_size}}),
                            'bot_id': owned.store.bot_id})
    selects, modes = [], []
    record = owned.owner.connection.connection._connection_record
    callbacks_before = len(record.finalize_callback)

    def selected(conn, cursor, statement, *args):
        if conn is owned.owner.connection and 'pg_backend_pid() AS backend_pid' in statement:
            selects.append(len(cursor.description))
            modes.append(conn.connection.driver_connection.autocommit)

    def measure(projected):
        selects.clear()
        modes.clear()
        started = time.perf_counter_ns()
        for _ in range(25):
            row = owned.owner._read_owner(identity_only=projected)
            assert row['backend_pid'] == owned.owner.backend_pid
        elapsed = (time.perf_counter_ns() - started) / 25e6
        assert selects == [1 if projected else 14] * 25
        assert modes == [True] * 25
        assert not owned.owner.connection.in_transaction()
        assert not owned.owner.connection.connection.driver_connection.autocommit
        return dict(ms_per_read=elapsed, fresh_selects=len(selects), columns=selects[0],
                    serialized_result_bytes=len(json.dumps(row, default=str).encode()))

    event.listen(owned.engine, 'after_cursor_execute', selected)
    samples = []
    try:
        measure(False)
        measure(True)
        for index in range(7):
            pair = {}
            for projected in ((False, True) if index % 2 == 0 else (True, False)):
                pair['after' if projected else 'before'] = measure(projected)
            samples.append(pair)
    finally:
        event.remove(owned.engine, 'after_cursor_execute', selected)
    assert len(record.finalize_callback) == callbacks_before
    assert all(s['after']['serialized_result_bytes'] < s['before']['serialized_result_bytes'] for s in samples)
    output = Path('build/owner-fence-projection-20261107')
    output.mkdir(parents=True, exist_ok=True)
    (output / f'paired-{payload_size}.json').write_text(json.dumps(dict(
        payload_size_per_json_field=payload_size, reads_per_batch=25, samples=samples,
        before_median_batch_mean_ms=statistics.median(s['before']['ms_per_read'] for s in samples),
        after_median_batch_mean_ms=statistics.median(s['after']['ms_per_read'] for s in samples),
        pool_reset_callback_growth=len(record.finalize_callback) - callbacks_before,
        scope='Disposable real PostgreSQL; warmed seven alternating batch pairs, each with 25 fresh owner reads. '
              'Two synthetic JSON payload fields, not a measured production distribution. Serialized result bytes '
              'are not network byte counts. Not a whole-engine/probe latency or historical root-cause proof.'), indent=2) + '\n')
