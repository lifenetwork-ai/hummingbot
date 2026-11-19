"""Compare verified trading-account cash observations in the asset's native units."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from sqlalchemy import Column, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.core.reporting import reporting_view, verified_account_identity
from hummingbot.model import HummingbotBase
from hummingbot.model.admin_reporting import day_range
from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot

MIGRATION = "024_create_session_cash_baselines.sql"


class SessionCashBaseline(HummingbotBase):
    __tablename__ = "session_cash_baselines"
    bot_id = Column(Text, primary_key=True)
    session_id = Column(Text, primary_key=True)
    account_id = Column(Text, primary_key=True)
    environment = Column(Text, primary_key=True)
    balances = Column(JSONB, nullable=False)


def _cash_account(reporting):
    identity = verified_account_identity(reporting)
    if identity is None:
        return None
    account_id, environment, _ = identity
    matches = [account for account in reporting.get("accounts", [])
               if account.get("account_id") == account_id and account.get("environment") == environment]
    return matches[0] if len(matches) == 1 and matches[0].get("identity_verified") is True else None


def save_session_cash_baseline(connection, bot_id, session, status):
    published = status.get("mm_session") or {}
    if (not session or session.get("status") != "running" or session.get("started_at") is None
            or published.get("id") != session["id"] or published.get("started_at") != session["started_at"]
            or not status.get("running")):
        return
    account = _cash_account(status.get("reporting") or {})
    if account is None:
        return
    now = float(connection.scalar(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")))
    # Accept the verified startup balance fetched while the session was preparing.
    # Waiting for a post-start refresh can move the baseline past the first fill.
    balances = {item["currency"]: dict(cash_balance=item.get("cash_balance"), observed_at=item["observed_at"])
                for item in account["balances"] if item.get("present_in_latest_snapshot", True)
                and 0 <= now - item["observed_at"] <= 60}
    if not any(item["cash_balance"] is not None for item in balances.values()):
        return
    # The migration timestamp prevents assigning a new opening observation to a legacy
    # session. The first accepted snapshot remains immutable across owner takeovers.
    connection.execute(text("""
        INSERT INTO session_cash_baselines (bot_id, session_id, account_id, environment, balances)
        SELECT :bot_id, :session_id, :account_id, :environment, CAST(:balances AS jsonb)
        FROM hbot_schema_migrations
        WHERE name = :migration AND applied_at <= to_timestamp(:started_at)
        ON CONFLICT DO NOTHING
    """), dict(bot_id=bot_id, session_id=session["id"], account_id=account["account_id"],
               environment=account["environment"], balances=json.dumps(balances, default=str),
               migration=MIGRATION, started_at=session["started_at"]))


def _observation_time(value):
    return value if isinstance(value, datetime) else datetime.fromtimestamp(value, timezone.utc)


def _decimal_string(value):
    if value is None:
        return None
    number = Decimal(value)
    if number.is_zero():
        return "0"
    plain = format(number, "f")
    return plain.rstrip("0").rstrip(".") if "." in plain else plain


def _cash_changes(opening, closing):
    for currency in sorted(opening.keys() | closing.keys()):
        before = opening.get(currency) or {}
        after = closing.get(currency) or {}
        left, right = before.get("cash_balance"), after.get("cash_balance")
        reason = None
        if after.get("stale") or (before and after and
                                  _observation_time(after["observed_at"]) < _observation_time(before["observed_at"])):
            reason = "closing_stale"
        elif left is None:
            reason = "opening_missing"
        elif right is None:
            reason = "closing_missing"
        with localcontext() as context:
            context.prec = 40  # NUMERIC(38,18) subtraction may need one more digit.
            change = Decimal(right) - Decimal(left) if reason is None else None
        yield dict(currency=currency, opening_cash=_decimal_string(left), closing_cash=_decimal_string(right),
                   opening_observed_at=_observation_time(before["observed_at"]) if before else None,
                   closing_observed_at=_observation_time(after["observed_at"]) if after else None,
                   delta_cash=_decimal_string(change), reason_code=reason)


def balance_changes(engine, bot_id, *, period, reporting_timezone, start_day, end_day):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        state = connection.execute(text("""
            SELECT owner_uuid,
                   jsonb_build_object('reporting', status->'reporting', 'running', status->'running',
                                      'reporting_unavailable', status->'reporting_unavailable') AS status,
                   mm_session - 'catalogue' AS mm_session,
                   clock_timestamp() AS now,
                   EXTRACT(EPOCH FROM (clock_timestamp() - heartbeat)) AS heartbeat_age_s
            FROM bot_state WHERE bot_id = :bot_id
        """), {"bot_id": bot_id}).mappings().first()
        now = state["now"] if state else connection.scalar(text("SELECT clock_timestamp()"))
        zone = ZoneInfo(reporting_timezone)
        today = now.astimezone(zone).date()
        session = (state["mm_session"] or {}) if state and period == "session" else {}
        if period == "days":
            start_day, end_day, _, _ = day_range(start_day or (today if end_day is None else None),
                                                 end_day, reporting_timezone, now)
            if end_day > today + timedelta(days=1):
                raise ValueError("Balance comparison cannot include future days")
            if start_day == datetime.min.date():
                raise ValueError("Balance comparison requires a preceding day")
        result = dict(period=period, start_day=start_day, end_day=end_day, reporting_timezone=reporting_timezone,
                      session_id=session.get("id"), session_started_at=session.get("started_at"), account_id=None,
                      environment=None, server_time=now, flow_history="unverified", reason_code=None, rows=[])
        reporting = ((state["status"] or {}).get("reporting") or {}) if state else {}
        scope = _cash_account(reporting)
        if scope is None:
            return dict(result, reason_code="account_identity_unavailable")
        result.update(account_id=scope["account_id"], environment=scope["environment"])
        if period == "session" and session.get("started_at") is None:
            return dict(result, reason_code="session_unavailable")

        opening, closing = {}, {}
        if period == "session":
            opening = connection.scalar(select(SessionCashBaseline.balances).filter_by(
                bot_id=bot_id, session_id=session["id"], account_id=result["account_id"],
                environment=result["environment"])) or {}
        else:
            opening_day, closing_day = start_day - timedelta(days=1), end_day - timedelta(days=1)
            historical_days = [opening_day] if closing_day == today else [opening_day, closing_day]
            snapshot = DailyBalanceSnapshot
            for row in connection.execute(select(snapshot.day, snapshot.currency, snapshot.cash_balance, snapshot.observed_at)
                                          .where(snapshot.account_id == result["account_id"],
                                                 snapshot.environment == result["environment"],
                                                 snapshot.reporting_timezone == reporting_timezone,
                                                 snapshot.day.in_(historical_days))).mappings():
                (opening if row["day"] == opening_day else closing)[row["currency"]] = row

        if period == "session" or closing_day == today:
            projection = reporting_view(dict(state, database_now=now.timestamp()))
            current = next(account for account in projection["accounts"]
                           if account["account_id"] == result["account_id"] and account["environment"] == result["environment"])
            for item in current["balances"]:
                if period == "days" and _observation_time(item["observed_at"]).astimezone(zone).date() != today:
                    item["stale"] = True
                closing[item["currency"]] = item
        result["rows"] = list(_cash_changes(opening, closing))
        if not opening:
            result["reason_code"] = "opening_missing"
        elif not closing:
            result["reason_code"] = "closing_missing"
        return result
