"""The final native send fence must leave loop turns without skipping durable proof."""
import asyncio
from copy import deepcopy
from decimal import Decimal
from test.integration.postgres.test_ownership import Throttler, assistant, intent, owned as owned_fixture
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_submission_admission_fairness import native as native_fixture
from unittest.mock import AsyncMock, patch

import pytest
from psycopg2 import OperationalError
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from hummingbot.core.bot_ownership import ORDER_PATH, OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt, digest

owned = owned_fixture
native = native_fixture


async def send(rest, identity):
    return await rest.execute_request(
        url='https://offline.invalid' + ORDER_PATH, method=RESTMethod.POST,
        throttler_limit_id='order', is_auth_required=True,
        data=dict(clOrdId=identity, instId='BTC-USDT', side='buy', sz='0.1'))


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
    intent(owned)
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
