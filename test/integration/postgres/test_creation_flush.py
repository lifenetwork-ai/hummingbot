"""Intent/ACK durability must not spend separate guarded flushes on metadata reads."""
import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import create
from test.integration.postgres.test_submission_admission_fairness import native as native_fixture
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from psycopg2 import OperationalError as DriverOperationalError
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError

from hummingbot.connector import markets_recorder
from hummingbot.core.bot_ownership import ORDER_PATH, OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import RuntimeJournal, SubmissionAttempt

owned = _owned
native = native_fixture


def acknowledge(context, identity='order-1'):
    order = context.market.in_flight_orders[identity]
    order.exchange_order_id, order.current_state = 'exchange-' + identity, OrderState.OPEN
    context.market.tracking_states[identity] = order.to_json()
    context.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, context.market, replace(
        create(), order_id=identity, exchange_order_id=order.exchange_order_id,
        price=order.price, creation_timestamp=order.creation_timestamp))


@pytest.mark.parametrize('outcome', ['committed', 'cancelled', 'rejected', 'reply_lost'])
def test_ready_native_open_acks_share_a_commit_before_publishing(native, monkeypatch, outcome):
    tracker = native.market._order_tracker
    identities = {f'burst-ack-{i}' for i in range(6)}
    existing_history = min(identities)
    flushes, published, ack_connections = [], [], []
    publish = native.recorder.publish_recorded_order_event
    acquire = native.recorder.order_writes.acquire
    queued = asyncio.Event()
    waiting = 0
    track_waiters = False
    reject_commit = outcome == 'rejected'
    reply_lost = outcome == 'reply_lost'

    if reject_commit:
        with native.engine.begin() as connection:
            connection.execute(text("""
                CREATE FUNCTION reject_burst_ack() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'burst ACK commit rejected' USING ERRCODE = '55P03'; END;
                $$;
                CREATE CONSTRAINT TRIGGER reject_burst_ack AFTER UPDATE ON orders
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                WHEN (NEW.id = 'burst-ack-3' AND NOT NEW.creation_pending) EXECUTE FUNCTION reject_burst_ack()
            """))

    def inspect_flush(session, *_):
        created = {row.order.id for row in session.new
                   if isinstance(row, OrderStatus) and row.status == 'BuyOrderCreated'}
        if created:
            flushes.append(created)
            if reply_lost:
                ack_connections.append(session.connection().connection.driver_connection)

    if reply_lost:
        driver_class = type(native.owner.connection.connection.driver_connection)
        commit = driver_class.commit

        def lose_ack_reply(driver):
            commit(driver)
            if driver in ack_connections:
                ack_connections.remove(driver)
                driver.close()
                raise DriverOperationalError('test ACK COMMIT reply lost')

        monkeypatch.setattr(driver_class, 'commit', lose_ack_reply)

    def observe_publication(market, tag, payload):
        assert all(tracker.fetch_tracked_order(identity).current_state is OrderState.OPEN for identity in identities)
        with native.manager.get_new_session() as db:
            published.append((payload.order_id, db.query(SubmissionAttempt).filter_by(state='acknowledged').count()))
        publish(market, tag, payload)

    async def observe_acquire(**kwargs):
        nonlocal waiting
        if track_waiters:
            waiting += 1
            if waiting == len(identities):
                queued.set()
        return await acquire(**kwargs)

    async def exercise():
        nonlocal track_waiters
        created_at = time.time()
        orders = [InFlightOrder(identity, 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                                Decimal('5'), created_at, Decimal('10')) for identity in sorted(identities)]
        for order in orders:
            tracker.start_tracking_order(order)
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(native.owner.admit_order(native.market, order) for order in orders))
        for order in orders:
            identity = order.client_order_id
            native.owner._record_send_attempt(identity, {'clOrdId': identity}, 123456, {})
        # One order already has creation history at the shared timestamp.
        with native.engine.begin() as connection:
            connection.execute(OrderStatus.__table__.insert().values(
                order_id=existing_history, timestamp=int(created_at * 1e3), status='BuyOrderCreated'))
        async with native.recorder.order_writes:
            track_waiters = True
            tasks = [asyncio.create_task(tracker._process_order_update(OrderUpdate(
                'LIFE-USDT', time.time(), OrderState.OPEN, order.client_order_id, f'venue-{order.client_order_id}')))
                for order in orders]
            await asyncio.wait_for(queued.wait(), 2)
            if outcome == 'cancelled':
                tasks[2].cancel()
        return orders, await asyncio.gather(*tasks, return_exceptions=True)

    monkeypatch.setattr(native.recorder, 'publish_recorded_order_event', observe_publication)
    monkeypatch.setattr(native.recorder.order_writes, 'acquire', observe_acquire)
    event.listen(native.manager._session_cls, 'before_flush', inspect_flush)
    try:
        with queries(native.engine) as statements:
            orders, outcomes = native.loop.run_until_complete(exercise())
    finally:
        event.remove(native.manager._session_cls, 'before_flush', inspect_flush)
    assert flushes == [identities - {existing_history}]
    evidence_reads = [statement for statement in statements if statement.startswith('SELECT')
                      and 'JOIN submission_attempts' in statement and 'FROM order_status' in statement]
    assert len(evidence_reads) == 1
    with native.manager.get_new_session() as db:
        history = db.scalars(select(OrderStatus.order_id).where(OrderStatus.status == 'BuyOrderCreated')).all()
        assert sorted(history) == ([existing_history] if reject_commit else sorted(identities))
        assert {row.state for row in db.query(SubmissionAttempt)} == {'possibly_sent' if reject_commit else 'acknowledged'}
        assert all(row.creation_pending is reject_commit for row in db.query(Order))
        snapshot = db.query(MarketState).one().saved_state
        if reply_lost:
            assert set(snapshot) == identities
            assert all(order.current_state is OrderState.PENDING_CREATE for order in orders)
            assert all(state['exchange_order_id'] == f'venue-{identity}' for identity, state in snapshot.items())
        else:
            assert snapshot == {order.client_order_id: order.to_json() for order in orders}
    if reject_commit or reply_lost:
        assert all(isinstance(result, OperationalError) for result in outcomes)
        assert not published and not native.manager.persistence_healthy
    else:
        if outcome == 'cancelled':
            assert isinstance(outcomes[2], asyncio.CancelledError)
            outcomes[2] = None
        assert outcomes == [None] * len(orders)
        assert set(published) == {(identity, len(identities)) for identity in identities}
    assert not native.owner.terminal_tasks and not native.recorder.order_writes.locked()


