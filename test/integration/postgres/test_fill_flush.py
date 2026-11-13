"""Fill/status/checkpoint stay atomic with one fewer premature guarded flush."""
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import create, fill
from unittest.mock import PropertyMock, patch

import pytest
from sqlalchemy import event, select

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill

owned = _owned


@pytest.mark.parametrize('status', ['OrderCancelled', 'BuyOrderCompleted', 'SellOrderCompleted', 'OrderExpired', 'OrderFailure'])
def test_terminal_status_coalesces_history_and_attempt_before_checkpoint(owned, status):
    from types import SimpleNamespace

    from hummingbot.model.runtime_journal import SubmissionAttempt
    intent(owned)
    flushed = []

    def observe(session, *args):
        flushed.append({type(row).__name__ for row in set(session.new) | set(session.dirty)})

    event.listen(owned.manager._session_cls, 'before_flush', observe)
    try:
        with patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as guard:
            owned.recorder._update_order_status(MarketEvent[status].value, owned.market,
                                                SimpleNamespace(order_id='order-1', exchange_order_id='venue-1'))
    finally:
        event.remove(owned.manager._session_cls, 'before_flush', observe)
    assert len(flushed) == 2, flushed
    assert {'Order', 'OrderStatus', 'SubmissionAttempt'} <= flushed[0], flushed
    assert guard.call_count == 2
    with owned.manager.get_new_session() as session:
        order = session.get(Order, 'order-1')
        assert order.last_status == status
        assert order.amount == Decimal('.1') and order.price == Decimal('100')
        assert session.get(SubmissionAttempt, 'order-1').state == 'terminal'
        assert session.query(OrderStatus).filter_by(order_id='order-1', status=status).count() == 1
        assert session.query(TradeFill).count() == 0
        assert session.query(MarketState).one().saved_state == owned.market.tracking_states


def test_terminal_status_duplicate_keeps_one_history_row_and_uncertain_attempt(owned):
    from types import SimpleNamespace

    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.model.runtime_journal import SubmissionAttempt
    intent(owned)
    with owned.manager.get_new_session() as session, session.begin():
        session.get(SubmissionAttempt, 'order-1').state = 'uncertain'
    with patch.object(MarketsRecorder, 'db_timestamp', new_callable=PropertyMock, return_value=1700000001000):
        for _ in range(2):
            owned.recorder._update_order_status(MarketEvent.OrderCancelled.value, owned.market,
                                                SimpleNamespace(order_id='order-1', exchange_order_id='venue-1'))
    with owned.manager.get_new_session() as session:
        assert session.query(OrderStatus).filter_by(status='OrderCancelled').count() == 1
        assert session.get(SubmissionAttempt, 'order-1').state == 'uncertain'


@pytest.mark.parametrize('failure', ['owner_after_flush', 'final_commit'])
def test_terminal_status_failure_rolls_back_order_attempt_history_and_tracking(owned, failure):
    from types import SimpleNamespace

    from hummingbot.model.runtime_journal import SubmissionAttempt
    intent(owned)
    with owned.engine.connect() as connection:
        original = connection.execute(select(MarketState.saved_state)).scalar_one()
    owned.market.tracking_states = {}

    def fail(session, *args):
        if failure == 'owner_after_flush':
            owned.owner.connection.invalidate()
        else:
            raise RuntimeError('synthetic terminal commit failure')

    hook = 'after_flush' if failure == 'owner_after_flush' else 'before_commit'
    event.listen(owned.manager._session_cls, hook, fail)
    try:
        with pytest.raises(OwnershipLost if failure == 'owner_after_flush' else RuntimeError):
            owned.recorder._update_order_status(MarketEvent.OrderCancelled.value, owned.market,
                                                SimpleNamespace(order_id='order-1', exchange_order_id='venue-1'))
    finally:
        event.remove(owned.manager._session_cls, hook, fail)
    assert not owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.last_status)).scalar_one() == 'PendingBuy'
        assert connection.execute(select(SubmissionAttempt.state)).scalar_one() == 'prepared'
        assert not connection.execute(select(OrderStatus.id).where(OrderStatus.status == 'OrderCancelled')).all()
        assert connection.execute(select(MarketState.saved_state)).scalar_one() == original


