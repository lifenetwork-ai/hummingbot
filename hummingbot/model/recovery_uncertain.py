"""Local proof and atomic retirement of reviewed, expired OKX Spot BUYs."""
from sqlalchemy import select, update

from hummingbot.core.automatic_recovery import eligible
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.recovery import RecoveryBlocked, decimal
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt, digest
from hummingbot.model.trade_fill import TradeFill

ORDER_HISTORY_AGE_MS = 6 * 86_400_000
SETTLEMENT_GRACE_MS = 60_000


def _require(condition):
    if not condition:
        raise RecoveryBlocked('recovery_uncertain_order_scope')


def _matches_stopped_session(scope, original, created, deadline):
    session_id, generation = original
    current = scope.get('generation')
    if (scope.get('status') != 'stopped' or scope.get('id') != session_id or not session_id
            or type(generation) is not int or type(current) is not int or not 0 <= generation <= current):
        return False
    if generation == current:
        return True
    request = scope.get('request') or {}
    seconds, step_index = request.get('step_duration_seconds'), scope.get('step_index')
    started = scope.get('started_at')
    if (request.get('selection') != 'random' or type(seconds) is not int or seconds <= 0
            or type(step_index) is not int or type(started) not in (int, float)):
        return False
    start_ms, step_ms = decimal(started) * 1000, seconds * 1000
    offset = created - start_ms
    if offset < 0:
        return False
    creation_step = int(offset // step_ms)
    # Random steps can skip elapsed indices on restart. The original
    # generation and its valid send deadline must fit the elapsed schedule.
    return (generation <= creation_step < step_index
            and step_index - creation_step >= current - generation
            and deadline <= start_ms + (creation_step + 1) * step_ms)


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
        return [_candidate(db, state, bindings, checkpoint, anchor, identity, *trackers[identity])
                for identity in identities]
    except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
        raise RecoveryBlocked('recovery_uncertain_order_scope') from None


def _candidate(db, state, bindings, checkpoint, anchor, identity, tracker_row, raw):
    order = db.execute(select(Order).where(Order.id == identity)).mappings().one_or_none()
    attempt = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().one_or_none()
    _require(order is not None and attempt is not None and attempt['state'] == 'uncertain')
    context, receipt = attempt['context'], attempt['receipt'] or {}
    _require(receipt.get('code') == '50013' and receipt.get('http_status') in (429, 500, 503)
             and not receipt.get('order'))
    _require(eligible(context) and context.get('bot_id') == state['bot_id']
             and context.get('config_file_path') == order['config_file_path'] == state['config_file_path']
             and context['environment'] == checkpoint['environment']
             and context['executor']['connector_name'] == order['market'] == 'okx')
    active = [{k: row[k] for k in ('connector', 'environment', 'account_id', 'version')}
              for row in bindings if row['state'] == 'active']
    _require(sorted(context['bindings'], key=digest) == sorted(active, key=digest)
             and context['credential_fingerprint'] == checkpoint['credential_fingerprints']['okx'])
    _require(order['last_status'] == 'PendingBuy' and order['exchange_order_id'] is None
             and order['order_type'] == 'LIMIT_MAKER' and order['position'] == 'NIL'
             and not order['position_applied'] and not order['cleanup_operation_id'])
    created, deadline = order['creation_timestamp'], attempt['deadline_ms']
    now = int(state['database_now'].timestamp() * 1000)
    # The authenticated journaled deadline caps when this POST can first be
    # processed. Follow-up financial proof must cover its entire lifetime.
    _require(type(deadline) is int and anchor['verified_at_ms'] < created <= deadline
             and created <= order['last_update_timestamp'] <= now
             and deadline - created <= 60_000)
    _require(_matches_stopped_session(state['mm_session'] or {}, context['session'], created, deadline))
    if now - deadline < SETTLEMENT_GRACE_MS:
        raise RecoveryBlocked('recovery_uncertain_order_wait')
    if now - created >= ORDER_HISTORY_AGE_MS:
        raise RecoveryBlocked('recovery_history_window_exceeded')
    original = InFlightOrder.from_json(context['order'])
    amount, price = decimal(order['amount']), decimal(order['price'])
    pair = order['symbol']
    _require(amount > 0 and price > 0 and pair == f"{order['base_asset']}-{order['quote_asset']}")
    _require(attempt['payload_hash'] == digest(dict(
        clOrdId=identity, instId=pair, tdMode='cash', ordType='post_only', side='buy',
        sz=str(original.amount), px=f"{original.price:f}")))
    _require(tracker_row['config_file_path'] == state['config_file_path']
             and tracker_row['market'] == 'okx')
    for native in (original, InFlightOrder.from_json(raw)):
        _require(native.client_order_id == identity and native.exchange_order_id is None
                 and native.trading_pair == pair and native.trade_type.name == 'BUY' and native.position.name == 'NIL'
                 and native.order_type.name == 'LIMIT_MAKER'
                 and native.current_state in (OrderState.PENDING_CREATE, OrderState.PENDING_CANCEL)
                 and native.amount == amount and native.price == price
                 and not native.executed_amount_base and not native.executed_amount_quote and not native.order_fills)
    _require(not db.execute(select(TradeFill.order_id).where(TradeFill.order_id == identity).limit(1)).first())
    timeline = db.execute(select(OrderStatus.status).where(OrderStatus.order_id == identity).limit(2)).scalars().all()
    _require(timeline == ['PendingBuy'])
    return dict(id=identity, pair=pair, side='BUY', amount=str(amount), price=str(price), created_ms=created,
                deadline_ms=deadline, market_state_id=tracker_row['id'], tracker_digest=digest(raw),
                credential_fingerprint=context['credential_fingerprint'])


def change_for(candidate):
    return {**{key: candidate[key] for key in ('id', 'pair', 'side', 'amount', 'price', 'deadline_ms')},
            'connector': 'okx', 'before': 'PendingBuy', 'after': 'OrderFailure',
            'evidence': 'expired_submission_and_reconciled_exchange'}


def apply_change(db, candidate, timestamp):
    identity, tracker_id = candidate['id'], candidate['market_state_id']
    saved = dict(db.scalar(select(MarketState.saved_state).where(MarketState.id == tracker_id)))
    if digest(saved.get(identity)) != candidate['tracker_digest']:
        raise RecoveryBlocked('recovery_state_changed')
    saved.pop(identity)
    order = db.execute(update(Order).where(Order.id == identity, Order.creation_pending.is_(True),
                                           Order.last_status == 'PendingBuy', Order.exchange_order_id.is_(None))
                       .values(creation_pending=False, last_status='OrderFailure', last_update_timestamp=timestamp))
    attempt = db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity,
                                                         SubmissionAttempt.state == 'uncertain').values(state='rejected'))
    if order.rowcount != 1 or attempt.rowcount != 1:
        raise RecoveryBlocked('recovery_state_changed')
    db.execute(OrderStatus.__table__.insert().values(order_id=identity, status='OrderFailure', timestamp=timestamp))
    db.execute(update(MarketState).where(MarketState.id == tracker_id).values(saved_state=saved))
