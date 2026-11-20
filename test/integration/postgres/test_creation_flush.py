"""Intent/ACK durability must not spend separate guarded flushes on metadata reads."""
from dataclasses import replace
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import create
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import RuntimeJournal, SubmissionAttempt

owned = _owned


def acknowledge(context, identity='order-1'):
    order = context.market.in_flight_orders[identity]
    order.exchange_order_id, order.current_state = 'exchange-' + identity, OrderState.OPEN
    context.market.tracking_states[identity] = order.to_json()
    context.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, context.market, replace(
        create(), order_id=identity, exchange_order_id=order.exchange_order_id,
        price=order.price, creation_timestamp=order.creation_timestamp))


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