def test_terminal_status_paired_roundtrip_measurement(owned):
    """Replay the old flush boundaries with identical fencing and durable writes."""
    import time
    from statistics import median
    from test.integration.postgres.test_performance import report
    from types import SimpleNamespace

    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.model.runtime_journal import SubmissionAttempt
    attempt, history = MarketsRecorder._attempt_observation, MarketsRecorder._add_order_status
    serial = 0

    def old_attempt(recorder, session, *args):
        session.flush()
        return attempt(recorder, session, *args)

    def old_history(session, *args):
        session.flush()
        return history(session, *args)

    def run(legacy, delay):
        nonlocal serial
        serial += 1
        identity = 'terminal-paired-' + str(serial)
        intent(owned, identity)

        def network_wait(*args):
            time.sleep(delay)

        if delay:
            event.listen(owned.engine, 'after_cursor_execute', network_wait)
        try:
            with patch.object(MarketsRecorder, '_attempt_observation', old_attempt if legacy else attempt), \
                    patch.object(MarketsRecorder, '_add_order_status', staticmethod(old_history if legacy else history)), \
                    queries(owned.engine) as statements, \
                    patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as checks:
                started = time.perf_counter()
                owned.recorder._update_order_status(MarketEvent.OrderCancelled.value, owned.market,
                                                    SimpleNamespace(order_id=identity, exchange_order_id='venue-' + identity))
                elapsed = (time.perf_counter() - started) * 1000
        finally:
            if delay:
                event.remove(owned.engine, 'after_cursor_execute', network_wait)
        with owned.manager.get_new_session() as session:
            assert session.get(Order, identity).last_status == 'OrderCancelled'
            assert session.get(SubmissionAttempt, identity).state == 'terminal'
            assert session.query(OrderStatus).filter_by(order_id=identity, status='OrderCancelled').count() == 1
        return dict(sql=len(statements), owner_reads=checks.call_count, elapsed_ms=elapsed)

    values = {}
    for delay in (0, .005):
        pairs = []
        for index in range(5):
            if index % 2:
                new, old = run(False, delay), run(True, delay)
            else:
                old, new = run(True, delay), run(False, delay)
            pairs.append((old, new))
        # No wall-clock performance assertions: deterministic DB work is the contract.
        for old, new in pairs:
            assert old['owner_reads'] == 4 and new['owner_reads'] == 2
            assert old['sql'] - new['sql'] == 2
        values[str(delay)] = dict(
            before_sql=pairs[0][0]['sql'], after_sql=pairs[0][1]['sql'],
            before_p50_ms=median(old['elapsed_ms'] for old, _ in pairs),
            after_p50_ms=median(new['elapsed_ms'] for _, new in pairs),
            injected_delay_per_statement_seconds=delay)
    report('terminal_status_coalescing', values)


def seed(context, known):
    if known:
        intent(context)
    else:
        context.market.tracking_states = {'order-1': {'state': 'pending'}}


def economics(context, *, fills=1, statuses=1):
    with context.manager.get_new_session() as session:
        order = session.get(Order, 'order-1')
        assert order.last_status == 'OrderFilled'
        assert session.query(TradeFill).count() == fills
        assert session.query(OrderStatus).filter_by(status='OrderFilled').count() == statuses
        for row in session.query(TradeFill):
            assert row.amount == Decimal('0.012345') and row.price == Decimal('123.456789')
            assert row.trade_fee_in_quote == Decimal('.2')
        assert session.query(MarketState).one().saved_state == context.market.tracking_states


@pytest.mark.parametrize('known', [False, True])
def test_fill_coalesces_order_and_status_before_checkpoint_without_an_extra_owner_roundtrip(owned, known):
    seed(owned, known)
    flushed = []

    def observe(session, *args):
        flushed.append(sorted({type(row).__name__ for row in set(session.new) | set(session.dirty)}))
    event.listen(owned.manager._session_cls, 'before_flush', observe)
    try:
        with patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as guard, \
                queries(owned.engine) as statements:
            owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill())
    finally:
        event.remove(owned.manager._session_cls, 'before_flush', observe)
    assert len(flushed) == 2, flushed
    assert {'Order', 'OrderStatus', 'TradeFill'} <= set(flushed[0]), flushed
    assert guard.call_count == 2
    # An existing status must still be queried before inserting a new one.
    assert any('order_statuses' in sql and sql.lstrip().upper().startswith('SELECT') for sql in statements)
    economics(owned)


@pytest.mark.parametrize('known', [False, True])
def test_same_timestamp_fills_and_duplicates_keep_one_status_and_exact_economics(owned, known):
    seed(owned, known)
    for identity in ('fill-1', 'fill-2', 'fill-1'):
        owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill(exchange_trade_id=identity))
    economics(owned, fills=2)
    # Late create cannot downgrade a fill. Replaying it must not duplicate history.
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, create())
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, create())
    economics(owned, fills=2)