@pytest.mark.parametrize('outcome', ['confirmed', 'cancelled', 'commit_rejected'])
def test_native_open_ack_yields_recorder_during_rollback_retry(native, monkeypatch, outcome):
    monkeypatch.setattr(markets_recorder, '_ORDER_FENCE_RETRY_SECONDS', 15)
    tracker = native.market._order_tracker
    if outcome == 'commit_rejected':
        # A lock error at COMMIT is outside the verified prewrite failure path.
        with native.engine.begin() as connection:
            connection.execute(text("""
                CREATE FUNCTION reject_ack_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'lock error at ACK commit' USING ERRCODE = '55P03'; END
                $$
            """))
            connection.execute(text("""
                CREATE CONSTRAINT TRIGGER reject_ack_commit AFTER UPDATE ON orders
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                WHEN (NEW.id = 'busy-ack' AND NOT NEW.creation_pending) EXECUTE FUNCTION reject_ack_commit()
            """))

    async def exercise():
        now = time.time()
        for identity in ('busy-ack', 'independent-receipt'):
            order = InFlightOrder(identity, 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                                  Decimal('5'), now, Decimal('10'))
            tracker.start_tracking_order(order)
            with session_order_scope(native.session.id, native.session.generation):
                await native.owner.admit_order(native.market, order)
            native.owner._record_send_attempt(identity, {'clOrdId': identity}, 123456, {})
        tracked = tracker.fetch_tracked_order('busy-ack')
        loop = asyncio.get_running_loop()
        rejected = asyncio.Event()
        retry_waiting, release_retry = asyncio.Event(), asyncio.Event()

        async def pause_retry(_delay):
            retry_waiting.set()
            await release_retry.wait()

        monkeypatch.setattr(native.recorder, '_sleep', pause_retry)

        def notice_rejected_fence(context):
            if getattr(context.original_exception, 'pgcode', None) == '55P03' and 'FOR SHARE NOWAIT' in (context.statement or ''):
                loop.call_soon_threadsafe(rejected.set)

        open_update = OrderUpdate('LIFE-USDT', now, OrderState.OPEN, 'busy-ack', 'venue-ack')
        cancel_update = open_update._replace(new_state=OrderState.PENDING_CANCEL, update_timestamp=now + 1)
        request = SimpleNamespace(url='https://offline.invalid' + ORDER_PATH,
                                  data=json.dumps({'clOrdId': 'independent-receipt'}))
        response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))
        tasks = []
        event.listen(native.engine, 'handle_error', notice_rejected_fence)
        try:
            with native.engine.connect() as blocker:
                blocker.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '12s'")
                blocker.exec_driver_sql('SELECT bot_id FROM bot_state FOR UPDATE').all()
                ack = asyncio.create_task(tracker._process_order_update(open_update))
                tasks.append(ack)
                await asyncio.wait_for(retry_waiting.wait(), 5)
                assert rejected.is_set()
                if outcome == 'cancelled':
                    ack.cancel()
                if outcome != 'commit_rejected':
                    cancel = asyncio.create_task(tracker._process_order_update(cancel_update))
                    tasks.append(cancel)
                receipt = asyncio.create_task(native.owner.record_transport_receipt(request, response))
                tasks.append(receipt)
                await asyncio.wait_for(asyncio.shield(receipt), 5)
                assert ack in native.owner.terminal_tasks and not ack.done()
                assert tracked.current_state is OrderState.PENDING_CREATE
                if outcome != 'commit_rejected':
                    assert not cancel.done()
                with native.manager.get_new_session() as db:
                    assert db.get(SubmissionAttempt, 'independent-receipt').receipt == {'http_status': 200, 'code': '0'}
                    assert db.get(Order, 'busy-ack').creation_pending
                blocker.rollback()
            release_retry.set()
            result, *others = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
            assert all(other is None for other in others) and not native.owner.terminal_tasks
            if outcome == 'commit_rejected':
                assert isinstance(result, OperationalError) and result.orig.pgcode == '55P03'
                assert tracked.current_state is OrderState.PENDING_CREATE and not native.manager.persistence_healthy
            else:
                if outcome == 'cancelled':
                    assert isinstance(result, asyncio.CancelledError)
                else:
                    assert result is None
                assert tracked.current_state is OrderState.PENDING_CANCEL and native.manager.persistence_healthy
        finally:
            release_retry.set()
            event.remove(native.engine, 'handle_error', notice_rejected_fence)
            await asyncio.gather(*tasks, return_exceptions=True)

    native.loop.run_until_complete(exercise())
    with native.manager.get_new_session() as db:
        committed = outcome != 'commit_rejected'
        assert db.get(SubmissionAttempt, 'busy-ack').state == ('acknowledged' if committed else 'possibly_sent')
        assert db.query(OrderStatus).filter_by(order_id='busy-ack', status='BuyOrderCreated').count() == int(committed)


