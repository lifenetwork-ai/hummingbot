"""Local proof and atomic retirement of reviewed, expired OKX Spot orders."""
from sqlalchemy import select, update

from hummingbot.connector.okx_order_status import OKXPriceLimitOrderRejection, order_rejection_reason
from hummingbot.core.automatic_recovery import eligible
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.recovery import (
    RecoveryBlocked,
    decimal,
    supports_failed_start_order_review,
    supports_halted_order_review,
)
from hummingbot.model.market_state import MarketState
from hummingbot.model.mm_event import MMEvent
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt, digest
from hummingbot.model.trade_fill import TradeFill

ORDER_HISTORY_AGE_MS = 6 * 86_400_000
SETTLEMENT_GRACE_MS = 60_000
PENDING_STATUS = {'BUY': 'PendingBuy', 'SELL': 'PendingSell'}


def _require(condition):
    if not condition:
        raise RecoveryBlocked('recovery_uncertain_order_scope')


def _matches_stopped_session(db, state, original, created, deadline, *, scope=None):
    session_id, generation = original
    scope = (state['mm_session'] or {}) if scope is None else scope
    current = scope.get('generation')
    if (scope.get('status') != 'stopped' or scope.get('id') != session_id or not session_id
            or type(generation) is not int or type(current) is not int or not 0 <= generation <= current):
        return False
    if generation == current:
        return True
    request = scope.get('request') or {}
    seconds, last_step = request.get('step_duration_seconds'), scope.get('step_index')
    started = scope.get('started_at')
    if type(last_step) is not int or type(started) not in (int, float):
        return False
    # Stop advances the generation without changing the step. A restart can
    # also skip elapsed steps, so look up the original activated generation.
    step = db.scalar(select(MMEvent.payload).where(
        MMEvent.bot_id == state['bot_id'], MMEvent.session_id == session_id, MMEvent.event_type == 'step_started',
        MMEvent.generation == generation).order_by(MMEvent.id.desc()).limit(1))
    if not step or step.get('status') != 'running' or step.get('started_at') != started:
        return False
    index, ended_at = step.get('step_index'), step.get('step_expires_at')
    start_ms = decimal(started) * 1000
    if request.get('selection') == 'fixed':
        return (generation == 0 and last_step == index == 0 and created >= start_ms
                and (ended_at is None or type(ended_at) in (int, float) and deadline <= decimal(ended_at) * 1000))
    if request.get('selection') != 'random' or type(seconds) is not int or seconds <= 0:
        return False
    if type(index) is not int or not 0 <= index <= last_step or type(ended_at) not in (int, float):
        return False
    step_ms = seconds * 1000
    return (start_ms + index * step_ms <= created
            and deadline <= min(decimal(ended_at) * 1000, start_ms + (index + 1) * step_ms))


def _previous_stopped_session(db, state, original, deadline):
    session_id, generation = original
    current_id = state['mm_session']['id']
    _require(session_id != current_id and type(generation) is int)

    def event(session, kind):
        return db.execute(select(MMEvent.id, MMEvent.generation, MMEvent.payload, MMEvent.created_at).where(
            MMEvent.bot_id == state['bot_id'], MMEvent.session_id == session, MMEvent.event_type == kind
        ).order_by(MMEvent.id.desc()).limit(1)).one_or_none()

    replacement = event(current_id, 'session_created')
    _require(replacement is not None and replacement.payload.get('previous_session_id') == session_id
             and event(current_id, 'step_started') is None)
    created, stopped = event(session_id, 'session_created'), event(session_id, 'session_stopped')
    _require(created is not None and stopped is not None and created.id < stopped.id < replacement.id
             and 0 <= generation < stopped.generation and deadline <= int(stopped.created_at.timestamp() * 1000))
    return {**stopped.payload, 'id': session_id, 'generation': stopped.generation,
            'request': created.payload['configuration']['request']}


def candidates(db, state, bindings, checkpoint, anchor, identities):
    try:
        pending = db.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(len(identities) + 1)).scalars().all()
        _require(set(pending) == set(identities))
        tracker_rows = db.execute(select(MarketState).limit(101)).mappings().all()
        _require(len(tracker_rows) <= 100)
        live = [(row, key, value) for row in tracker_rows for key, value in row['saved_state'].items()
                if not InFlightOrder.from_json(value).is_done]
        _require(len(live) == len(identities) and {key for _, key, _ in live} == set(identities))
        trackers = {key: (row, raw) for row, key, raw in live}
        for identity in identities:
            _require(sum(identity in row['saved_state'] for row in tracker_rows) == 1)
        failed_start = supports_failed_start_order_review(state, identities)
        return [_candidate(db, state, bindings, checkpoint, anchor, identity, *trackers[identity], failed_start=failed_start)
                for identity in identities]
    except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
        raise RecoveryBlocked('recovery_uncertain_order_scope') from None