def test_lost_physical_owner_between_fill_flush_and_checkpoint_rolls_back_without_ack(owned):
    intent(owned)
    flushed = []

    def lose(session, *args):
        flushed.append(True)
        if len(flushed) == 1:
            owned.owner.connection.invalidate()  # Real dedicated lock session is lost.
    event.listen(owned.manager._session_cls, 'after_flush', lose)
    try:
        with patch('hummingbot.core.order_submission_guard.fill_committed') as ack:
            with pytest.raises(OwnershipLost):
                owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill())
        ack.assert_not_called()
    finally:
        event.remove(owned.manager._session_cls, 'after_flush', lose)
    assert owned.owner.lost and not owned.owner.allow_new_orders
    assert not owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        assert not connection.execute(select(TradeFill.exchange_trade_id)).all()
        assert connection.execute(select(Order.last_status).where(Order.id == 'order-1')).scalar_one() == 'PendingBuy'


def test_final_commit_failure_still_rolls_back_all_three_records_without_ack(owned):
    intent(owned)

    def fail(session):
        raise RuntimeError('synthetic commit failure')
    event.listen(owned.manager._session_cls, 'before_commit', fail)
    try:
        with patch('hummingbot.core.order_submission_guard.fill_committed') as ack:
            with pytest.raises(RuntimeError, match='synthetic commit failure'):
                owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill())
        ack.assert_not_called()
    finally:
        event.remove(owned.manager._session_cls, 'before_commit', fail)
    assert not owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        assert not connection.execute(select(TradeFill.exchange_trade_id)).all()
        assert not connection.execute(select(OrderStatus.id).where(OrderStatus.status == 'OrderFilled')).all()


def test_fill_commit_is_visible_before_acknowledgement(owned):
    intent(owned)
    seen = []

    def committed(row):
        with owned.engine.connect() as connection:
            assert connection.execute(select(TradeFill.exchange_trade_id)).scalar_one() == row['exchange_trade_id']
            assert connection.execute(select(Order.last_status)).scalar_one() == 'OrderFilled'
        seen.append(row)
    with patch('hummingbot.core.order_submission_guard.fill_committed', side_effect=committed):
        owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, fill())
    assert len(seen) == 1
    economics(owned)


def test_paired_fill_measurement_retains_fresh_checks_and_removes_one_statement(owned):
    """The control replays the old Order-only flush before its status lookup."""
    import time
    from statistics import median
    from test.integration.postgres.test_performance import report

    from hummingbot.connector.markets_recorder import MarketsRecorder
    original = MarketsRecorder._add_order_status
    intent(owned)
    serial = 0

    def legacy_flush(session, *args):
        session.flush()
        return original(session, *args)

    def run(legacy):
        nonlocal serial
        serial += 1
        flushed = []

        def count(session, *args):
            flushed.append(True)
        event.listen(owned.manager._session_cls, 'before_flush', count)
        try:
            with patch.object(MarketsRecorder, '_add_order_status', staticmethod(legacy_flush if legacy else original)), \
                    queries(owned.engine) as statements, \
                    patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as checks:
                started = time.perf_counter()
                owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market,
                                               fill(exchange_trade_id='paired-' + str(serial),
                                                    timestamp=1700000000 + serial))
                duration = (time.perf_counter() - started) * 1000
        finally:
            event.remove(owned.manager._session_cls, 'before_flush', count)
        return dict(duration_ms=duration, flushes=len(flushed), ownership_checks=checks.call_count,
                    sql_statements=len(statements))
    run(True)
    run(False)
    pairs = []
    for index in range(7):
        pair = {}
        for legacy in ((True, False) if index % 2 == 0 else (False, True)):
            pair['before' if legacy else 'after'] = run(legacy)
        assert pair['before']['flushes'] == pair['before']['ownership_checks'] == 3
        assert pair['after']['flushes'] == pair['after']['ownership_checks'] == 2
        assert pair['before']['sql_statements'] - pair['after']['sql_statements'] == 1
        pairs.append(pair)
    report('fill_flush_coalescing', dict(pairs=pairs,
           before_p50_ms=median(p['before']['duration_ms'] for p in pairs),
           after_p50_ms=median(p['after']['duration_ms'] for p in pairs),
           qualification='Synthetic connector tracking, real PostgreSQL/recorder/owner fences. '
                         'Alternating warmed control replays the original flush schedule; '
                         'component timings have no wall-time threshold or whole-MM/probe attribution.'))
