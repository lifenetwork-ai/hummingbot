"""Bounded local prerequisites for reviewing terminal maker-order metadata."""
from sqlalchemy import exists, select, update

from hummingbot.core.automatic_recovery import eligible
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.recovery import RecoveryBlocked, decimal
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import SubmissionAttempt, digest
from hummingbot.model.trade_fill import TradeFill

MAX_TERMINAL_ORDERS = 64


def candidates(connection, state, bindings, checkpoint):
    """None means unsupported work; an empty list means no terminal repair."""
    attempts = connection.execute(select(SubmissionAttempt).where(
        SubmissionAttempt.state.notin_(('terminal', 'rejected'))).order_by(
            SubmissionAttempt.order_id).limit(MAX_TERMINAL_ORDERS + 1)).mappings().all()
    trackers = connection.execute(select(MarketState).limit(101)).mappings().all()
    if len(attempts) > MAX_TERMINAL_ORDERS or len(trackers) > 100:
        return None
    live, result = {}, []
    try:
        for row in trackers:
            if not isinstance(row['saved_state'], dict) or len(row['saved_state']) > 1000:
                return None
            for identity, raw in row['saved_state'].items():
                tracked = InFlightOrder.from_json(raw)
                if tracked.is_done:
                    continue
                if identity != tracked.client_order_id or identity in live:
                    return None
                live[identity] = row, tracked, raw
        expected_bindings = sorted((r['connector'], r['environment'], r['account_id'], r['version'])
                                   for r in bindings if r['state'] == 'active')
        for attempt in attempts:
            identity, context = attempt['order_id'], attempt['context']
            if attempt['state'] != 'acknowledged' or identity not in live or not eligible(context):
                return None
            order = connection.execute(select(Order).where(Order.id == identity)).mappings().one_or_none()
            tracker_row, tracked, raw = live[identity]
            original, receipt = context['order'], attempt['receipt'] or {}
            credential = (checkpoint.get('credential_fingerprints') or {}).get(order['market']) if order else None
            if (not order or order['creation_pending'] or not order['exchange_order_id']
                    or order['order_type'] != 'LIMIT_MAKER' or order['last_status'] not in ('BuyOrderCreated', 'SellOrderCreated')
                    or context['bot_id'] != state['bot_id'] or context['config_file_path'] != state['config_file_path']
                    or context['environment'] != checkpoint['environment']
                    or sorted((r['connector'], r['environment'], r['account_id'], r['version'])
                              for r in context['bindings']) != expected_bindings
                    or not credential or context['credential_fingerprint'] != credential
                    or tracker_row['config_file_path'] != state['config_file_path'] or tracker_row['market'] != order['market']
                    or order['config_file_path'] != state['config_file_path'] or context['executor']['connector_name'] != order['market']
                    or original['client_order_id'] != identity or original['trading_pair'] != order['symbol']
                    or original['trade_type'] != ('BUY' if order['last_status'] == 'BuyOrderCreated' else 'SELL')
                    or original['position'] != order['position'] or tracked.exchange_order_id != order['exchange_order_id']
                    or tracked.trade_type.name != original['trade_type'] or tracked.position.name != original['position']
                    or tracked.trading_pair != order['symbol'] or tracked.order_type.name != order['order_type']
                    or tracked.amount != decimal(order['amount']) or tracked.price != decimal(order['price'])
                    or decimal(original['amount']) != tracked.amount or decimal(original['price']) != tracked.price
                    or tracked.executed_amount_base != 0 or tracked.executed_amount_quote != 0 or tracked.order_fills
                    or decimal(original['executed_amount_base']) != 0 or decimal(original['executed_amount_quote']) != 0
                    or original['order_fills']
                    or receipt.get('code') != '0' or receipt.get('http_status') != 200
                    or (receipt.get('order') or {}).get('sCode') != '0'
                    or receipt['order'].get('ordId') != order['exchange_order_id']
                    or receipt['order'].get('clOrdId') != identity):
                return None
            if connection.scalar(select(exists().where(TradeFill.order_id == identity))):
                return None
            allowed = ('PendingBuy', 'BuyOrderCreated') if original['trade_type'] == 'BUY' else ('PendingSell', 'SellOrderCreated')
            if connection.scalar(select(exists().where(OrderStatus.order_id == identity, OrderStatus.status.notin_(allowed)))):
                return None
            result.append(dict(id=identity, connector=order['market'], pair=order['symbol'], side=original['trade_type'],
                               position=order['position'], amount=str(order['amount']), price=str(order['price']),
                               exchange_order_id=order['exchange_order_id'], creation_timestamp=order['creation_timestamp'],
                               last_update_timestamp=order['last_update_timestamp'], last_status=order['last_status'],
                               market_state_id=tracker_row['id'], tracker_digest=digest(raw), credential_fingerprint=credential))
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, RecoveryBlocked):
        return None
    return result if set(live) == {r['id'] for r in result} else None


def apply_changes(connection, snapshot, changes):
    """Caller holds the stopped proof/authority locks and owns the transaction."""
    from hummingbot.core.recovery_terminal import validate_changes
    validate_changes(snapshot, changes)
    by_id = {row['id']: row for row in snapshot.get('terminal_orders', [])}
    trackers = {}
    for change in changes:
        candidate = by_id[change['id']]
        identity, key = candidate['id'], candidate['market_state_id']
        if key not in trackers:
            row = connection.execute(select(MarketState).where(MarketState.id == key)).mappings().one()
            trackers[key] = dict(row['saved_state'])
        if digest(trackers[key].get(identity)) != candidate['tracker_digest']:
            raise RecoveryBlocked('recovery_state_changed')
        trackers[key].pop(identity)
        order = connection.execute(update(Order).where(
            Order.id == identity, Order.creation_pending.is_(False),
            Order.exchange_order_id == candidate['exchange_order_id'], Order.last_status == candidate['last_status'],
            Order.last_update_timestamp == candidate['last_update_timestamp']).values(
                last_status='OrderCancelled', last_update_timestamp=change['terminal_timestamp']))
        attempt = connection.execute(update(SubmissionAttempt).where(
            SubmissionAttempt.order_id == identity, SubmissionAttempt.state == 'acknowledged').values(state='terminal'))
        if order.rowcount != 1 or attempt.rowcount != 1:
            raise RecoveryBlocked('recovery_state_changed')
        connection.execute(OrderStatus.__table__.insert().values(order_id=identity, status='OrderCancelled',
                                                                 timestamp=change['terminal_timestamp']))
    for key, saved in trackers.items():
        connection.execute(update(MarketState).where(MarketState.id == key).values(saved_state=saved))
