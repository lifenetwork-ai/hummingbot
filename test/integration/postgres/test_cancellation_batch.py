"""A burst of venue cancellations must commit ahead of an already prepared send."""
import asyncio
import json
import time
from decimal import Decimal
from test.integration.postgres.test_performance import queries, report
from test.integration.postgres.test_submission_admission_fairness import (
    native as native_fixture,
    owned as owned_fixture,
)

import pytest
from psycopg2 import OperationalError as DriverOperationalError
from sqlalchemy import event, select, text, update

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill

native = native_fixture
owned = owned_fixture


def submit(native, identity, side=TradeType.SELL):
    return native.market._create_order(side, identity, 'LIFE-USDT', Decimal('1'),
                                       OrderType.LIMIT_MAKER, Decimal('10'))


def cancel(native, identity):
    return native.market._order_tracker.process_order_update(OrderUpdate(
        'LIFE-USDT', time.time(), OrderState.CANCELED, identity, 'venue-' + identity))


@pytest.mark.parametrize('ahead,expected_commits', [(4, 1), (10, 1), (13, 2)])
def test_contiguous_cancellations_use_bounded_commits_before_a_ready_send(native, monkeypatch, ahead, expected_commits):
    identities = [f'cancel-{i}' for i in range(max(10, ahead))]
    slot = native.recorder.order_writes
    prepared, allow_final, final_queued = (asyncio.Event() for _ in range(3))
    queued = asyncio.Queue()
    authorize, acquire = native.owner.authorize_rest, slot.acquire
    venue, publish = native.transport.call.side_effect, native.recorder.publish_recorded_order_event
    published, dispatch = [], {}
    commits = 0
    started = None

    async def before_final(request):
        if json.loads(request.data)['clOrdId'] == 'ready':
            prepared.set()
            await allow_final.wait()
        return await authorize(request)

    async def observe_queue(**kwargs):
        if kwargs.get('operation') in ('tracker_update', 'cancel_ack'):
            queued.put_nowait(None)
        elif allow_final.is_set() and kwargs.get('operation') == 'final_authority':
            final_queued.set()
        return await acquire(**kwargs)

    def committed(session):
        nonlocal commits
        if started is not None and session.info.get('worker_order_owner') is native.owner:
            commits += 1

    def observe_cancel(market, tag, payload):
        if tag is MarketEvent.OrderCancelled:
            published.append(payload.order_id)
        return publish(market, tag, payload)

    async def send(request):
        if json.loads(request.data)['clOrdId'] == 'ready':
            dispatch.update(wait_ms=(time.perf_counter() - started) * 1000, sql=len(statements),
                            commits=commits, published=list(published))
        return await venue(request)

    async def exercise():
        nonlocal started
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(submit(native, identity) for identity in identities))
            ready = asyncio.create_task(submit(native, 'ready'))
        await prepared.wait()
        updates = []
        async with slot:
            for index, identity in enumerate(identities, 1):
                updates.append(cancel(native, identity))
                await queued.get()
                if index == ahead:
                    allow_final.set()
                    await final_queued.wait()
            statements.clear()
            started = time.perf_counter()
        await asyncio.gather(*updates, ready)

    monkeypatch.setattr(native.owner, 'authorize_rest', before_final)
    monkeypatch.setattr(slot, 'acquire', observe_queue)
    monkeypatch.setattr(native.transport.call, 'side_effect', send)
    monkeypatch.setattr(native.recorder, 'publish_recorded_order_event', observe_cancel)

    def roundtrip(*_):
        if started is not None:
            time.sleep(.003)

    event.listen(native.manager._session_cls, 'after_commit', committed)
    event.listen(native.engine, 'after_cursor_execute', roundtrip)
    try:
        with queries(native.engine) as statements:
            native.loop.run_until_complete(asyncio.wait_for(exercise(), 30))
    finally:
        event.remove(native.manager._session_cls, 'after_commit', committed)
        event.remove(native.engine, 'after_cursor_execute', roundtrip)
    report(f'cancel_burst_ahead_of_send_{ahead}', dispatch)
    assert dispatch['published'] == identities[:ahead]
    assert dispatch['commits'] == expected_commits
    before_send = statements[:dispatch['sql']]
    assert sum('requested_acks' in sql and 'FOR SHARE NOWAIT' in sql for sql in before_send) == expected_commits
    assert sum('INSERT INTO order_statuses' in sql for sql in before_send) == expected_commits
    assert published == identities and native.transport.call.await_count == len(identities) + 1
    assert not native.owner.terminal_tasks and not slot.locked()
    with native.manager.get_new_session() as db:
        assert {row.order_id for row in db.query(SubmissionAttempt).filter_by(state='terminal')} == set(identities)
        assert sorted(db.scalars(select(OrderStatus.order_id).where(OrderStatus.status == 'OrderCancelled'))) == sorted(identities)
        assert db.get(Order, 'ready').last_status == 'SellOrderCreated'
        saved = db.query(MarketState).one().saved_state
        assert {identity for identity, order in saved.items() if order['last_state'] == str(OrderState.OPEN.value)} == {'ready'}


