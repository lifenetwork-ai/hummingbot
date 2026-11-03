"""Bound tracking filter work without weakening durable intent/snapshot fences."""
import hashlib
import json
import re
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries, report, seed_orders
from unittest.mock import patch

import pytest
from sqlalchemy import event, insert, select, text, update

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order

owned = _owned


def save(context):
    with context.manager.get_new_session() as session, session.begin():
        context.recorder.save_market_states('test.yml', context.market, session)
    with context.engine.connect() as connection:
        return connection.execute(select(MarketState.saved_state).where(
            MarketState.config_file_path == 'test.yml', MarketState.market == context.market.display_name)).scalar_one()


def plan_visits(plan):
    return ((plan.get('Actual Rows', 0) + plan.get('Rows Removed by Filter', 0)) * plan.get('Actual Loops', 1)
            + sum(plan_visits(child) for child in plan.get('Plans', [])))


@pytest.mark.parametrize('size', [12, 64])
@pytest.mark.parametrize('repetition', range(3))
def test_tracking_cohort_uses_one_scoped_filter_per_snapshot(owned, size, repetition):
    seed_orders(owned.engine, count=100000)
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE orders SET creation_pending=false WHERE creation_pending"))
        connection.execute(text('ANALYZE orders'))
    assert save(owned) == {}
    # All queued creates are locally tracked before the first intent commits.
    orders = [InFlightOrder(f'cohort-{index}', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                            Decimal('.1'), 1700000000, Decimal('100')) for index in range(size)]
    owned.market.in_flight_orders = {order.client_order_id: order for order in orders}
    owned.market.tracking_states = {order.client_order_id: order.to_json() for order in orders}
    original = owned.recorder.save_market_states
    state = dict(inside=False, duration_ms=0, compact_json_bytes=0)
    filters, counts = [], []

    def capture(connection, cursor, statement, parameters, context, executemany):
        normalized = ' ' + re.sub(r'\s+', ' ', statement.lower()) + ' '
        if state['inside'] and normalized.startswith(' select ') and ' from orders ' in normalized:
            filters.append((statement, dict(parameters)))

    def snapshot(*args, **kwargs):
        start = time.perf_counter()
        state['inside'] = True
        try:
            result = original(*args, **kwargs)
        finally:
            state['inside'] = False
            state['duration_ms'] += (time.perf_counter() - start) * 1000
        session = args[2]
        saved = next(row for row in session.identity_map.values() if isinstance(row, MarketState))
        counts.append(len(saved.saved_state))
        state['compact_json_bytes'] += len(json.dumps(saved.saved_state, sort_keys=True, separators=(',', ':')))
        return result

    event.listen(owned.engine, 'before_cursor_execute', capture)
    try:
        with patch.object(owned.recorder, 'save_market_states', side_effect=snapshot), queries(owned.engine) as statements:
            start = time.perf_counter()
            for order in orders:
                owned.owner.before_order(owned.market, order)
            cohort_ms = (time.perf_counter() - start) * 1000
    finally:
        event.remove(owned.engine, 'before_cursor_execute', capture)
    saved = save(owned)
    assert set(saved) == set(owned.market.tracking_states)
    assert counts == list(range(1, size + 1)), 'Never serialize a queued sibling before its intent exists'
    with owned.engine.connect() as connection:
        plans = [connection.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql, params).scalar()[0]['Plan']
                 for sql, params in filters[-2:]]
    metrics = dict(size=size, repetition=repetition, history_orders=100000,
                   snapshot_filter_queries=len(filters), total_sql_statements=len(statements),
                   snapshot_call_ms=state['duration_ms'], instrumented_admission_ms=cohort_ms,
                   compact_snapshot_json_bytes=state['compact_json_bytes'],
                   final_snapshot_sha256=hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest(),
                   last_filter_plans=plans)
    report(f'tracking-cohort-{size}-{repetition}', metrics)
    assert all(plan_visits(plan) < 1000 for plan in plans), 'Filter work must not scale with closed history'
    assert len(filters) == size


def test_tracking_filter_scopes_both_admitted_and_pending_rows_by_config_and_market(owned):
    tracked = intent(owned, 'current')
    with owned.engine.begin() as connection:
        template = dict(connection.execute(select(Order).where(Order.id == 'current')).mappings().one())
        for identity, config, market in [('other-config', 'other.yml', 'okx'), ('other-market', 'test.yml', 'okx_perpetual')]:
            connection.execute(insert(Order).values({**template, 'id': identity, 'config_file_path': config, 'market': market}))
    owned.market.tracking_states.update({identity: {**tracked.to_json(), 'client_order_id': identity}
                                         for identity in ('other-config', 'other-market', 'never-admitted')})
    assert set(save(owned)) == {'current'}


@pytest.mark.parametrize('with_tracked_peer', [False, True])
def test_missing_pending_tracking_is_retained_until_positive_durable_ack(owned, with_tracked_peer):
    tracked = intent(owned, 'lost-ack')
    original = tracked.to_json()
    peer = intent(owned, 'peer') if with_tracked_peer else None
    owned.market.in_flight_orders.clear()
    peer_state = {'peer': peer.to_json()} if peer is not None else {}
    owned.market.tracking_states = peer_state
    assert save(owned) == {**peer_state, 'lost-ack': original}
    with owned.engine.begin() as connection:
        connection.execute(update(Order).where(Order.id == 'lost-ack').values(
            creation_pending=False, exchange_order_id='acknowledged'))
    assert save(owned) == peer_state


def test_tracking_filter_failure_rolls_back_intent_and_preserves_prior_snapshot(owned):
    intent(owned, 'before')
    before = save(owned)
    original = owned.recorder.save_market_states
    state = dict(inside=False)

    def snapshot(*args, **kwargs):
        state['inside'] = True
        try:
            return original(*args, **kwargs)
        finally:
            state['inside'] = False

    def fail(connection, cursor, statement, parameters, context, executemany):
        if state['inside'] and ' from orders ' in ' ' + re.sub(r'\s+', ' ', statement.lower()) + ' ':
            raise OSError('Synthetic tracking-filter failure')

    event.listen(owned.engine, 'before_cursor_execute', fail)
    try:
        with patch.object(owned.recorder, 'save_market_states', side_effect=snapshot):
            with pytest.raises(OSError, match='tracking-filter'):
                intent(owned, 'failed')
    finally:
        event.remove(owned.engine, 'before_cursor_execute', fail)
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.id).where(Order.id == 'failed')).first() is None
        assert connection.execute(select(MarketState.saved_state)).scalar_one() == before
    assert 'failed' not in owned.owner.sent_intents
