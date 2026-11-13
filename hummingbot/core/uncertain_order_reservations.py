"""Conservative cash-only admission during a bounded, unresolved BUY cohort.

This module supplies no ownership, session or financial authority. It never
credits unproved executions, cash proceeds or rebates, and never resolves an ID.
"""
from decimal import Decimal, InvalidOperation

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, DeductedFromReturnsTradeFee
from hummingbot.core.order_submission_guard import MarketReadinessBlocked

MAX_UNCERTAIN = 64
MAX_RESIDENT = 64
ZERO = Decimal(0)


def buy_fee_envelope(connector, order):
    """Persist supported fee assumptions before transport, never reconstruct them later."""
    try:
        if connector.name != 'okx' or order.trade_type != TradeType.BUY or order.order_type != OrderType.LIMIT_MAKER:
            return None
        base, quote = order.trading_pair.split('-')
        fee = connector.get_fee(base, quote, order.order_type, order.trade_type, order.amount, order.price, is_maker=True)
        if not isinstance(fee, (AddedToCostTradeFee, DeductedFromReturnsTradeFee)) or fee.flat_fees:
            return None
        percent = Decimal(str(fee.percent))
        token = fee.percent_token or (quote if isinstance(fee, AddedToCostTradeFee) else base)
        if not percent.is_finite() or abs(percent) >= 1 or token not in (base, quote):
            return None
        return {'version': 1, 'percent': str(percent), 'percent_token': token,
                'base_hold': str(order.amount * (1 + max(ZERO, -percent) if token == base else 1)),
                'quote_hold': str(order.amount * order.price * (1 + max(ZERO, percent) if token == quote else 1))}
    except (ArithmeticError, ValueError, TypeError, AttributeError):
        return None


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or result < 0 or positive and result == 0:
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise MarketReadinessBlocked('uncertain_reservation_unavailable') from None


def cohort(attempts, *, bindings, environment, config_file_path, max_quote):
    """Validate a complete bounded cohort; no persisted proof is inferred here."""
    if not attempts or len(attempts) > MAX_UNCERTAIN:
        raise MarketReadinessBlocked('uncertain_reservation_limit')
    seen, base_holds, quote_hold = set(), {}, ZERO
    for attempt in attempts:
        context = attempt.get('context') or {}
        order, executor = context.get('order') or {}, context.get('executor') or {}
        identity, pair = attempt.get('order_id'), order.get('trading_pair')
        if (not isinstance(identity, str) or not identity or identity in seen
                or attempt.get('state') not in ('uncertain', 'possibly_sent') or context.get('environment') != environment
                or context.get('config_file_path') != config_file_path
                or context.get('bindings') != bindings or executor.get('connector_name') != 'okx'
                or order.get('trade_type') != 'BUY' or order.get('position') not in ('NIL', 'OPEN')
                or order.get('order_type') != 'LIMIT_MAKER' or not isinstance(pair, str)
                or not pair.endswith('-USDT') or len(pair.split('-')) != 2 or pair.split('-')[0] in ('', 'USDT')
                or order.get('client_order_id') != identity or executor.get('trading_pair') != pair
                or executor.get('type') != 'position_executor'):
            raise MarketReadinessBlocked('uncertain_reservation_scope')
        seen.add(identity)
        amount, price = number(order.get('amount'), positive=True), number(order.get('price'), positive=True)
        if number(executor.get('amount'), positive=True) < amount:
            raise MarketReadinessBlocked('uncertain_reservation_scope')
        base = pair.split('-')[0]
        fee = (context.get('reservation') or {}).get('cash_buy_fee') or {}
        try:
            percent = Decimal(str(fee['percent']))
            token = fee['percent_token']
            expected_base = amount * (1 + max(ZERO, -percent) if token == base else 1)
            expected_quote = amount * price * (1 + max(ZERO, percent) if token == 'USDT' else 1)
            if (type(fee['version']) is not int or fee['version'] != 1 or not percent.is_finite()
                    or abs(percent) >= 1 or token not in (base, 'USDT')
                    or number(fee['base_hold'], positive=True) != expected_base
                    or number(fee['quote_hold'], positive=True) != expected_quote):
                raise ValueError
        except (ArithmeticError, ValueError, KeyError, TypeError):
            raise MarketReadinessBlocked('uncertain_fee_unavailable') from None
        # Subtract even the possible acquired base: it is not spendable proof.
        base_holds[base] = base_holds.get(base, ZERO) + expected_base
        quote_hold += expected_quote
    if quote_hold > number(max_quote, positive=True):
        raise MarketReadinessBlocked('uncertain_reservation_limit')
    return base_holds, quote_hold


