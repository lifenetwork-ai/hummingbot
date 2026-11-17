"""Conservative session fee conversion and bounded durable reporting projection."""
from decimal import Decimal, InvalidOperation


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


def session_fees(engine, *, bot_id, session_id, account_ids, environment, start_ms, end_ms):
    """Apply one reporting batch without an overall session-history row cap."""
    if (not isinstance(bot_id, str) or not bot_id or not isinstance(session_id, str) or not session_id
            or environment not in ('live', 'demo') or not isinstance(account_ids, (list, tuple))
            or not account_ids or any(not isinstance(value, str) or not value for value in account_ids)
            or type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms <= start_ms):
        raise ValueError('Invalid session fee scope')
    from hummingbot.model.overview_fee_projection import project_session_fees

    return project_session_fees(engine, bot_id=bot_id, session_id=session_id, account_ids=account_ids,
                                environment=environment, start_ms=start_ms, end_ms=end_ms)
