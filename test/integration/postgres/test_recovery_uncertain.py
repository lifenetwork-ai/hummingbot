"""Expired unknown sends need an explicit, account-bound recovery review."""
import asyncio
from random import Random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering
from test.integration.postgres.test_recovery_terminal_metadata import seed_terminal_work

import pytest
from sqlalchemy import null, select, update

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.market_state import MarketState
from hummingbot.model.mm_event import record_transition
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import RuntimeCheckpoint, SubmissionAttempt, digest

recovering = _recovering


def seed_uncertain(store, *, existing_binding=False, api_key='offline', count=1, sell_last=False, lost_receipt=False):
    identities = seed_terminal_work(store, count=count, existing_binding=existing_binding, api_key=api_key)
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='spot', profile_id='follow_market',
                                                           duration_seconds=600), rng=Random(1), session_id='old-session')
    session.status = 'stopped'
    with store.engine.begin() as db:
        state = dict(db.scalar(select(BotState.recovery_state)))
        for key in ('halt', 'reason', 'halt_diagnostic'):
            state.pop(key, None)
        state['automatic_recovery'] = dict(phase='settled_stopped', order_id=identities[0], quarantined_orders=count)
        db.execute(update(BotState).values(recovery_state=state, mm_session=session.model_dump(mode='json')))
        tracker = db.scalar(select(MarketState.saved_state))
        for identity in identities:
            created = db.scalar(select(Order.creation_timestamp).where(Order.id == identity))
            sell = sell_last and identity == identities[-1]
            side, status = ('SELL', 'PendingSell') if sell else ('BUY', 'PendingBuy')
            context = db.scalar(select(SubmissionAttempt.context).where(SubmissionAttempt.order_id == identity))
            context['executor']['side'] = 2 if sell else 1
            context['order']['trade_type'] = side
            db.execute(update(Order).where(Order.id == identity).values(
                last_status=status, creation_pending=True, exchange_order_id=None, position_applied=False))
            db.execute(OrderStatus.__table__.insert().values(order_id=identity, timestamp=created, status=status))
            db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity).values(
                state='uncertain', context=context, deadline_ms=created + 5000,
                receipt=null() if sell and lost_receipt else dict(code='50013', http_status=429),
                payload_hash=digest(dict(clOrdId=identity, instId='BTC-USDT', tdMode='cash', ordType='post_only',
                                         side=side.lower(), sz='1', px='100'))))
            tracker[identity].update(trade_type=side, last_state=str(OrderState.PENDING_CANCEL.value), exchange_order_id=None)
        db.execute(update(MarketState).values(saved_state=tracker))
    return identities[0] if count == 1 else identities


def test_uncertain_batch_requires_complete_exact_set(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, _ = recovering
    identities = seed_uncertain(store, count=2)
    with pytest.raises(RecoveryBlocked, match='recovery_uncertain_order_scope'):
        store.operation_snapshot({'uncertain_order_id': identities[0]})
    with pytest.raises(RecoveryBlocked, match='recovery_uncertain_order_scope'):
        store.operation_snapshot({'uncertain_order_ids': [identities[0], 'unexpected']})
    snapshot = store.operation_snapshot({'uncertain_order_ids': list(reversed(identities))})
    assert [item['id'] for item in snapshot['uncertain_orders']] == identities
    with store.engine.begin() as db:
        db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identities[-1]).values(
            receipt=dict(code='50011', http_status=429)))
    with pytest.raises(RecoveryBlocked, match='recovery_uncertain_order_scope'):
        store.operation_snapshot({'uncertain_order_ids': identities})


@pytest.mark.parametrize('fault', [None, 'current_step', 'different_session', 'future_generation', 'expired_step', 'missing_step', 'fixed_sell'])
def test_uncertain_batch_from_earlier_random_step_requires_its_original_send_window(recovering, monkeypatch, fault):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_uncertain(store, count=2, sell_last=fault == 'fixed_sell')
    with store.engine.begin() as db:
        scope = db.scalar(select(BotState.mm_session))
        created = db.scalar(select(Order.creation_timestamp).where(Order.id == identities[0]))
        scope.update(started_at=created / 1000 - 1, status='running', anchors={})
        if fault != 'fixed_sell':
            scope['request'].update(selection='random', profile_id=None, step_duration_seconds=30,
                                    profile_ids=['follow_market', 'sideways'])
        original = {**scope, 'step_expires_at': scope['started_at'] + (4 if fault == 'expired_step' else 30)}
        if fault != 'missing_step':
            record_transition(db, store.bot_id, {**original, 'status': 'preparing'}, original, 0)
        step = 0 if fault in ('current_step', 'fixed_sell') else 2
        scope.update(generation=step, step_index=step, step_expires_at=scope['started_at'] + (step + 1) * 30)
        if fault == 'different_session':
            scope['id'] = 'unrelated-session'
        if fault == 'future_generation':
            for identity, context in db.execute(select(SubmissionAttempt.order_id, SubmissionAttempt.context)):
                context['session'][1] = context['executor']['mm_generation'] = 4
                db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity).values(context=context))
        db.execute(update(BotState).values(mm_session=scope, desired_state='running'))
    stopped = store.state.request_mm_session(action='stop', expected_version=0, current_session_id=scope['id'])
    assert stopped['generation'] == step + 1
    store.state.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                                  session_id=scope['id'], generation=stopped['generation'],
                                  change=lambda session, _: setattr(session, 'status', 'stopped'))
    if fault not in (None, 'current_step', 'fixed_sell'):
        with pytest.raises(RecoveryBlocked, match='recovery_uncertain_order_scope'):
            store.operation_snapshot({'uncertain_order_ids': identities})
    else:
        snapshot = store.operation_snapshot({'uncertain_order_ids': identities})
        assert [item['id'] for item in snapshot['uncertain_orders']] == identities


@pytest.mark.parametrize('fault', ['wrong_id', 'changed_ack', 'changed_payload', 'missing_checkpoint', 'active_runtime', 'risk_stop'])
def test_unproven_uncertain_order_stays_blocked(recovering, monkeypatch, fault):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identity = seed_uncertain(store)
    with store.engine.begin() as db:
        if fault == 'changed_ack':
            db.execute(update(Order).where(Order.id == identity).values(exchange_order_id='42'))
        if fault == 'changed_payload':
            db.execute(update(SubmissionAttempt).values(payload_hash='0' * 64))
        if fault == 'missing_checkpoint':
            db.execute(RuntimeCheckpoint.__table__.delete())
    if fault == 'active_runtime':
        owner._mutate_recovery(lambda state: state.update(runtime_active=True))
    elif fault == 'risk_stop':
        owner.require_recovery('drawdown_global_limit')
    payload = dict(expected_version=0, uncertain_order_id='another' if fault == 'wrong_id' else identity)
    with pytest.raises(AccessDenied):
        store.request('recovery_check', 'nonadmin', payload, Principal('basic:reader', 'basic', 'read'), 'req')
    if fault in ('active_runtime', 'risk_stop'):
        with pytest.raises(ControlConflict):
            store.request('recovery_check', 'blocked', payload, ADMIN, 'req')
    else:
        with pytest.raises(RecoveryBlocked, match='recovery_uncertain_order_scope'):
            store.operation_snapshot(payload)
