"""The final native send fence must leave loop turns without skipping durable proof."""
import asyncio
import json
from copy import deepcopy
from decimal import Decimal
from test.integration.postgres.test_ownership import Throttler, assistant, intent, owned as owned_fixture
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_submission_admission_fairness import native as native_fixture
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from psycopg2 import OperationalError
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import DBAPIError

from hummingbot.core.bot_ownership import ORDER_PATH, OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.model.bot_state import BotState
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt, digest
from hummingbot.strategy_v2.utils.quote_guard import observe_market

owned = owned_fixture
native = native_fixture


async def send(rest, identity):
    return await rest.execute_request(
        url='https://offline.invalid' + ORDER_PATH, method=RESTMethod.POST,
        throttler_limit_id='order', is_auth_required=True,
        data=dict(clOrdId=identity, instId='BTC-USDT', side='buy', sz='0.1'))


def test_intent_and_send_read_pending_orders_with_fresh_owner_authority(owned):
    with queries(owned.engine) as admission:
        intent(owned)
    rest, transport = assistant()
    with queries(owned.engine) as placement:
        owned.loop.run_until_complete(send(rest, 'order-1'))

    for statements in (admission, placement):
        pending_reads = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')
                         and 'creation_pending IS true' in sql]
        combined = [sql for sql in pending_reads if 'public.bot_state' in sql and 'pg_locks' in sql]
        assert len(combined) == 1, pending_reads
        assert 'LIMIT' in combined[0]  # Preserve the sentinel for an over-capacity recovery ledger.
        assert not [sql for sql in pending_reads if 'orders.id AS orders_id, orders.market AS orders_market' in sql]

    transport.call.assert_awaited_once()


def test_native_order_authority_reads_only_the_risk_fields_it_uses(owned):
    intent(owned)
    recovery = dict(owned.store.read()['recovery_state'])
    recovery['unrelated_history'] = {'records': ['historical value'] * 40000}
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).values(recovery_state=recovery))

    with queries(owned.engine) as statements:
        projected = owned.owner.check_owner(new_risk=True, include_pending=True, order_authority=True)
    assert projected['pending_intents'] == [['order-1', 'okx']]
    assert 'unrelated_history' not in projected['recovery_state']
    assert len(statements) == 1 and 'pg_locks' in statements[0]
    assert owned.owner.check_owner()['recovery_state']['unrelated_history'] == recovery['unrelated_history']

    rest, transport = assistant()
    with queries(owned.engine) as statements:
        owned.loop.run_until_complete(send(rest, 'order-1'))
    [authority] = [statement for statement in statements if 'creation_pending IS true' in statement]
    assert 'jsonb_build_object' in authority
    transport.call.assert_awaited_once()


@pytest.mark.parametrize('over_capacity', [False, True])
def test_final_owner_read_blocks_pending_orders_added_after_admission(owned, over_capacity):
    from hummingbot.core.automatic_recovery import MAX_ATTEMPTS

    intent(owned)
    with owned.engine.begin() as connection:
        pending = dict(connection.execute(select(Order).where(Order.id == 'order-1')).mappings().one())
        count = MAX_ATTEMPTS if over_capacity else 1
        connection.execute(insert(Order), [{**pending, 'id': f'unknown-{i}'} for i in range(count)])

    rest, transport = assistant()
    reason = 'exceeds its bounded scope' if over_capacity else 'Unresolved order submission'
    with pytest.raises(SubmissionBlocked, match=reason):
        owned.loop.run_until_complete(send(rest, 'order-1'))
    transport.call.assert_not_awaited()
    assert owned.store.read()['recovery_state']['halt']
    with owned.manager.get_new_session() as db:
        assert db.get(SubmissionAttempt, 'order-1').state == 'prepared'


def test_send_transition_uses_one_journal_roundtrip_and_keeps_immutable_context(owned):
    intent(owned)
    with owned.manager.get_new_session() as db:
        original = dict(db.get(SubmissionAttempt, 'order-1').context)
    payload = dict(clOrdId='order-1', instId='BTC-USDT', side='buy', sz='0.1')
    with queries(owned.engine) as statements:
        owned.owner._record_send_attempt('order-1', payload, 123456, {})
    journal = [s for s in statements if 'submission_attempts' in s]
    assert len(journal) == 1, f'Send transition used {len(journal)} journal round trips'
    assert journal[0].lstrip().upper().startswith('UPDATE') and 'RETURNING' in journal[0]
    assert [s for s in statements if 'pg_locks' in s] == journal
    with owned.manager.get_new_session() as db:
        row = db.get(SubmissionAttempt, 'order-1')
        assert row.state == 'possibly_sent' and row.context == original
        assert row.payload_hash == digest(payload) and row.deadline_ms == 123456 and row.receipt is None
    assert owned.owner.sent_intents == {'order-1'}