@pytest.mark.parametrize('failure,expected_error', [
    ('identity', 'Cancel acknowledgment conflicts'),
    ('commit', 'cancel commit rejected'),
    ('reply_lost', 'cancel COMMIT reply lost'),
])
def test_failed_cancel_batch_leaves_tracker_waiting_for_durable_outcome(native, caplog, monkeypatch, failure, expected_error):
    identities = ['first', 'conflict', 'last']

    async def exercise():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(submit(native, identity) for identity in identities))
        if failure == 'reply_lost':
            driver = type(native.owner.connection.connection.driver_connection)
            commit = driver.commit

            def lose_reply(connection):
                commit(connection)
                monkeypatch.setattr(driver, 'commit', commit)
                connection.close()
                raise DriverOperationalError('cancel COMMIT reply lost')

            monkeypatch.setattr(driver, 'commit', lose_reply)
        elif failure == 'identity':
            with native.engine.begin() as db:
                db.execute(update(Order).where(Order.id == 'conflict').values(exchange_order_id='different-venue-id'))
        else:
            with native.engine.begin() as db:
                db.execute(text("""
                    CREATE FUNCTION fail_cancel_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'cancel commit rejected' USING ERRCODE = '55P03'; END; $$;
                    CREATE CONSTRAINT TRIGGER fail_cancel_commit AFTER UPDATE ON orders
                    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                    WHEN (NEW.id = 'conflict' AND NEW.last_status = 'OrderCancelled') EXECUTE FUNCTION fail_cancel_commit()
                """))
        async with native.recorder.order_writes:
            updates = [cancel(native, identity) for identity in identities]
            await asyncio.sleep(0)
        await asyncio.gather(*updates)

    native.loop.run_until_complete(asyncio.wait_for(exercise(), 15))
    assert expected_error in caplog.text
    assert set(native.market.in_flight_orders) == set(identities)
    assert all(order.current_state is OrderState.OPEN for order in native.market.in_flight_orders.values())
    assert not native.owner.terminal_tasks and not native.recorder.order_writes.locked()
    assert not native.manager.persistence_healthy
    committed = failure == 'reply_lost'
    with native.manager.get_new_session() as db:
        assert db.query(OrderStatus).filter_by(status='OrderCancelled').count() == (len(identities) if committed else 0)
        assert db.query(SubmissionAttempt).filter_by(state='terminal' if committed else 'acknowledged').count() == len(identities)
        restored = ClientOrderTracker(native.market)
        restored.restore_tracking_states(db.query(MarketState).one().saved_state)
        assert set(restored.active_orders) == (set() if committed else set(identities))


def test_cancel_batch_preserves_partial_fill_and_keeps_buy_reservations_until_readback(native):
    ids = ['empty-buy', 'partial-buy']
    tracker, guard = native.market._order_tracker, native.owner.spot_outflow

    async def exercise():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(submit(native, identity, TradeType.BUY) for identity in ids))
        fill = TradeUpdate('17', 'partial-buy', 'venue-partial-buy', 'LIFE-USDT', time.time(),
                           Decimal('10'), Decimal('.4'), Decimal('4'),
                           AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))]))
        await tracker.apply_trade_update(fill)
        async with native.recorder.order_writes:
            updates = [cancel(native, identity) for identity in ids]
            await asyncio.sleep(0)
        await asyncio.gather(*updates)

    native.loop.run_until_complete(asyncio.wait_for(exercise(), 15))
    assert set(guard.terminal_pending) == set(guard.projection.reservations) == set(ids)
    assert not guard.status()['buy_allowed']
    assert all(tracker.fetch_cached_order(identity).is_cancelled for identity in ids)
    with native.manager.get_new_session() as db:
        fill = db.query(TradeFill).one()
        assert (fill.order_id, fill.exchange_trade_id, fill.amount, fill.trade_fee_in_quote) == (
            'partial-buy', '17', Decimal('.4'), Decimal('.01'))
        assert db.query(SubmissionAttempt).filter_by(state='terminal').count() == len(ids)
        saved = db.query(MarketState).one().saved_state
        assert set(saved) == {'partial-buy'}
        assert saved['partial-buy']['last_state'] == str(OrderState.CANCELED.value)
        assert Decimal(saved['partial-buy']['executed_amount_base']) == Decimal('.4')