def _candidate(db, state, bindings, checkpoint, anchor, identity, tracker_row, raw, *, failed_start):
    order = db.execute(select(Order).where(Order.id == identity)).mappings().one_or_none()
    attempt = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().one_or_none()
    _require(order is not None and attempt is not None and attempt['state'] == 'uncertain')
    context, receipt = attempt['context'], attempt['receipt']
    _require(eligible(context) and context.get('bot_id') == state['bot_id']
             and context.get('config_file_path') == order['config_file_path'] == state['config_file_path']
             and context['environment'] == checkpoint['environment']
             and context['executor']['connector_name'] == order['market'] == 'okx')
    active = [{k: row[k] for k in ('connector', 'environment', 'account_id', 'version')}
              for row in bindings if row['state'] == 'active']
    _require(sorted(context['bindings'], key=digest) == sorted(active, key=digest)
             and context['credential_fingerprint'] == checkpoint['credential_fingerprints']['okx'])
    original = InFlightOrder.from_json(context['order'])
    side = original.trade_type.name
    _require(side in PENDING_STATUS and order['last_status'] == PENDING_STATUS[side] and order['exchange_order_id'] is None
             and order['order_type'] == 'LIMIT_MAKER' and order['position'] == 'NIL'
             and not order['position_applied'] and not order['cleanup_operation_id'])
    price_rejected = order_rejection_reason(receipt, identity) == OKXPriceLimitOrderRejection.reason_code
    if supports_halted_order_review(state, (identity,)):
        _require(side == 'SELL' and receipt is None)
    statuses = (200, 429, 500, 503) if side == 'SELL' else (429, 500, 503)
    _require(price_rejected or (side == 'SELL' and receipt is None) or (isinstance(receipt, dict)
             and receipt.get('code') == '50013' and receipt.get('http_status') in statuses and not receipt.get('order')))
    created, deadline = order['creation_timestamp'], attempt['deadline_ms']
    now = int(state['database_now'].timestamp() * 1000)
    # The authenticated journaled deadline caps when this POST can first be
    # processed. Follow-up financial proof must cover its entire lifetime.
    _require(type(deadline) is int and anchor['verified_at_ms'] < created <= deadline
             and created <= order['last_update_timestamp'] <= now
             and deadline - created <= 60_000)
    session = None
    if failed_start:
        _require(side == 'SELL' and price_rejected)
        session = _previous_stopped_session(db, state, context['session'], deadline)
    _require(_matches_stopped_session(db, state, context['session'], created, deadline, scope=session))
    if now - deadline < SETTLEMENT_GRACE_MS:
        raise RecoveryBlocked('recovery_uncertain_order_wait')
    if now - created >= ORDER_HISTORY_AGE_MS:
        raise RecoveryBlocked('recovery_history_window_exceeded')
    amount, price = decimal(order['amount']), decimal(order['price'])
    pair = order['symbol']
    _require(amount > 0 and price > 0 and pair == f"{order['base_asset']}-{order['quote_asset']}")
    _require(attempt['payload_hash'] == digest(dict(
        clOrdId=identity, instId=pair, tdMode='cash', ordType='post_only', side=side.lower(),
        sz=str(original.amount), px=f"{original.price:f}")))
    _require(tracker_row['config_file_path'] == state['config_file_path']
             and tracker_row['market'] == 'okx')
    for native in (original, InFlightOrder.from_json(raw)):
        _require(native.client_order_id == identity and native.exchange_order_id is None
                 and native.trading_pair == pair and native.trade_type.name == side and native.position.name == 'NIL'
                 and native.order_type.name == 'LIMIT_MAKER'
                 and native.current_state in (OrderState.PENDING_CREATE, OrderState.PENDING_CANCEL)
                 and native.amount == amount and native.price == price
                 and not native.executed_amount_base and not native.executed_amount_quote and not native.order_fills)
    _require(not db.execute(select(TradeFill.order_id).where(TradeFill.order_id == identity).limit(1)).first())
    timeline = db.execute(select(OrderStatus.status).where(OrderStatus.order_id == identity).limit(2)).scalars().all()
    _require(timeline == [PENDING_STATUS[side]])
    return dict(id=identity, pair=pair, side=side, amount=str(amount), price=str(price), created_ms=created,
                deadline_ms=deadline, market_state_id=tracker_row['id'], tracker_digest=digest(raw),
                credential_fingerprint=context['credential_fingerprint'])


def change_for(candidate):
    return {**{key: candidate[key] for key in ('id', 'pair', 'side', 'amount', 'price', 'deadline_ms')},
            'connector': 'okx', 'before': PENDING_STATUS[candidate['side']], 'after': 'OrderFailure',
            'evidence': 'expired_submission_and_reconciled_exchange'}


def apply_change(db, candidate, timestamp):
    identity, tracker_id = candidate['id'], candidate['market_state_id']
    saved = dict(db.scalar(select(MarketState.saved_state).where(MarketState.id == tracker_id)))
    if digest(saved.get(identity)) != candidate['tracker_digest']:
        raise RecoveryBlocked('recovery_state_changed')
    saved.pop(identity)
    order = db.execute(update(Order).where(Order.id == identity, Order.creation_pending.is_(True),
                                           Order.last_status == PENDING_STATUS[candidate['side']], Order.exchange_order_id.is_(None))
                       .values(creation_pending=False, last_status='OrderFailure', last_update_timestamp=timestamp))
    attempt = db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity,
                                                         SubmissionAttempt.state == 'uncertain').values(state='rejected'))
    if order.rowcount != 1 or attempt.rowcount != 1:
        raise RecoveryBlocked('recovery_state_changed')
    db.execute(OrderStatus.__table__.insert().values(order_id=identity, status='OrderFailure', timestamp=timestamp))
    db.execute(update(MarketState).where(MarketState.id == tracker_id).values(saved_state=saved))
