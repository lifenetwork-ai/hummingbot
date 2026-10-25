"""Bounded PostgreSQL aggregates over recorder data; no separate PnL ledger."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, cast, column, func, select, text, values
from sqlalchemy.dialects.postgresql import insert

from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot

MARKETS = {"spot": "okx", "perpetual": "okx_perpetual"}


def day_range(start_day, end_day, reporting_timezone, now=None):
    zone = ZoneInfo(reporting_timezone)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    end = date.fromisoformat(str(end_day)) if end_day else today + timedelta(days=1)
    start = date.fromisoformat(str(start_day)) if start_day else end - timedelta(days=7)
    if not 1 <= (end - start).days <= 90:
        raise ValueError("Daily range must be between one and ninety days, with an exclusive end date")
    return start, end, int(datetime.combine(start, time.min, zone).timestamp() * 1000), int(datetime.combine(end, time.min, zone).timestamp() * 1000)


def save_daily_balances(connection, accounts, reporting_timezone):
    """Called only inside a successful owner-fenced status transaction."""
    zone = ZoneInfo(reporting_timezone)
    table = DailyBalanceSnapshot.__table__
    rows = []
    keys = ("account_id", "environment", "currency", "reporting_timezone", "day", "observed_at",
            "connector", "source", "cash_balance", "available_cash", "equity", "available_equity")
    for account in accounts:
        if not account.get("identity_verified") or not account.get("account_id"):
            continue
        for balance in account["balances"]:
            if not balance.get("present_in_latest_snapshot", True):
                continue
            observed = datetime.fromtimestamp(float(balance["observed_at"]), timezone.utc)
            item = {key: balance.get(key) for key in keys}
            item.update(account_id=account["account_id"], environment=account["environment"],
                        reporting_timezone=reporting_timezone, day=observed.astimezone(zone).date(), observed_at=observed)
            rows.append(tuple(item[key] for key in keys))
    if not rows:
        return 0
    # One round trip for the complete observation. Freshness and update cadence
    # still use the database clock inside the caller's owner-fenced transaction.
    source = values(*(column(key, table.c[key].type) for key in keys), name="observed_balances").data(rows)
    primary = [source.c[key.name] for key in table.primary_key.columns]
    fresh = (select(*(cast(source.c[key], table.c[key].type) for key in keys), func.clock_timestamp()).where(
        source.c.observed_at <= func.clock_timestamp(),
        source.c.observed_at >= func.clock_timestamp() - text("interval '60 seconds'"))
        .distinct(*primary).order_by(*primary, source.c.observed_at.desc()))
    statement = insert(table).from_select([*keys, "updated_at"], fresh)
    statement = statement.on_conflict_do_update(
        index_elements=list(table.primary_key.columns),
        set_={key: statement.excluded[key] for key in (*keys, "updated_at") if key not in table.primary_key.columns.keys()},
        where=and_(table.c.observed_at < statement.excluded.observed_at,
                   table.c.updated_at <= func.clock_timestamp() - text("interval '60 seconds'")))
    return connection.execute(statement).rowcount


FILL_SCOPE = """
    SELECT *, (to_timestamp(timestamp / 1000.0) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee::jsonb->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(COALESCE(trade_fee::jsonb->'flat_fees', '[]'::jsonb)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
"""


def daily(engine, *, start_day=None, end_day=None, reporting_timezone, market="all", pair=None):
    if market not in (*MARKETS, "all"):
        raise ValueError("Unknown market")
    selected = tuple(MARKETS) if market == "all" else (market,)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        # These bounded UI aggregates repeatedly paid more JIT compilation than
        # execution. Keep the setting local to this read-only transaction.
        connection.exec_driver_sql("SET LOCAL jit = off")
        now = connection.execute(select(func.clock_timestamp())).scalar_one()
        start, end, start_ms, end_ms = day_range(start_day, end_day, reporting_timezone, now)
        params = dict(markets=[MARKETS[key] for key in selected], start_ms=start_ms, end_ms=end_ms, tz=reporting_timezone, pair=pair)
        fills = connection.execute(text("WITH scoped AS (" + FILL_SCOPE + """), converted AS (
            SELECT *, (trade_fee_in_quote IS NOT NULL AND quote_asset = 'USDT'
                AND NOT (trade_fee_in_quote = 0 AND has_original_fee)) AS valid_conversion FROM scoped)
            SELECT market, day, COUNT(*) AS fill_count, MAX(timestamp) AS last_event_ms,
                SUM(CASE WHEN trade_type='BUY' THEN amount ELSE 0 END) AS buy_volume_base,
                SUM(CASE WHEN trade_type='SELL' THEN amount ELSE 0 END) AS sell_volume_base,
                SUM(CASE WHEN trade_type='BUY' THEN price*amount ELSE 0 END) AS buy_volume_quote,
                SUM(CASE WHEN trade_type='SELL' THEN price*amount ELSE 0 END) AS sell_volume_quote,
                COUNT(*) FILTER (WHERE valid_conversion) AS converted_fill_count,
                COUNT(*) FILTER (WHERE NOT valid_conversion) AS unconverted_fill_count,
                COUNT(*) FILTER (WHERE COALESCE((trade_fee::jsonb->>'percent')::numeric,0) <> 0) AS percentage_fee_fill_count,
                SUM(trade_fee_in_quote) FILTER (WHERE valid_conversion) AS fee_in_usdt
            FROM converted GROUP BY market, day ORDER BY market, day
        """), params).mappings().all()
        fees = connection.execute(text("WITH scoped AS (" + FILL_SCOPE + """)
            SELECT market, day, fee->>'token' AS currency,
                SUM(GREATEST((fee->>'amount')::numeric,0)) AS paid,
                SUM(GREATEST(-(fee->>'amount')::numeric,0)) AS rebates,
                SUM((fee->>'amount')::numeric) AS net
            FROM scoped CROSS JOIN LATERAL jsonb_array_elements(COALESCE(trade_fee::jsonb->'flat_fees','[]'::jsonb)) fee
            GROUP BY market, day, fee->>'token' ORDER BY market, day, currency
        """), params).mappings().all()
        funding = connection.execute(text("""
            SELECT market, (to_timestamp(timestamp/1000.0) AT TIME ZONE :tz)::date AS day, MAX(timestamp) AS last_event_ms,
                SUM(GREATEST(-amount::text::numeric,0)) AS paid, SUM(GREATEST(amount::text::numeric,0)) AS received
            FROM funding_payments WHERE market='okx_perpetual' AND market=ANY(:markets)
                AND timestamp >= :start_ms AND timestamp < :end_ms AND (:pair IS NULL OR symbol=:pair)
            GROUP BY market, day ORDER BY day
        """), params).mappings().all()
        balances = [dict(row) for row in connection.execute(select(DailyBalanceSnapshot).where(
            DailyBalanceSnapshot.reporting_timezone == reporting_timezone, DailyBalanceSnapshot.day >= start,
            DailyBalanceSnapshot.day < end).order_by(DailyBalanceSnapshot.day, DailyBalanceSnapshot.account_id,
                                                     DailyBalanceSnapshot.environment, DailyBalanceSnapshot.currency)).mappings()]
    buckets = {}

    def bucket(connector, day):
        key = connector, day
        if key not in buckets:
            buckets[key] = dict(day=day, observed_at=now, last_event_at=None, fill_count=0,
                                buy_volume_base=Decimal(0), sell_volume_base=Decimal(0), buy_volume_quote=Decimal(0), sell_volume_quote=Decimal(0),
                                converted_fill_count=0, unconverted_fill_count=0, percentage_fee_fill_count=0, fee_in_usdt=None, fees=[],
                                funding_paid=Decimal(0) if connector == "okx_perpetual" else None,
                                funding_received=Decimal(0) if connector == "okx_perpetual" else None)
        return buckets[key]

    for row in fills:
        values = dict(row)
        connector, day, last = values.pop("market"), values.pop("day"), values.pop("last_event_ms")
        bucket(connector, day).update(**values, last_event_at=datetime.fromtimestamp(last / 1000, timezone.utc))
    for row in fees:
        bucket(row["market"], row["day"])["fees"].append({key: row[key] for key in ("currency", "paid", "rebates", "net")})
    for row in funding:
        item = bucket(row["market"], row["day"])
        last = datetime.fromtimestamp(row["last_event_ms"] / 1000, timezone.utc)
        item.update(funding_paid=row["paid"], funding_received=row["received"], last_event_at=max(item["last_event_at"] or last, last))
    return dict(start_day=start, end_day=end, reporting_timezone=reporting_timezone, server_time=now,
                coverage="observed_recorder_events", balance_scope="account_last_observed", balances=balances,
                markets=[dict(market=key, connector=MARKETS[key], funding_status="not_applicable" if key == "spot" else "observed_only",
                              days=[value for (connector, day), value in sorted(buckets.items()) if connector == MARKETS[key]]) for key in selected])
