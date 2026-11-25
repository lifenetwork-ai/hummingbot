"""An unsent intent may wait for ownership contention, never uncertain effects."""
import time
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_ownership import assistant, intent, owned  # noqa: F401
from threading import Event

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import MarketEvent, MarketOrderFailureEvent
from hummingbot.core.order_submission_guard import IntentAdmissionBusy, MarketReadinessBlocked
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill


@pytest.mark.parametrize('roll_back', [False, True])
@pytest.mark.parametrize('reply_observed', [False, True])
def test_contention_evidence_identifies_publication_until_transaction_ends(owned, roll_back, reply_observed):  # noqa: F811
    from hummingbot.model.ownership_lock_observation import observe_ownership_locks

    locked, release = Event(), Event()

    def pause_publication(connection, cursor, statement, parameters, context, executemany):
        if 'WITH publication_target' in statement:
            locked.set()
            assert release.wait(3)
            if roll_back:
                raise RuntimeError('publication interrupted')

    if reply_observed:
        observe_ownership_locks(owned.engine)
    event.listen(owned.engine, 'after_cursor_execute', pause_publication)
    if not reply_observed:
        observe_ownership_locks(owned.engine)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            publishing = executor.submit(
                owned.store.publish, owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                version=owned.owner.version, status={'running': False}, save_balances=False)
            try:
                assert locked.wait(3)
                with pytest.raises(IntentAdmissionBusy) as busy:
                    intent(owned)
                evidence = busy.value.ownership_evidence
                assert evidence['ownership_check_started_at_ns'] <= evidence['ownership_check_finished_at_ns']
                if reply_observed:
                    assert evidence['ownership_writer_overlap'] == 'publication'
                    assert evidence['ownership_writer_observed_us'] > 0
                    assert 'ownership_writer_in_flight' not in evidence
                else:
                    assert evidence['ownership_writer_overlap'] == 'unobserved'
                    assert evidence['ownership_writer_in_flight'] == 'publication'
                assert owned.manager.persistence_healthy
            finally:
                release.set()
            if roll_back:
                with pytest.raises(RuntimeError, match='publication interrupted'):
                    publishing.result(timeout=3)
            else:
                assert publishing.result(timeout=3)
    finally:
        event.remove(owned.engine, 'after_cursor_execute', pause_publication)

    owned.owner._intent_admission_retry_at = 0
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(IntentAdmissionBusy) as busy:
            intent(owned, 'unobserved-holder')
        assert busy.value.ownership_evidence['ownership_writer_overlap'] == 'unobserved'
        assert 'ownership_writer_observed_us' not in busy.value.ownership_evidence
        assert 'ownership_writer_in_flight' not in busy.value.ownership_evidence
    assert owned.manager.persistence_healthy


def test_unsent_intent_row_contention_keeps_persistence_healthy_and_next_admission_works(owned):  # noqa: F811
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(Exception) as failure:
            intent(owned)
    order = owned.market.in_flight_orders['order-1']
    assert isinstance(failure.value, MarketReadinessBlocked), 'Known prewrite lock contention is temporary readiness'
    assert owned.manager.persistence_healthy, 'A proven unsent rollback must not poison the recorder'
    assert not owned.owner.order_failed(owned.market, order, failure.value)
    order.current_state = OrderState.FAILED
    failure_event = MarketOrderFailureEvent(1700000000, order.client_order_id, order.order_type)
    owned.recorder._did_fail_order(MarketEvent.OrderFailure.value, owned.market, failure_event)
    with owned.manager.get_new_session() as session:
        for model in (Order, OrderStatus, SubmissionAttempt, TradeFill, MarketState):
            assert session.query(model).count() == 0
    assert not owned.owner.active_intents and not owned.owner.sent_intents
    assert not owned.store.read()['recovery_state'].get('halt')
    owned.owner._intent_admission_retry_at = 0
    order2 = intent(owned, 'order-2')
    rest, transport = assistant()
    owned.loop.run_until_complete(rest.execute_request(
        url='https://offline.invalid/api/v5/trade/order', method=RESTMethod.POST,
        throttler_limit_id='order', is_auth_required=True,
        data={'clOrdId': order2.client_order_id, 'instId': 'BTC-USDT', 'side': 'buy', 'sz': '0.1'}))
    assert transport.call.await_count == 1


