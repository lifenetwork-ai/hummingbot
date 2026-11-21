"""Background funding pages must not hold up the financial recorder fence."""
import threading
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_funding_bills import bill
from test.integration.postgres.test_ownership import intent, owned as _owned

import pytest
from sqlalchemy import create_engine, event, select, text

from hummingbot.core.control_errors import ControlConflict
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent
from hummingbot.model.funding_bill import FundingBill, FundingBillStore

owned = _owned


def funding(owned):
    return FundingBillStore(owned.engine, owned.store.bot_id, owner_backend_pid=owned.owner.backend_pid)


def pause_insert(owned):
    entered, release = threading.Event(), threading.Event()

    def pause(connection, cursor, statement, *args):
        if statement.startswith('INSERT INTO funding_bills'):
            entered.set()
            assert release.wait(5)

    event.listen(owned.engine, 'before_cursor_execute', pause)
    return entered, release, pause


def test_funding_background_insert_does_not_fail_real_create_ack(owned):
    store = funding(owned)
    order = intent(owned)
    ack = BuyOrderCreatedEvent(timestamp=1700000001, creation_timestamp=order.creation_timestamp,
                               type=OrderType.LIMIT, trading_pair=order.trading_pair, amount=order.amount,
                               price=order.price, order_id=order.client_order_id, exchange_order_id='venue-ack')
    entered, release, pause = pause_insert(owned)
    try:
        with ThreadPoolExecutor(max_workers=1) as work:
            task = work.submit(store.commit, owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1})
            try:
                assert entered.wait(3)
                owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, ack)
                assert owned.manager.persistence_healthy
            finally:
                release.set()
                task.result(timeout=5)
        assert store.load() == {'cursor': 1}
    finally:
        release.set()
        event.remove(owned.engine, 'before_cursor_execute', pause)


def test_funding_page_yields_without_committing_bills_or_cursor(owned):
    store = funding(owned)
    with owned.engine.begin() as financial, ThreadPoolExecutor(max_workers=1) as work:
        financial.execute(text('SELECT bot_id FROM bot_state FOR SHARE')).all()
        task = work.submit(store.commit, owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1})
        with pytest.raises(ControlConflict):
            task.result(timeout=3)
    assert store.load() == {}
    with owned.engine.connect() as connection:
        assert connection.execute(select(FundingBill.bill_id)).all() == []
    assert store.commit(owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1}) == {'cursor': 1}


def test_funding_locks_owner_and_updates_checkpoint_in_one_statement(owned):
    from test.integration.postgres.test_performance import queries

    store = funding(owned)
    with queries(owned.engine) as statements:
        assert store.commit(owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1}) == {'cursor': 1}

    locked = [sql for sql in statements if 'FOR UPDATE SKIP LOCKED' in sql or 'UPDATE bot_state SET' in sql]
    assert len(locked) == 1, locked
    assert 'FOR UPDATE SKIP LOCKED' in locked[0] and 'UPDATE bot_state SET' in locked[0]
    assert store.load() == {'cursor': 1}


@pytest.mark.parametrize('change', ['lost_owner', 'risk_checkpoint'])
def test_funding_page_final_fence_preserves_authority_and_concurrent_risk(owned, change):
    store = funding(owned)
    entered, release, pause = pause_insert(owned)
    try:
        with ThreadPoolExecutor(max_workers=1) as work:
            task = work.submit(store.commit, owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1})
            try:
                assert entered.wait(3)
                if change == 'lost_owner':
                    with owned.engine.begin() as connection:
                        connection.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': owned.owner.backend_pid})
                else:
                    owned.owner._mutate_recovery(lambda state: state.update(protected_risk={'keep': True}))
            finally:
                release.set()
            if change == 'lost_owner':
                with pytest.raises(ControlConflict):
                    task.result(timeout=5)
                assert store.load() == {}
                with owned.engine.connect() as connection:
                    assert connection.execute(select(FundingBill.bill_id)).all() == []
            else:
                assert task.result(timeout=5) == {'cursor': 1}
                assert owned.store.read()['recovery_state']['protected_risk'] == {'keep': True}
    finally:
        release.set()
        event.remove(owned.engine, 'before_cursor_execute', pause)


def test_concurrent_different_funding_bill_cannot_silently_advance_cursor(owned):
    from decimal import Decimal

    store = funding(owned)
    ready = threading.Barrier(2)

    def initial_read(connection, cursor, statement, *args):
        if 'FROM bot_state' in statement and 'pg_locks' in statement and 'FOR UPDATE' not in statement:
            ready.wait(5)

    event.listen(owned.engine, 'after_cursor_execute', initial_read)

    def write(amount):
        try:
            store.commit(owned.owner.owner_uuid, owned.owner.generation, [bill(amount=Decimal(amount))], {'winner': amount})
            return 'committed', amount
        except ValueError:
            return 'conflict', amount

    try:
        with ThreadPoolExecutor(max_workers=2) as work:
            tasks = [work.submit(write, amount) for amount in ('1', '2')]
            results = [task.result(timeout=10) for task in tasks]
        assert sorted(item[0] for item in results) == ['committed', 'conflict']
        winning = next(amount for result, amount in results if result == 'committed')
        with owned.engine.connect() as connection:
            assert connection.scalar(select(FundingBill.amount)) == Decimal(winning)
        assert store.load() == {'winner': winning}
    finally:
        event.remove(owned.engine, 'after_cursor_execute', initial_read)


def test_funding_commit_does_not_decode_the_full_locked_risk_checkpoint(owned):
    import json

    protected = {'keep': 'x' * 700_000}
    owned.owner._mutate_recovery(lambda state: state.update(protected_risk=protected))
    decoded = []

    def decode(raw):
        decoded.append(len(raw))
        return json.loads(raw)

    engine = create_engine(owned.engine.url, json_deserializer=decode)
    try:
        store = FundingBillStore(engine, owned.store.bot_id, owner_backend_pid=owned.owner.backend_pid)
        assert store.commit(owned.owner.owner_uuid, owned.owner.generation, [bill()], {'cursor': 1}) == {'cursor': 1}
        assert not decoded or max(decoded) < 64_000
    finally:
        engine.dispose()
    state = owned.store.read()['recovery_state']
    assert state['protected_risk'] == protected and state['funding_sync'] == {'cursor': 1}
