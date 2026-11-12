"""An exact durable venue rejection can resolve a lost local failure callback."""
import asyncio
import time
from test.integration.postgres.test_automatic_recovery import (
    managed_native as managed_native_fixture,
    owned as owned_fixture,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, update

from hummingbot.connector.okx_order_status import OKXOrderNotFoundError
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt

managed_native = managed_native_fixture
owned = owned_fixture


def receipt(identity='auto-order', code='50037'):
    return dict(http_status=200, code='1', order=dict(clOrdId=identity, ordId='', sCode=code))


@pytest.mark.parametrize('code', ['50036', '50037'])
def test_cold_recovery_consumes_only_correlated_expired_receipt_atomically(managed_native, code):
    context, exchange, recorder, transport = managed_native
    proof = receipt(code=code)
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=proof))
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    recovery = context.owner.automatic_recovery
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=AssertionError('No REST needed'))) as read:
        context.loop.run_until_complete(recovery.restore(core))
        context.loop.run_until_complete(recovery.restore(core))
    with context.manager.get_new_session() as db:
        order = db.get(Order, 'auto-order')
        assert not order.creation_pending and order.exchange_order_id is None
        assert order.last_status == 'OrderFailure'
        attempt = db.get(SubmissionAttempt, 'auto-order')
        assert attempt.state == 'terminal' and attempt.receipt == proof
    assert 'auto-order' not in exchange.in_flight_orders
    assert 'auto-order' not in context.owner.active_intents
    assert 'auto-order' not in context.owner.sent_intents
    assert not recovery.bootstrap_orders
    assert transport.call.await_count == 1
    read.assert_not_awaited()
    assert not context.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('change', ['identity', 'exchange_id', 'http', 'busy', 'missing', 'changed'])
def test_recorder_rejects_insufficient_or_stale_expiry_receipts_without_mutation(managed_native, change):
    context, exchange, recorder, _ = managed_native
    proof = receipt()
    if change == 'identity':
        proof['order']['clOrdId'] = 'foreign'
    elif change == 'exchange_id':
        proof['order']['ordId'] = 'venue-order'
    elif change == 'http':
        proof['http_status'] = 503
    elif change == 'busy':
        proof['order']['sCode'] = '50013'
    elif change == 'missing':
        del proof['order']
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=proof))
    supplied = receipt() if change == 'changed' else proof
    if change == 'changed':
        supplied['code'] = '0'
    with pytest.raises(ValueError):
        recorder.reject_unsent_order(exchange, 'auto-order', require_existing=True, expiry_receipt=supplied)
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'auto-order').creation_pending
        assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
    assert 'auto-order' in exchange.in_flight_orders


def test_bootstrap_not_found_escalates_without_settling_or_resending(managed_native):
    context, exchange, recorder, transport = managed_native
    recovery = context.owner.automatic_recovery
    recovery.status['started_at'] = time.time() - 301
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))), \
            patch('hummingbot.core.automatic_recovery.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            context.loop.run_until_complete(recovery.restore(core))
    state = context.store.read()['recovery_state']['automatic_recovery']
    assert state['operator_required'] is True
    assert state['reason_code'] == 'order_not_found_inconclusive'
    assert state['order_id'] == 'auto-order'
    assert state['phase'] == 'bootstrapping_recovery'
    with context.engine.connect() as db:
        assert db.execute(select(SubmissionAttempt.state)).scalar_one() == 'uncertain'
    assert 'auto-order' in exchange.in_flight_orders
    assert transport.call.await_count == 1


def test_expired_rejection_commit_failure_rolls_back_intent_tracker_and_audit(managed_native):
    from hummingbot.model.admin_access import AdminAccessStore, AdminAuditEvent
    from hummingbot.model.market_state import MarketState
    context, exchange, recorder, _ = managed_native
    proof = receipt()
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=proof))
    with context.manager.get_new_session() as db:
        before = db.query(MarketState).filter_by(config_file_path='test.yml', market='okx').one().saved_state
    original = AdminAccessStore.audit

    def fail_after_insert(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError('transaction interrupted')

    with patch.object(AdminAccessStore, 'audit', fail_after_insert), pytest.raises(OSError):
        recorder.reject_unsent_order(exchange, 'auto-order', require_existing=True, expiry_receipt=proof)
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'auto-order').creation_pending
        assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
        assert db.query(MarketState).filter_by(config_file_path='test.yml', market='okx').one().saved_state == before
        assert db.query(AdminAuditEvent).filter_by(action='automatic_recovery_order_rejected').count() == 0
    assert not context.manager.persistence_healthy