def sell_cost(connector, order):
    base, quote = order.trading_pair.split('-')
    amount, price = number(order.amount, positive=True), number(order.price, positive=True)
    fee = connector.get_fee(base, quote, order.order_type, order.trade_type, amount, price, is_maker=True)
    if not isinstance(fee, (AddedToCostTradeFee, DeductedFromReturnsTradeFee)):
        raise MarketReadinessBlocked('uncertain_fee_unavailable')
    percent = Decimal(str(fee.percent))
    if not percent.is_finite() or abs(percent) >= 1:
        raise MarketReadinessBlocked('uncertain_fee_unavailable')
    token = fee.percent_token or (base if isinstance(fee, AddedToCostTradeFee) else quote)
    if token not in (base, quote):
        raise MarketReadinessBlocked('uncertain_fee_unavailable')
    cost = amount + (amount * max(ZERO, percent) if token == base else ZERO)
    quote_cost = amount * price * max(ZERO, percent) if token == quote else ZERO
    for flat in fee.flat_fees:
        value = Decimal(str(flat.amount))
        if not value.is_finite() or flat.token not in (base, quote):
            raise MarketReadinessBlocked('uncertain_fee_unavailable')
        if flat.token == base:
            cost += max(ZERO, value)
        else:
            quote_cost += max(ZERO, value)
    if quote_cost >= amount * price:
        raise MarketReadinessBlocked('uncertain_fee_unavailable')
    original = getattr(order, '_cash_sell_reserved_base', None)
    return max(cost, number(original, positive=True)) if original is not None else cost


def authorize_cash_sell(connector, order, base_holds, *, now, max_age):
    if (connector.name != 'okx' or order.trade_type != TradeType.SELL
            or order.order_type != OrderType.LIMIT_MAKER or not order.trading_pair.endswith('-USDT')
            or order.trading_pair.split('-')[0] in ('', 'USDT')):
        raise MarketReadinessBlocked('uncertain_order_risk_paused')
    base = order.trading_pair.split('-')[0]
    observation = connector.account_balance_observations.get(base, {})
    stamp = observation.get('observed_at')
    if (not isinstance(stamp, (float, int)) or isinstance(stamp, bool)
            or not 0 <= now - stamp <= max_age):
        raise MarketReadinessBlocked('uncertain_balance_unavailable')
    cash = min(number(observation.get('cash_balance')), number(connector.get_available_balance(base)))
    reserved = base_holds.get(base, ZERO)
    tracker = getattr(connector, '_order_tracker', None)
    # A cached terminal SELL may have executed after the balance sample. Its
    # debit must remain reserved until that account observation catches up.
    orders = tracker.all_fillable_orders if tracker is not None else connector.in_flight_orders
    if len(orders) > MAX_RESIDENT:
        raise MarketReadinessBlocked('uncertain_reservation_limit')
    for identity, resident in orders.items():
        if (identity != order.client_order_id and resident.trading_pair == order.trading_pair
                and resident.trade_type == TradeType.SELL
                and (not resident.is_done or resident.last_update_timestamp > stamp)):
            if getattr(resident, '_cash_sell_reserved_base', None) is None:
                raise MarketReadinessBlocked('uncertain_fee_unavailable')
            # Reserve the whole native amount, including unacknowledged creates.
            reserved += sell_cost(connector, resident)
    if sell_cost(connector, order) + reserved > cash:
        raise MarketReadinessBlocked('uncertain_base_reserved')
