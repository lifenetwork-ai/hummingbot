"""Fill/status/checkpoint stay atomic with one fewer premature guarded flush."""
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import create, fill
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill

owned = _owned


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