def test_send_transition_checks_advisory_lock_after_final_authority_read(owned):
    intent(owned)
    rest, transport = assistant()
    original = owned.owner._record_send_attempt

    def lose_lock(identity, *args):
        owned.owner.connection.exec_driver_sql('SELECT pg_advisory_unlock_all()')
        owned.owner.connection.commit()
        return original(identity, *args)

    with patch.object(owned.owner, '_record_send_attempt', side_effect=lose_lock) as record:
        with pytest.raises(OwnershipLost) as error:
            owned.loop.run_until_complete(send(rest, 'order-1'))
        record.assert_called_once()
    assert error.value.reason_code == 'ownership_identity_mismatch'
    transport.call.assert_not_awaited()
    assert owned.owner.lost and not owned.owner.sent_intents
    with owned.manager.get_new_session() as db:
        row = db.get(SubmissionAttempt, 'order-1')
        assert row.state == 'prepared' and row.payload_hash is None and row.deadline_ms is None


def test_pending_transport_receipts_share_a_write_and_preserve_native_evidence(owned):
    identities = [f'receipt-{i}' for i in range(4)]
    for identity in identities:
        intent(owned, identity)
        owned.owner._record_send_attempt(identity, {'clOrdId': identity}, 123456, {})

    async def receive():
        async with owned.recorder.order_writes:
            tasks = []
            for i, identity in enumerate(identities):
                body = {'code': '0', 'data': [{'ordId': f'venue-{i}', 'sCode': '0', 'clOrdId': identity}]}
                if i == 3:
                    body = {'code': '50013', 'data': []}
                response = SimpleNamespace(status=503 if i == 3 else 200, json=AsyncMock(return_value=body))
                request = SimpleNamespace(url='https://offline.invalid' + ORDER_PATH, data=json.dumps({'clOrdId': identity}))
                tasks.append(asyncio.create_task(owned.owner.record_transport_receipt(request, response)))
            await asyncio.sleep(0)
            assert all(not task.done() for task in tasks)
        await asyncio.gather(*tasks)

    with queries(owned.engine) as statements:
        owned.loop.run_until_complete(receive())
    writes = [sql for sql in statements if sql.startswith('UPDATE submission_attempts')]
    assert len(writes) == 1
    assert [sql for sql in statements if 'pg_locks' in sql] == writes
    with owned.manager.get_new_session() as db:
        for i, identity in enumerate(identities):
            entry = db.get(SubmissionAttempt, identity)
            assert entry.state == 'possibly_sent' and entry.deadline_ms == 123456
            expected = {'http_status': 200, 'code': '0', 'order': {'ordId': f'venue-{i}', 'sCode': '0', 'clOrdId': identity}}
            assert entry.receipt == (expected if i < 3 else {'http_status': 503, 'code': '50013'})


def test_cancelled_transport_waits_until_its_received_evidence_is_durable(owned):
    intent(owned)
    response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))
    request = SimpleNamespace(url='https://offline.invalid' + ORDER_PATH, data='{"clOrdId":"order-1"}')
    owned.owner._record_send_attempt('order-1', {'clOrdId': 'order-1'}, 123456, {})

    async def receive():
        async with owned.recorder.order_writes:
            waiting = asyncio.create_task(owned.owner.record_transport_receipt(request, response))
            await asyncio.sleep(0)
            waiting.cancel()
            await asyncio.sleep(0)
            assert not waiting.done()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    owned.loop.run_until_complete(receive())
    with owned.manager.get_new_session() as db:
        assert db.get(SubmissionAttempt, 'order-1').receipt == {'http_status': 200, 'code': '0'}