@pytest.mark.parametrize('stage', ['intent', 'acknowledged'])
def test_creation_metadata_is_grouped_before_authoritative_tracking_snapshot(owned, stage):
    if stage == 'acknowledged':
        intent(owned)
    flushed = []

    def observe(session, *args):
        flushed.append({type(row).__name__ for row in set(session.new) | set(session.dirty)})

    event.listen(owned.manager._session_cls, 'before_flush', observe)
    try:
        with patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as owner_reads, \
                queries(owned.engine) as statements:
            intent(owned) if stage == 'intent' else acknowledge(owned)
    finally:
        event.remove(owned.manager._session_cls, 'before_flush', observe)
    assert len(flushed) == 2, flushed
    assert {'Order', 'OrderStatus', 'SubmissionAttempt'} <= flushed[0], flushed
    assert flushed[1] == {'MarketState'}, flushed
    # Intent checks admission and acquires its NOWAIT fence before staging,
    # then checks physical ownership at both flushes.
    assert owner_reads.call_count == (4 if stage == 'intent' else 2)
    assert any('orders.creation_pending' in sql for sql in statements)  # Never skip fresh admission proof.
    with owned.manager.get_new_session() as db:
        order = db.get(Order, 'order-1')
        assert order.amount == Decimal('.1') and order.price == Decimal('100')
        assert order.creation_pending is (stage == 'intent')
        attempt = db.get(SubmissionAttempt, 'order-1')
        assert attempt.state == ('prepared' if stage == 'intent' else 'acknowledged')
        assert attempt.context['owner_uuid'] == owned.owner.owner_uuid
        assert attempt.payload_hash is None and attempt.receipt is None
        assert db.query(OrderStatus).filter_by(order_id='order-1').count() == (1 if stage == 'intent' else 2)
        assert db.query(RuntimeJournal).filter_by(event_key='intent:order-1', kind='intent').one().applied
        assert db.query(MarketState).one().saved_state == owned.market.tracking_states


