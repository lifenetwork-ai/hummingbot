"""Expired unknown sends need an explicit, account-bound recovery review."""
import asyncio
from random import Random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering
from test.integration.postgres.test_recovery_terminal_metadata import seed_terminal_work

import pytest
from sqlalchemy import select, update

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import RuntimeCheckpoint, SubmissionAttempt, digest

recovering = _recovering


def seed_uncertain(store, *, existing_binding=False, api_key='offline'):
    identity, = seed_terminal_work(store, count=1, existing_binding=existing_binding, api_key=api_key)
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='spot', profile_id='follow_market',
                                                           duration_seconds=600), rng=Random(1), session_id='old-session')
    session.status = 'stopped'
    with store.engine.begin() as db:
        state = dict(db.scalar(select(BotState.recovery_state)))
        for key in ('halt', 'reason', 'halt_diagnostic'):
            state.pop(key, None)
        state['automatic_recovery'] = dict(phase='settled_stopped', order_id=identity, quarantined_orders=1)
        db.execute(update(BotState).values(recovery_state=state, mm_session=session.model_dump(mode='json')))
        created = db.scalar(select(Order.creation_timestamp).where(Order.id == identity))
        db.execute(update(Order).where(Order.id == identity).values(
            last_status='PendingBuy', creation_pending=True, exchange_order_id=None, position_applied=False))
        db.execute(OrderStatus.__table__.insert().values(order_id=identity, timestamp=created, status='PendingBuy'))
        db.execute(update(SubmissionAttempt).values(
            state='uncertain', deadline_ms=created + 5000, receipt=dict(code='50013', http_status=429),
            payload_hash=digest(dict(clOrdId=identity, instId='BTC-USDT', tdMode='cash', ordType='post_only',
                                     side='buy', sz='1', px='100'))))
        tracker = db.scalar(select(MarketState.saved_state))
        tracker[identity].update(last_state=str(OrderState.PENDING_CANCEL.value), exchange_order_id=None)
        db.execute(update(MarketState).values(saved_state=tracker))
    return identity


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