def test_receipt_writer_cancelled_before_first_turn_releases_transport(owned):
    intent(owned)
    owned.owner._record_send_attempt('order-1', {'clOrdId': 'order-1'}, 123456, {})
    request = SimpleNamespace(url='https://offline.invalid' + ORDER_PATH, data='{"clOrdId":"order-1"}')
    response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))

    async def receive():
        pending = asyncio.create_task(owned.owner.record_transport_receipt(request, response))
        await asyncio.sleep(0)
        writer = owned.owner._order_receipt_writer
        writer.cancel()
        done, _ = await asyncio.wait({pending}, timeout=.2)
        if not done:
            # Unstick teardown if the writer never signals its waiting transports.
            _, _, outcome = owned.owner._order_receipts.popleft()
            outcome.set_result(None)
            await pending
        assert done
        assert pending.cancelled()
        assert owned.owner._order_receipt_writer is None

    owned.loop.run_until_complete(receive())
    with owned.manager.get_new_session() as db:
        assert db.get(SubmissionAttempt, 'order-1').receipt is None


@pytest.mark.parametrize('failure', ['missing-attempt', 'lost-owner'])
def test_receipt_batch_rejects_partial_writes_and_lost_ownership(owned, failure):
    intent(owned)
    owned.owner._record_send_attempt('order-1', {'clOrdId': 'order-1'}, 123456, {})
    if failure == 'lost-owner':
        intent(owned, 'order-2')
        owned.owner._record_send_attempt('order-2', {'clOrdId': 'order-2'}, 123456, {})

    async def receive():
        async with owned.recorder.order_writes:
            requests = []
            for identity in ('order-1', 'order-2'):
                request = SimpleNamespace(url='https://offline.invalid' + ORDER_PATH, data=json.dumps({'clOrdId': identity}))
                response = SimpleNamespace(status=200, json=AsyncMock(return_value={'code': '0'}))
                requests.append(asyncio.create_task(owned.owner.record_transport_receipt(request, response)))
            await asyncio.sleep(0)
            if failure == 'lost-owner':
                owned.owner.connection.exec_driver_sql('SELECT pg_advisory_unlock_all()')
                owned.owner.connection.commit()
        return await asyncio.gather(*requests, return_exceptions=True)

    outcomes = owned.loop.run_until_complete(receive())
    expected = OwnershipLost if failure == 'lost-owner' else SubmissionBlocked
    assert len(outcomes) == 2 and all(type(result) is expected for result in outcomes)
    assert owned.owner.lost is (failure == 'lost-owner')
    with owned.manager.get_new_session() as db:
        assert all(row.state == 'possibly_sent' and row.receipt is None for row in db.query(SubmissionAttempt))


@pytest.mark.parametrize('committed', [False, True], ids=['receipt-rolled-back', 'receipt-commit-reply-lost'])
def test_receipt_commit_failure_never_returns_an_ack_or_repeats_transport(owned, monkeypatch, committed):
    intent(owned)
    raw = owned.owner.connection.connection.driver_connection
    commit = type(raw).commit
    wrote_receipt = False

    def observe(connection, cursor, statement, *_):
        nonlocal wrote_receipt
        if connection is owned.owner.connection and statement.startswith('UPDATE submission_attempts SET receipt='):
            wrote_receipt = True

    def disconnect(connection):
        if connection is raw and wrote_receipt:
            if committed:
                commit(connection)
            connection.close()
            raise OperationalError('test lost receipt COMMIT response')
        return commit(connection)

    monkeypatch.setattr(type(raw), 'commit', disconnect)
    event.listen(owned.engine, 'after_cursor_execute', observe)
    rest, transport = assistant()
    try:
        with pytest.raises(DBAPIError):
            owned.loop.run_until_complete(send(rest, 'order-1'))
    finally:
        event.remove(owned.engine, 'after_cursor_execute', observe)
    assert wrote_receipt and owned.owner.lost and not owned.owner.transport_tasks
    transport.call.assert_awaited_once()
    with owned.manager.get_new_session() as db:
        row = db.get(SubmissionAttempt, 'order-1')
        assert row.state == 'possibly_sent'
        assert (row.receipt is not None) is committed


def test_final_send_burst_leaves_a_health_turn_between_durable_gates(owned):
    for i in range(8):
        intent(owned, 'burst-' + str(i))
    rest, transport = assistant()
    sent, probe = [], []
    original = owned.owner._record_send_attempt

    def record(identity, *args):
        original(identity, *args)
        sent.append(identity)
        if len(sent) == 1:
            asyncio.get_running_loop().call_soon(lambda: probe.append(len(sent)))

    async def run():
        await asyncio.gather(*(send(rest, 'burst-' + str(i)) for i in range(8)))
        await asyncio.sleep(0)
        assert not owned.owner.submission_tasks and not owned.owner.transport_tasks

    with patch.object(owned.owner, '_record_send_attempt', side_effect=record):
        owned.loop.run_until_complete(run())
    assert transport.call.await_count == len(sent) == 8
    assert probe == [1], f'Health callback waited behind {probe} final send gates'
    with owned.manager.get_new_session() as db:
        assert {row.state for row in db.query(SubmissionAttempt)} == {'possibly_sent'}
        assert all(row.receipt['http_status'] == 200 for row in db.query(SubmissionAttempt))