@pytest.mark.parametrize('stage', ['intent', 'acknowledged'])
@pytest.mark.parametrize('failure', ['owner_after_flush', 'commit'])
def test_creation_metadata_failure_rolls_back_identity_attempt_history_and_snapshot(owned, stage, failure):
    if stage == 'acknowledged':
        intent(owned)
    with owned.engine.connect() as connection:
        original = connection.execute(select(MarketState.saved_state)).scalar_one_or_none()

    def fail(session, *args):
        if failure == 'owner_after_flush':
            owned.owner.connection.invalidate()
        else:
            raise RuntimeError('synthetic creation commit failure')

    hook = 'after_flush' if failure == 'owner_after_flush' else 'before_commit'
    event.listen(owned.manager._session_cls, hook, fail)
    try:
        with pytest.raises(OwnershipLost if failure == 'owner_after_flush' else RuntimeError):
            intent(owned) if stage == 'intent' else acknowledge(owned)
    finally:
        event.remove(owned.manager._session_cls, hook, fail)
    assert not owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        rows = connection.execute(select(Order.last_status, Order.creation_pending)).all()
        assert rows == ([] if stage == 'intent' else [('PendingBuy', True)])
        attempts = connection.execute(select(SubmissionAttempt.state)).scalars().all()
        assert attempts == ([] if stage == 'intent' else ['prepared'])
        assert len(connection.execute(select(OrderStatus.id)).all()) == (0 if stage == 'intent' else 1)
        assert connection.execute(select(MarketState.saved_state)).scalar_one_or_none() == original


@pytest.mark.parametrize('prior', ['terminal', 'uncertain'])
def test_acknowledgement_never_regresses_terminal_or_uncertain_evidence(owned, prior):
    intent(owned)
    if prior == 'terminal':
        owned.recorder._update_order_status(MarketEvent.OrderCancelled.value, owned.market,
                                            SimpleNamespace(order_id='order-1', exchange_order_id='exchange-order-1'))
    else:
        with owned.manager.get_new_session() as db, db.begin():
            db.get(SubmissionAttempt, 'order-1').state = 'uncertain'
    acknowledge(owned)
    acknowledge(owned)
    with owned.manager.get_new_session() as db:
        order = db.get(Order, 'order-1')
        assert not order.creation_pending and order.exchange_order_id == 'exchange-order-1'
        assert order.last_status == ('OrderCancelled' if prior == 'terminal' else 'BuyOrderCreated')
        assert db.get(SubmissionAttempt, 'order-1').state == ('terminal' if prior == 'terminal' else 'uncertain')
        assert db.query(OrderStatus).filter_by(order_id='order-1', status='BuyOrderCreated').count() == 1


def test_creation_snapshot_admits_only_durable_peers_and_the_new_identity(owned):
    first = intent(owned, 'first')
    owned.market.tracking_states['never-admitted'] = {**first.to_json(), 'client_order_id': 'never-admitted'}
    second = intent(owned, 'second')
    with owned.manager.get_new_session() as db:
        saved = db.query(MarketState).one().saved_state
        assert saved == {'first': first.to_json(), 'second': second.to_json()}
        assert {row.id for row in db.query(Order).filter(Order.creation_pending.is_(True))} == {'first', 'second'}
        assert {row.order_id for row in db.query(SubmissionAttempt)} == {'first', 'second'}


@pytest.mark.parametrize('tracking_not_empty', [False, True])
def test_worker_ack_snapshot_keeps_other_missing_pending_orders(owned, tracking_not_empty):
    peer = intent(owned, 'still-pending')
    order = intent(owned, 'acknowledged')
    created = replace(create(), order_id=order.client_order_id, exchange_order_id='venue-ack',
                      price=order.price, creation_timestamp=order.creation_timestamp)
    tracking = {'never-admitted': {**peer.to_json(), 'client_order_id': 'never-admitted'}} if tracking_not_empty else {}
    owned.loop.run_until_complete(owned.recorder.persist_order_update(
        owned.market, [(MarketEvent.BuyOrderCreated, created)], tracking))

    with owned.manager.get_new_session() as db:
        assert not db.get(Order, order.client_order_id).creation_pending
        assert db.get(SubmissionAttempt, order.client_order_id).state == 'acknowledged'
        assert db.query(MarketState).one().saved_state == {peer.client_order_id: peer.to_json()}


