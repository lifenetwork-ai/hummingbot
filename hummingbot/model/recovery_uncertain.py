"""Local proof and atomic retirement of one reviewed, expired OKX Spot BUY."""
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


def candidate(db, state, bindings, checkpoint, anchor, identity):
    def require(condition):
        if not condition:
            raise RecoveryBlocked('recovery_uncertain_order_scope')

    try:
        require(db.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(2)).scalars().all() == [identity])
        order = db.execute(select(Order).where(Order.id == identity)).mappings().one()
        attempt = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().one_or_none()
        require(attempt and attempt['state'] == 'uncertain')
        context, receipt = attempt['context'], attempt['receipt'] or {}
        require(receipt.get('code') == '50013' and receipt.get('http_status') in (429, 500, 503)
                and not receipt.get('order'))
        require(eligible(context) and context.get('bot_id') == state['bot_id']
                and context.get('config_file_path') == order['config_file_path'] == state['config_file_path']
                and context['environment'] == checkpoint['environment']
                and context['executor']['connector_name'] == order['market'] == 'okx')
        active = [{k: row[k] for k in ('connector', 'environment', 'account_id', 'version')}
                  for row in bindings if row['state'] == 'active']
        require(sorted(context['bindings'], key=digest) == sorted(active, key=digest)
                and context['credential_fingerprint'] == checkpoint['credential_fingerprints']['okx'])
        scope = state['mm_session'] or {}
        require(context['session'] == [scope.get('id'), scope.get('generation')] and scope.get('status') == 'stopped')
        require(order['last_status'] == 'PendingBuy' and order['exchange_order_id'] is None
                and order['order_type'] == 'LIMIT_MAKER' and order['position'] == 'NIL'
                and not order['position_applied'] and not order['cleanup_operation_id'])
        created, deadline = order['creation_timestamp'], attempt['deadline_ms']
        now = int(state['database_now'].timestamp() * 1000)
        # The authenticated journaled deadline caps when this POST can first be
        # processed. Follow-up financial proof must cover its entire lifetime.
        require(type(deadline) is int and anchor['verified_at_ms'] < created <= deadline
                and created <= order['last_update_timestamp'] <= now
                and deadline - created <= 60_000)
        if now - deadline < SETTLEMENT_GRACE_MS:
            raise RecoveryBlocked('recovery_uncertain_order_wait')
        if now - created >= ORDER_HISTORY_AGE_MS:
            raise RecoveryBlocked('recovery_history_window_exceeded')
        original = InFlightOrder.from_json(context['order'])
        amount, price = decimal(order['amount']), decimal(order['price'])
        pair = order['symbol']
        require(amount > 0 and price > 0 and pair == f"{order['base_asset']}-{order['quote_asset']}")
        require(attempt['payload_hash'] == digest(dict(
            clOrdId=identity, instId=pair, tdMode='cash', ordType='post_only', side='buy',
            sz=str(original.amount), px=f"{original.price:f}")))
        tracker_rows = db.execute(select(MarketState).limit(101)).mappings().all()
        require(len(tracker_rows) <= 100 and sum(identity in row['saved_state'] for row in tracker_rows) == 1)
        live = [(row, key, value) for row in tracker_rows for key, value in row['saved_state'].items()
                if not InFlightOrder.from_json(value).is_done]
        require(len(live) == 1)
        tracker_row, key, raw = live[0]
        require(key == identity and tracker_row['config_file_path'] == state['config_file_path']
                and tracker_row['market'] == 'okx')
        for native in (original, InFlightOrder.from_json(raw)):
            require(native.client_order_id == identity and native.exchange_order_id is None
                    and native.trading_pair == pair and native.trade_type.name == 'BUY' and native.position.name == 'NIL'
                    and native.order_type.name == 'LIMIT_MAKER'
                    and native.current_state in (OrderState.PENDING_CREATE, OrderState.PENDING_CANCEL)
                    and native.amount == amount and native.price == price
                    and not native.executed_amount_base and not native.executed_amount_quote and not native.order_fills)
        require(not db.execute(select(TradeFill.order_id).where(TradeFill.order_id == identity).limit(1)).first())
        timeline = db.execute(select(OrderStatus.status).where(OrderStatus.order_id == identity).limit(2)).scalars().all()
        require(timeline == ['PendingBuy'])
        return dict(id=identity, pair=pair, side='BUY', amount=str(amount), price=str(price), created_ms=created,
                    deadline_ms=deadline, market_state_id=tracker_row['id'], tracker_digest=digest(raw),
                    credential_fingerprint=context['credential_fingerprint'])
    except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
        raise RecoveryBlocked('recovery_uncertain_order_scope') from None


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