def test_stop_callback_between_final_gates_blocks_the_unsent_siblings(owned):
    for i in range(8):
        intent(owned, 'stop-' + str(i))
    rest, transport = assistant()
    original = owned.owner._record_send_attempt
    sent = []

    def record(identity, *args):
        original(identity, *args)
        sent.append(identity)
        if len(sent) == 1:
            asyncio.get_running_loop().call_soon(owned.owner.begin_shutdown)

    async def run():
        return await asyncio.gather(*(send(rest, 'stop-' + str(i)) for i in range(8)), return_exceptions=True)

    with patch.object(owned.owner, '_record_send_attempt', side_effect=record):
        results = owned.loop.run_until_complete(run())
    assert transport.call.await_count == len(sent) == 1
    assert sum(isinstance(value, SubmissionBlocked) for value in results) == 7
    with owned.manager.get_new_session() as db:
        assert db.query(SubmissionAttempt).filter_by(state='prepared').count() == 7
        assert db.query(SubmissionAttempt).filter_by(state='possibly_sent').count() == 1
    assert not owned.store.read()['recovery_state'].get('halt')


def test_cancel_queued_final_gate_cannot_mark_sent_or_call_transport(owned):
    intent(owned)
    rest, transport = assistant()

    async def run():
        await owned.owner.submission_admission.acquire()
        task = asyncio.create_task(send(rest, 'order-1'))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            transport.call.assert_not_awaited()
            assert task in owned.owner.submission_tasks
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            owned.owner.submission_admission.release()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not owned.owner.submission_tasks and not owned.owner.transport_tasks

    owned.loop.run_until_complete(run())
    assert not owned.owner.sent_intents
    with owned.manager.get_new_session() as db:
        row = db.get(SubmissionAttempt, 'order-1')
        assert row.state == 'prepared' and row.payload_hash is None and row.receipt is None


@pytest.mark.parametrize('change', ['owner', 'generation', 'state'])
def test_failed_transition_validation_leaves_the_entire_journal_row_unchanged(owned, change):
    intent(owned)
    with owned.engine.begin() as connection:
        if change == 'state':
            connection.execute(update(SubmissionAttempt).values(state='uncertain'))
        else:
            with owned.manager.get_new_session() as db:
                context = deepcopy(db.get(SubmissionAttempt, 'order-1').context)
            context['owner_uuid' if change == 'owner' else 'generation'] = 'other' if change == 'owner' else -1
            connection.execute(update(SubmissionAttempt).values(context=context))
    with owned.manager.get_new_session() as db:
        before = {c.name: getattr(db.get(SubmissionAttempt, 'order-1'), c.name) for c in SubmissionAttempt.__table__.columns}
    with pytest.raises(SubmissionBlocked):
        owned.owner._record_send_attempt('order-1', dict(clOrdId='order-1'), 123456, {})
    with owned.manager.get_new_session() as db:
        after = {c.name: getattr(db.get(SubmissionAttempt, 'order-1'), c.name) for c in SubmissionAttempt.__table__.columns}
    assert after == before and not owned.owner.sent_intents


@pytest.mark.parametrize('committed', [False, True], ids=['commit-not-applied', 'commit-reply-lost'])
def test_send_commit_failure_never_reaches_transport_or_retries_an_uncertain_send(owned, monkeypatch, committed):
    order = intent(owned)
    order._managed_before_transport = True
    raw = owned.owner.connection.connection.driver_connection
    original = type(raw).commit
    faults = []

    def fail_commit(connection):
        # Owner SELECTs use autocommit; target only the real journal transaction.
        if connection is raw and not raw.autocommit and not faults:
            faults.append(True)
            if committed:
                original(connection)
            connection.close()
            raise OperationalError('test lost journal COMMIT response')
        return original(connection)

    monkeypatch.setattr(type(raw), 'commit', fail_commit)
    rest, transport = assistant()
    with pytest.raises(DBAPIError):
        owned.loop.run_until_complete(send(rest, 'order-1'))
    assert order._managed_before_transport is False
    assert faults == [True] and owned.owner.sent_intents == {'order-1'}
    transport.call.assert_not_awaited()
    with owned.manager.get_new_session() as db:
        row = db.get(SubmissionAttempt, 'order-1')
        assert row.state == ('possibly_sent' if committed else 'prepared')
        assert (row.payload_hash is not None) is committed
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(send(rest, 'order-1'))
    transport.call.assert_not_awaited()
    assert owned.owner.lost and not owned.owner.allow_new_orders