@pytest.mark.parametrize('missing_peer', [False, True])
def test_worker_ack_projects_old_snapshot_and_loads_json_only_for_a_missing_pending_peer(owned, missing_peer):
    peer = intent(owned, 'pending') if missing_peer else None
    order = intent(owned)
    order.update_exchange_order_id('venue-ack')
    order.current_state = OrderState.OPEN
    created = replace(create(), order_id=order.client_order_id, exchange_order_id='venue-ack',
                      price=order.price, creation_timestamp=order.creation_timestamp)
    tracking = {order.client_order_id: order.to_json()}
    with queries(owned.engine) as statements:
        owned.loop.run_until_complete(owned.recorder.persist_order_update(
            owned.market, [(MarketEvent.BuyOrderCreated, created)], tracking))
    snapshot_reads = [sql for sql in statements if sql.startswith('SELECT')
                      and ('FROM market_states' in sql or 'JOIN market_states' in sql)]
    assert len(snapshot_reads) == 1 + missing_peer
    assert 'market_states.saved_state' not in snapshot_reads[0]
    assert 'FROM orders' in snapshot_reads[0] and 'orders.creation_pending' in snapshot_reads[0]
    with owned.manager.get_new_session() as db:
        expected = {**tracking, peer.client_order_id: peer.to_json()} if missing_peer else tracking
        assert db.query(MarketState).one().saved_state == expected


@pytest.mark.parametrize('prior', ['possibly_sent', 'uncertain', 'terminal', 'no_attempt', 'no_order'])
def test_worker_ack_reads_creation_evidence_together_under_both_fences(owned, prior):
    if prior != 'no_order':
        intent(owned)
        if prior == 'terminal':
            owned.recorder._update_order_status(MarketEvent.OrderCancelled.value, owned.market,
                                                SimpleNamespace(order_id='order-1', exchange_order_id='venue-ack'))
        else:
            with owned.manager.get_new_session() as db, db.begin():
                attempt = db.get(SubmissionAttempt, 'order-1')
                if prior == 'no_attempt':
                    db.delete(attempt)
                else:
                    attempt.state = prior

    created = replace(create(), order_id='order-1', exchange_order_id='venue-ack')
    with queries(owned.engine) as statements:
        owned.loop.run_until_complete(owned.recorder.persist_order_update(
            owned.market, [(MarketEvent.BuyOrderCreated, created)], {}))
    if prior != 'no_order':
        identity_reads = [sql for sql in statements if sql.startswith('SELECT') and (
            'FROM orders \nWHERE orders.id =' in sql or 'submission_attempts' in sql or 'order_statuses' in sql)]
        assert len(identity_reads) == 1, identity_reads
        assert all(table in identity_reads[0] for table in ('orders', 'submission_attempts', 'order_statuses'))
        assert 'submission_attempts.context' not in identity_reads[0]
        assert 'submission_attempts.receipt' not in identity_reads[0]
    fences = [sql for sql in statements if 'FOR SHARE NOWAIT' in sql or 'pg_locks' in sql]
    assert len(fences) == 2
    assert 'FOR SHARE NOWAIT' in fences[0] and 'pg_locks' in fences[0]
    assert 'FOR SHARE NOWAIT' not in fences[1] and 'pg_locks' in fences[1]

    # An identical replay must preserve the status row and prior terminal or uncertain proof.
    owned.loop.run_until_complete(owned.recorder.persist_order_update(
        owned.market, [(MarketEvent.BuyOrderCreated, created)], {}))
    with owned.manager.get_new_session() as db:
        order = db.get(Order, 'order-1')
        assert not order.creation_pending and order.exchange_order_id == 'venue-ack'
        assert order.last_status == ('OrderCancelled' if prior == 'terminal' else 'BuyOrderCreated')
        assert db.query(OrderStatus).filter_by(order_id=order.id, status='BuyOrderCreated').count() == 1
        attempt = db.get(SubmissionAttempt, order.id)
        assert (attempt.state if attempt is not None else None) == {
            'possibly_sent': 'acknowledged', 'uncertain': 'uncertain', 'terminal': 'terminal',
            'no_attempt': None, 'no_order': None,
        }[prior]


