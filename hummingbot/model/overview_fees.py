"""Conservative, read-only session fee projection from attributed recorder fills."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, select, text

from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill

ROW_LIMIT = 10_000


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def fee_in_usdt(fill):
    """Return the recorded USDT fee iff native fee tokens prove fill-time conversion.

    Recorder conversion uses the fill price for base-token fees. Third-token FX
    has no persisted rate timestamp, so it cannot be called an at-fill value.
    """
    quote = fill.get('quote_asset')
    base = fill.get('base_asset')
    native = fill.get('trade_fee')
    converted = _decimal(fill.get('trade_fee_in_quote'))
    if quote != 'USDT' or not isinstance(native, dict) or converted is None:
        return None, False
    percent = _decimal(native.get('percent', '0'))
    flat_fees = native.get('flat_fees', [])
    if percent is None or not isinstance(flat_fees, list):
        return None, False
    percent_token = native.get('percent_token')
    if percent != 0 and percent_token not in (None, quote, base):
        return None, False
    original_nonzero = percent != 0
    for item in flat_fees:
        if not isinstance(item, dict):
            return None, False
        amount = _decimal(item.get('amount'))
        token = item.get('token')
        if amount is None:
            return None, False
        if amount != 0:
            original_nonzero = True
            if token not in (quote, base):
                return None, False
    if converted == 0 and original_nonzero:
        # Legacy data can mean either a genuine zero or a failed conversion.
        return None, False
    if converted != 0 and not original_nonzero:
        return None, False
    return converted, True


def aggregate_fees(rows):
    total = Decimal(0)
    markets = {}
    unconverted = 0
    converted_count = 0
    for row in rows:
        value, safe = fee_in_usdt(row)
        if not safe:
            unconverted += 1
            continue
        market = {'okx': 'spot', 'okx_perpetual': 'perpetual'}.get(row.get('market'))
        if market is None:
            unconverted += 1
            continue
        total += value
        markets[market] = markets.get(market, Decimal(0)) + value
        converted_count += 1
    return dict(total_usdt=total, by_market=markets, converted_count=converted_count,
                unconverted_count=unconverted, history_complete=False)


def _observation(value, *, observed_at, reason):
    return dict(value=value, observed_at=observed_at, stale=False, unavailable_reason=reason)


def _iso_utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def session_fees(engine, *, bot_id, session_id, account_ids, environment, start_ms, end_ms):
    """Read fees tied to this bot/session and captured account bindings.

    All generations for the session ID are included. Unknown/legacy conversions
    and incomplete recorder coverage stay visible; they never make a complete
    fee total. The source query has a statement deadline and a hard row cap.
    """
    if (not isinstance(bot_id, str) or not bot_id or not isinstance(session_id, str) or not session_id
            or environment not in ('live', 'demo') or not isinstance(account_ids, (list, tuple))
            or not account_ids or any(not isinstance(value, str) or not value for value in account_ids)
            or type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms <= start_ms):
        raise ValueError('Invalid session fee scope')
    accounts = set(account_ids)
    observed_at = datetime.now(timezone.utc)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection, connection.begin():
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
        connection.exec_driver_sql('SET LOCAL jit = off')
        connection.exec_driver_sql('SET LOCAL max_parallel_workers_per_gather = 0')
        observed_at = connection.execute(text('SELECT clock_timestamp()')).scalar_one()
        session_scope = SubmissionAttempt.context['session']
        matching_session = and_(
            SubmissionAttempt.context['bot_id'].astext == bot_id,
            session_scope[0].astext == session_id,
        )
        statement = (select(
            TradeFill.timestamp, TradeFill.market, TradeFill.quote_asset, TradeFill.base_asset,
            TradeFill.trade_fee, TradeFill.trade_fee_in_quote, SubmissionAttempt.context,
        ).join(SubmissionAttempt, SubmissionAttempt.order_id == TradeFill.order_id)
            .where(TradeFill.timestamp >= start_ms, TradeFill.timestamp < end_ms, matching_session)
            .order_by(TradeFill.timestamp, TradeFill.market, TradeFill.order_id, TradeFill.exchange_trade_id)
            .limit(ROW_LIMIT + 1))
        rows = connection.execute(statement).mappings().all()
    if len(rows) > ROW_LIMIT:
        return _observation(None, observed_at=observed_at,
                            reason='session_fee_row_limit_exceeded')
    attributed, unattributed = [], 0
    for row in rows:
        context = row['context']
        bindings = context.get('bindings') if isinstance(context, dict) else None
        matched = isinstance(bindings, list) and any(
            isinstance(binding, dict)
            and binding.get('connector') == row['market']
            and binding.get('account_id') in accounts
            and binding.get('environment') == environment
            for binding in bindings
        )
        if not matched:
            unattributed += 1
            continue
        attributed.append(row)
    if not attributed:
        reason = 'session_fee_binding_unavailable' if rows or unattributed else 'session_fee_history_unavailable'
        return _observation(None, observed_at=observed_at, reason=reason)
    totals = aggregate_fees(attributed)
    if totals['converted_count'] == 0:
        return _observation(None, observed_at=observed_at, reason='session_fee_conversion_unavailable')
    reason = 'session_fee_history_incomplete'
    if totals['unconverted_count']:
        reason = 'session_fee_conversion_incomplete'
    elif unattributed:
        reason = 'session_fee_binding_incomplete'
    through = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    value = dict(
        total_usdt=totals['total_usdt'],
        by_market=[dict(market=market, total_usdt=amount) for market, amount in sorted(totals['by_market'].items())],
        conversion_basis='at_fill', history_complete=False, through_at=_iso_utc(through),
    )
    return _observation(value, observed_at=observed_at, reason=reason)