def test_native_quote_remains_local_until_the_final_journal_write(native):
    entered, release = asyncio.Event(), asyncio.Event()
    rest, transport = assistant(Throttler(entered, release))
    rest._auth = native.market.authenticator
    order_id = 'local-before-send'
    record = native.owner._record_send_attempt
    now = native.market.current_timestamp
    book = OrderBook()
    update_id = int(now)
    book.apply_snapshot([OrderBookRow(9.9, 5, update_id)], [OrderBookRow(10, 5, update_id)], update_id)

    def outside_price(connector, order):
        return observe_market(connector, book, order.trading_pair, now=now, max_age_seconds=10,
                              max_levels=5, max_orders=20).market_mid

    native.owner.mm_order_ready_check = lambda connector, order: outside_price(connector, order) is not None

    def record_attempt(*args):
        order = native.market.in_flight_orders[order_id]
        assert order._managed_before_transport is False
        assert outside_price(native.market, order) is None
        return record(*args)

    async def run():
        with session_order_scope(native.session.id, native.session.generation):
            task = asyncio.create_task(native.market._create_order(
                TradeType.SELL, order_id, 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            order = native.market.in_flight_orders[order_id]
            assert order._managed_before_transport is True
            assert '_managed_before_transport' not in order.to_json()
            transport.call.assert_not_awaited()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
        transport.call.assert_awaited_once()

    with (patch.object(native.market._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)),
          patch.object(native.owner, '_record_send_attempt', side_effect=record_attempt) as gate):
        native.loop.run_until_complete(run())
        gate.assert_called_once()


@pytest.mark.parametrize('retirement', ['task_cancel', 'tracker_cancel', 'tracker_remove'])
@pytest.mark.parametrize('waiting_at', ['throttle', 'final_queue'])
def test_native_final_queue_resolves_proven_unsent_cancellation_and_retirement(native, retirement, waiting_at):
    entered, release = asyncio.Event(), asyncio.Event()
    rest, transport = assistant(Throttler(entered, release))

    async def run():
        with session_order_scope(native.session.id, native.session.generation):
            task = asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'cancel-at-send', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
        async with asyncio.timeout(2):
            await entered.wait()  # Intent exists; throttle still precedes the final fence.
        await native.owner.submission_admission.acquire()
        try:
            if waiting_at == 'final_queue':
                release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert task in native.owner.submission_tasks and not native.owner.sent_intents
            transport.call.assert_not_awaited()
            if retirement == 'task_cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                if retirement == 'tracker_cancel':
                    native.market._order_tracker.process_order_update(OrderUpdate(
                        trading_pair='LIFE-USDT', update_timestamp=native.market.current_timestamp,
                        new_state=OrderState.CANCELED, client_order_id='cancel-at-send'))
                else:
                    native.market.stop_tracking_order('cancel-at-send')
        finally:
            release.set()
            native.owner.submission_admission.release()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks and not native.owner.transport_tasks

    with patch.object(native.market._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
        native.loop.run_until_complete(run())
    with native.manager.get_new_session() as db:
        assert not db.get(Order, 'cancel-at-send').creation_pending
        row = db.get(SubmissionAttempt, 'cancel-at-send')
        assert row.state == ('rejected' if retirement == 'task_cancel' else 'terminal')
        assert row.payload_hash is None and row.receipt is None
    assert not native.store.read()['recovery_state'].get('halt')


def test_missing_tracker_with_a_durable_send_marker_still_requires_reconciliation(owned):
    intent(owned)
    owned.owner._record_send_attempt('order-1', dict(clOrdId='order-1'), 123456, {})
    owned.market.in_flight_orders.pop('order-1')
    rest, transport = assistant()
    with pytest.raises(SubmissionBlocked, match='reconciliation'):
        owned.loop.run_until_complete(send(rest, 'order-1'))
    transport.call.assert_not_awaited()
    assert owned.store.read()['recovery_state']['halt']
    with owned.manager.get_new_session() as db:
        assert db.get(SubmissionAttempt, 'order-1').state == 'possibly_sent'