def test_creation_pipeline_paired_roundtrip_measurement(owned):
    """Replay the predecessor flush cuts with identical writes and owner fences."""
    import time
    from statistics import median
    from test.integration.postgres.test_performance import report

    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.model import runtime_journal
    prepare, attempt, history = (runtime_journal.prepare_attempt, MarketsRecorder._attempt_observation,
                                 MarketsRecorder._add_order_status)
    serial = 0

    def old_prepare(session, *args):
        session.flush()
        return prepare(session, *args)

    def old_attempt(recorder, session, *args):
        session.flush()
        return attempt(recorder, session, *args)

    def old_history(session, *args):
        session.flush()
        return history(session, *args)

    def run(stage, legacy, delay):
        nonlocal serial
        serial += 1
        identity = 'creation-paired-' + str(serial)
        if stage == 'acknowledged':
            intent(owned, identity)

        def network_wait(*args):
            time.sleep(delay)

        if delay:
            event.listen(owned.engine, 'after_cursor_execute', network_wait)
        try:
            with patch.object(runtime_journal, 'prepare_attempt', old_prepare if legacy else prepare), \
                    patch.object(MarketsRecorder, '_attempt_observation', old_attempt if legacy else attempt), \
                    patch.object(MarketsRecorder, '_add_order_status', staticmethod(old_history if legacy else history)), \
                    queries(owned.engine) as statements, \
                    patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as checks:
                started = time.perf_counter()
                intent(owned, identity) if stage == 'intent' else acknowledge(owned, identity)
                elapsed = (time.perf_counter() - started) * 1000
        finally:
            if delay:
                event.remove(owned.engine, 'after_cursor_execute', network_wait)
        with owned.manager.get_new_session() as db:
            row = db.get(Order, identity)
            assert row.amount == Decimal('.1') and row.price == Decimal('100')
            assert db.get(SubmissionAttempt, identity).state == ('prepared' if stage == 'intent' else 'acknowledged')
            assert db.query(OrderStatus).filter_by(order_id=identity).count() == (1 if stage == 'intent' else 2)
            assert identity in db.query(MarketState).one().saved_state
        return dict(sql=len(statements), owner_reads=checks.call_count, elapsed_ms=elapsed)

    values = {}
    for stage in ('intent', 'acknowledged'):
        for delay in (0, .005):
            pairs = []
            for index in range(4):
                if index % 2:
                    new, old = run(stage, False, delay), run(stage, True, delay)
                else:
                    old, new = run(stage, True, delay), run(stage, False, delay)
                pairs.append((old, new))
            for old, new in pairs:
                assert old['owner_reads'] - new['owner_reads'] == 2
                assert old['sql'] - new['sql'] == 2
            # Fixed DB-work savings are asserted; wall-clock medians are evidence only.
            values[stage + ':' + str(delay)] = dict(
                before_sql=pairs[0][0]['sql'], after_sql=pairs[0][1]['sql'],
                before_owner_reads=pairs[0][0]['owner_reads'], after_owner_reads=pairs[0][1]['owner_reads'],
                before_p50_ms=median(old['elapsed_ms'] for old, _ in pairs),
                after_p50_ms=median(new['elapsed_ms'] for _, new in pairs),
                injected_delay_per_statement_seconds=delay)
    report('creation_pipeline_coalescing', values)


def test_creation_stage_traces_distinguish_intent_and_ack_without_identifiers(owned, monkeypatch):
    import hashlib
    import json

    import hummingbot.core.engine_diagnostics as diagnostic
    tracer = diagnostic.EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    tracer.attach_engine(owned.engine)
    identity = 'never-export-traced-creation'
    try:
        intent(owned, identity)
        acknowledge(owned, identity)
        wrong = replace(create(), order_id=identity, trading_pair='ETH-USDT')
        with pytest.raises(ValueError, match='identity conflicts'):
            owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, wrong)
        snapshot = tracer.snapshot()
        assert snapshot['operations']['order.intent']['completed'] == 1
        assert snapshot['operations']['order.created']['completed'] == 2
        assert snapshot['operations']['order.created']['errors'] == 1
        key = hashlib.sha256(identity.encode()).hexdigest()[:16]
        stages = [row for row in snapshot['recent'] if row['operation'] in ('order.intent', 'order.created')]
        assert stages and all(row['order'] == key for row in stages)
        rendered = json.dumps(snapshot)
        assert identity not in rendered and 'ETH-USDT' not in rendered
        assert 'identity conflicts' not in rendered and 'INSERT INTO' not in rendered
    finally:
        owned.loop.run_until_complete(tracer.close())