def test_expired_rejection_is_idempotent_before_tracker_callback(managed_native):
    from hummingbot.model.admin_access import AdminAuditEvent
    context, exchange, recorder, _ = managed_native
    proof = receipt()
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=proof))
    recorder.reject_unsent_order(exchange, 'auto-order', require_existing=True, expiry_receipt=proof)
    recorder.reject_unsent_order(exchange, 'auto-order', require_existing=True, expiry_receipt=proof)
    with context.manager.get_new_session() as db:
        assert not db.get(Order, 'auto-order').creation_pending
        assert db.get(SubmissionAttempt, 'auto-order').state == 'rejected'
        assert db.query(AdminAuditEvent).filter_by(action='automatic_recovery_order_rejected').count() == 1


def test_successor_replays_expiry_receipt_without_second_send_or_financial_change(managed_native):
    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.core.order_submission_guard import install_guard
    from hummingbot.model.runtime_journal import RuntimeCheckpoint, financial_hash
    context, exchange, recorder, transport = managed_native
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=receipt()))
    with context.manager.get_new_session() as db:
        before = financial_hash(db), db.get(RuntimeCheckpoint, 'test.yml').payload
    context.owner.release()
    successor = BotOwnership(context.engine, 'hummingbot')
    assert successor.try_acquire()
    try:
        install_guard(successor)
        successor.prepare(1, 'test.yml')
        successor.connectors = {'okx': exchange}
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=AssertionError('No REST needed'))):
            context.loop.run_until_complete(successor.automatic_recovery.restore(
                SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)))
        assert not context.store.read()['recovery_state'].get('halt')
        with context.manager.get_new_session() as db:
            assert (financial_hash(db), db.get(RuntimeCheckpoint, 'test.yml').payload) == before
            assert not db.get(Order, 'auto-order').creation_pending
        assert transport.call.await_count == 1
    finally:
        successor.release()
        install_guard(context.owner)


@pytest.mark.parametrize('conflict', ['acknowledged', 'fill'])
def test_positive_expiry_cannot_override_conflicting_durable_financial_evidence(managed_native, conflict):
    from decimal import Decimal

    from hummingbot.model.trade_fill import TradeFill
    context, exchange, recorder, _ = managed_native
    proof = receipt()
    with context.engine.begin() as db:
        db.execute(update(SubmissionAttempt).values(receipt=proof))
        if conflict == 'acknowledged':
            db.execute(update(Order).values(exchange_order_id='venue-order'))
        else:
            db.execute(TradeFill.__table__.insert().values(
                config_file_path='test.yml', strategy='pure_market_making', market='okx', symbol='BTC-USDT',
                base_asset='BTC', quote_asset='USDT', timestamp=1700000000000, order_id='auto-order',
                trade_type='BUY', order_type='LIMIT_MAKER', price=Decimal('100'), amount=Decimal('.01'),
                trade_fee={}, exchange_trade_id='fill', position='NIL'))
    with pytest.raises(ValueError):
        recorder.reject_unsent_order(exchange, 'auto-order', require_existing=True, expiry_receipt=proof)
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'auto-order').creation_pending
        assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
        assert db.query(TradeFill).count() == (1 if conflict == 'fill' else 0)


@pytest.mark.parametrize('code', ['50036', '50037'])
def test_native_transport_expiry_never_enters_unknown_outcome_recovery(owned, code):
    fixture = managed_native_fixture.__wrapped__(owned, expiry_code=code)
    context, exchange, recorder, transport = next(fixture)
    try:
        assert 'auto-order' not in exchange.in_flight_orders
        assert not context.owner.automatic_recovery.active
        assert 'auto-order' not in context.owner.sent_intents
        assert 'auto-order' not in context.owner.active_intents
        assert not context.store.read()['recovery_state'].get('halt')
        with context.manager.get_new_session() as db:
            assert not db.get(Order, 'auto-order').creation_pending
            saved = db.get(SubmissionAttempt, 'auto-order')
            assert saved.state == 'terminal' and saved.receipt == receipt(code=code)
        assert transport.call.await_count == 1
    finally:
        fixture.close()