def test_admission_backoff_avoids_repeating_the_fence_while_the_row_is_busy(owned):  # noqa: F811
    statements = []

    def observed(connection, cursor, statement, *args):
        if 'FOR SHARE' in statement:
            statements.append(statement)

    event.listen(owned.engine, 'before_cursor_execute', observed)
    try:
        with owned.engine.begin() as blocker:
            blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            with pytest.raises(MarketReadinessBlocked):
                intent(owned)
            owned.owner._intent_admission_retry_at = time.monotonic() + 30
            for index in range(3):
                with pytest.raises(MarketReadinessBlocked):
                    intent(owned, 'waiting-' + str(index))
        assert len(statements) == 1 and 'NOWAIT' in statements[0]
        assert owned.manager.persistence_healthy and not owned.owner.active_intents
    finally:
        event.remove(owned.engine, 'before_cursor_execute', observed)


def test_lock_timeout_after_the_admission_fence_still_latches_persistence_failure(owned):  # noqa: F811
    intent(owned, 'existing-order')
    with owned.engine.begin() as blocker:
        blocker.execute(text('LOCK TABLE order_statuses IN SHARE MODE'))
        with pytest.raises(OperationalError) as failure:
            intent(owned)
    assert failure.value.orig.pgcode == '55P03'
    assert not owned.manager.persistence_healthy
    order = owned.market.in_flight_orders['order-1']
    owned.owner.order_failed(owned.market, order, failure.value)
    assert owned.store.read()['recovery_state'].get('halt')


def test_duplicate_identity_does_not_become_a_transient_busy_admission(owned):  # noqa: F811
    intent(owned)
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(RuntimeError, match='identity already exists'):
            intent(owned)
    assert not owned.manager.persistence_healthy


@pytest.mark.parametrize('identities', ['new-a', ('new-a', 'new-b')])
def test_worker_checks_existing_ids_and_takes_the_owner_fence_in_one_read(owned, identities):  # noqa: F811
    reads = []

    def observe(connection, cursor, statement, *args):
        reads.append(' '.join(statement.split()))

    with owned.engine.connect():
        pass  # Exclude initial connection setup from the recorder statement count.
    event.listen(owned.engine, 'before_cursor_execute', observe)
    try:
        with owned.manager.order_intent_transaction(identities, worker_owner=owned.owner):
            pass
    finally:
        event.remove(owned.engine, 'before_cursor_execute', observe)
    checks = [sql for sql in reads if 'FROM orders' in sql or 'FOR SHARE NOWAIT' in sql]
    assert len(checks) == 1
    assert 'FROM orders' in checks[0] and 'FOR SHARE NOWAIT' in checks[0] and 'pg_locks' in checks[0]


@pytest.mark.parametrize('identities', ['existing', ('new', 'existing')])
def test_worker_duplicate_identity_stays_fatal_even_when_owner_row_is_locked(owned, identities):  # noqa: F811
    intent(owned, 'existing')
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(RuntimeError, match='identity already exists'):
            with owned.manager.order_intent_transaction(identities, worker_owner=owned.owner):
                pytest.fail('Duplicate identity reached the staging phase')
    assert not owned.manager.persistence_healthy


@pytest.mark.parametrize('code,invalidated', [('55P03', True), ('57014', False), ('08006', False)])
def test_unrecognized_or_lost_prewrite_connection_stays_fatal(owned, code, invalidated):  # noqa: F811
    from unittest.mock import patch
    original = RuntimeError('synthetic database response')
    original.pgcode = code
    error = OperationalError('ownership-fence', {}, original, connection_invalidated=invalidated)
    with patch.object(owned.owner, 'fence_session', side_effect=error):
        with pytest.raises(OperationalError):
            intent(owned)
    assert not owned.manager.persistence_healthy


def test_previously_unhealthy_recorder_cannot_be_reopened_by_busy_handling(owned):  # noqa: F811
    owned.manager.persistence_healthy = False
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(RuntimeError, match='Persistence failed'):
            intent(owned)
    assert not owned.manager.persistence_healthy


def test_failed_prewrite_rollback_cannot_be_reported_as_temporary_contention(owned):  # noqa: F811
    target = []

    def capture(connection, cursor, statement, *args):
        if 'FOR SHARE' in statement and 'NOWAIT' in statement:
            target.append(connection)

    def broken_rollback(connection):
        if target and connection is target[-1]:
            raise OSError('rollback acknowledgment unavailable')

    event.listen(owned.engine, 'before_cursor_execute', capture)
    event.listen(owned.engine, 'rollback', broken_rollback)
    try:
        with owned.engine.begin() as blocker:
            blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            with pytest.raises(OSError, match='rollback acknowledgment'):
                intent(owned)
    finally:
        event.remove(owned.engine, 'before_cursor_execute', capture)
        event.remove(owned.engine, 'rollback', broken_rollback)
    assert target and not owned.manager.persistence_healthy
