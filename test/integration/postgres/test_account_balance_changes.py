import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event, text

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.account_balance_changes import MIGRATION
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def store(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "balance-test")
    state.ensure()
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET owner_uuid='owner', owner_generation=1"))
    return AdminStore(state)


def publish(store, session, observed, balances, *, environment="demo", owner="owner", available=None):
    reporting = dict(active_connectors=["okx", "okx_perpetual"], active_account_bindings={
        name: dict(account_id="uid", environment=environment) for name in ("okx", "okx_perpetual")
    }, accounts=[dict(account_id="uid", environment=environment, identity_verified=True, balances=[
        dict(currency=currency, cash_balance=cash, equity=equity, available_cash=(available or {}).get(currency),
             available_equity=None, connector="okx",
             observed_at=observed.timestamp(), source="okx_account_balance")
        for currency, (cash, equity) in balances.items()
    ])])
    return store.store.publish(owner_uuid=owner, owner_generation=1, version=0,
                               status=dict(running=True, mm_session=session, reporting=reporting, reporting_timezone="UTC"))


def set_session(store, started, session_id="session"):
    session = dict(id=session_id, started_at=started.timestamp(), status="running")
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET mm_session=CAST(:session AS jsonb)"),
                           dict(session=json.dumps(session)))
    return session


def test_session_baseline_is_immutable_and_preserves_cash_units(store):
    with store.engine.connect() as connection:
        started = connection.scalar(text("SELECT clock_timestamp()"))
    session = set_session(store, started)
    observed = datetime.now(timezone.utc)
    assert not publish(store, session, observed, {"BTC": ("9", "9")}, owner="fenced")
    assert publish(store, session, observed, {"BTC": ("0.1", "0.1"), "USDT": ("10000", "10000")})
    restarted = AdminStore(BotStateStore(store.engine, "balance-test"))
    assert publish(restarted, session, datetime.now(timezone.utc),
                   {"BTC": ("0.07999999", "0.07999999"), "USDT": ("11300", "11200"), "ETH": ("0", "0")})
    result = restarted.balance_changes(period="session", reporting_timezone="UTC", start_day=None, end_day=None)
    assert result["session_id"] == "session"
    rows = {item["currency"]: item for item in result["rows"]}
    assert rows["BTC"]["delta_cash"] == "-0.02000001"
    assert rows["USDT"]["delta_cash"] == "1300"
    assert rows["ETH"]["closing_cash"] == "0"
    assert rows["ETH"]["delta_cash"] is None
    assert rows["ETH"]["reason_code"] == "opening_missing"
    # Changing available cash or unrealized PnL must leave the cash-only comparison unchanged.
    assert publish(restarted, session, datetime.now(timezone.utc),
                   {"BTC": ("0.07999999", "0.07999999"), "USDT": ("11300", "11000")}, available={"USDT": "100"})
    assert restarted.balance_changes(period="session", reporting_timezone="UTC", start_day=None, end_day=None)["rows"][1]["delta_cash"] == "1300"
    assert publish(restarted, session, datetime.now(timezone.utc), {"BTC": ("9", "9")}, environment="live")
    live = restarted.balance_changes(period="session", reporting_timezone="UTC", start_day=None, end_day=None)
    assert live["environment"] == "live"
    assert live["rows"][0]["opening_cash"] == "9" and live["rows"][0]["delta_cash"] == "0"


def test_session_uses_fresh_cash_observed_just_before_start(store):
    started = datetime.now(timezone.utc)
    session = set_session(store, started)
    observed = started - timedelta(seconds=1)
    assert publish(store, session, observed, {"BTC": ("0.1", "0.1")})
    result = store.balance_changes(period="session", reporting_timezone="UTC", start_day=None, end_day=None)
    assert result["rows"][0]["opening_observed_at"] == observed
    assert result["rows"][0]["delta_cash"] == "0"


def test_legacy_session_and_yesterdays_closing_observation_stay_unqualified(store):
    now = datetime.now(timezone.utc)
    session = set_session(store, now - timedelta(days=2))
    assert publish(store, session, now, {"BTC": ("0.08", "0.09")})
    result = store.balance_changes(period="session", reporting_timezone="UTC", start_day=None, end_day=None)
    assert result["reason_code"] == "opening_missing"
    assert result["rows"][0]["delta_cash"] is None
    yesterday = now - timedelta(days=1)
    assert publish(store, session, yesterday, {"BTC": ("0.1", "0.1")})
    result = store.balance_changes(period="days", reporting_timezone="UTC", start_day=None, end_day=None)
    assert result["start_day"] == now.date() and result["end_day"] == now.date() + timedelta(days=1)
    assert result["rows"][0]["closing_observed_at"] == yesterday
    assert result["rows"][0]["reason_code"] == "closing_stale"


def test_balance_migration_preserves_existing_daily_cash(store):
    assert publish(store, None, datetime.now(timezone.utc), {"BTC": ("0.1", "0.1")})
    with store.engine.begin() as connection:
        before = connection.execute(text("SELECT * FROM daily_balance_snapshots")).mappings().all()
        assert len(before) == 1
        connection.exec_driver_sql("DROP TABLE session_cash_baselines")
        connection.exec_driver_sql("DROP INDEX idx_daily_balance_snapshots_scope_day")
        connection.execute(text("DELETE FROM hbot_schema_migrations WHERE name = :name"), {"name": MIGRATION})
    bootstrap_postgres(store.engine, auto_migrate=True)
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM daily_balance_snapshots")).mappings().all() == before
        assert connection.scalar(text("SELECT count(*) FROM session_cash_baselines")) == 0
        assert connection.scalar(text("SELECT count(*) FROM hbot_schema_migrations WHERE name = :name"),
                                 {"name": MIGRATION}) == 1


def test_dates_use_exact_days_and_verified_environment(store):
    cutoff = datetime(2026, 1, 1, 17, tzinfo=timezone.utc)
    with store.engine.begin() as connection:
        for day, currency, cash, environment in [
            (0, "BTC", "0.1", "demo"), (1, "BTC", "0.08", "demo"),
            (0, "BTC", "9", "live"), (1, "USDT", "0", "demo"),
        ]:
            instant = cutoff + timedelta(days=day, seconds=-1)
            connection.execute(text("""INSERT INTO daily_balance_snapshots
                (account_id, environment, currency, reporting_timezone, day, observed_at, updated_at, connector, source, cash_balance)
                VALUES ('uid', :environment, :currency, 'Asia/Ho_Chi_Minh', :day, :instant, :instant, 'okx', 'okx_account_balance', :cash)"""),
                               dict(environment=environment, currency=currency, cash=cash, day=instant.date(), instant=instant))
    assert publish(store, None, datetime.now(timezone.utc), {"BTC": ("0.08", "0.08")})
    result = store.balance_changes(period="days", reporting_timezone="Asia/Ho_Chi_Minh",
                                   start_day="2026-01-02", end_day="2026-01-03")
    btc, usdt = result["rows"]
    assert btc["delta_cash"] == "-0.02"
    assert btc["opening_observed_at"] == cutoff - timedelta(seconds=1)
    assert usdt["opening_cash"] is None and usdt["closing_cash"] == "0"
    assert usdt["delta_cash"] is None
    with pytest.raises(ValueError, match="future"):
        store.balance_changes(period="days", reporting_timezone="UTC", start_day="9998-01-02", end_day="9998-01-03")


def test_cash_queries_remain_bounded_under_concurrent_history_reads(store):
    with store.engine.begin() as connection:
        connection.execute(text("""INSERT INTO daily_balance_snapshots
            (account_id, environment, currency, reporting_timezone, day, observed_at, updated_at, connector, source, cash_balance)
            SELECT 'uid', 'demo', 'BTC', 'UTC', day::date, day, day, 'okx', 'okx_account_balance', 0.1
            FROM generate_series('2000-01-01'::timestamptz, '2026-10-01'::timestamptz, INTERVAL '1 day') AS day"""))
    assert publish(store, None, datetime.now(timezone.utc), {"BTC": ("0.08", "0.08")})

    def read(_):
        return store.balance_changes(period="days", reporting_timezone="UTC", start_day="2026-09-01", end_day="2026-10-01")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(8)))
    assert all(result["rows"][0]["delta_cash"] == "0" for result in results)


@pytest.mark.asyncio
async def test_held_http_comparison_leaves_order_owner_publication_and_live_balances_available(store):
    now = datetime.now(timezone.utc)
    opening = {"BTC": ("0.1", "0.1"), "USDT": ("10000", "10000")}
    closing = {"BTC": ("0.08", "0.08"), "USDT": ("11300", "11300")}
    assert publish(store, None, now, opening)
    entered, release = Event(), Event()

    def hold(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT daily_balance_snapshots.day"):
            entered.set()
            assert release.wait(5)

    settings = AdminSettings(True, "operator", "offline-password", basic_auth_enabled=True)
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(None, settings, store=store).register(app)
    event.listen(store.engine, "before_cursor_execute", hold)
    try:
        async with TestClient(TestServer(app), headers={
            "Authorization": BasicAuth("operator", "offline-password").encode(),
        }) as client:
            comparison = asyncio.create_task(client.get(ADMIN_PREFIX + "/balances/changes"))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert await asyncio.wait_for(asyncio.to_thread(
                    publish, store, None, datetime.now(timezone.utc), closing), 2)
                response = await asyncio.wait_for(client.get(ADMIN_PREFIX + "/balances"), 2)
                assert response.status == 200
                body = await response.json()
                assert body["accounts"][0]["balances"][0]["cash_balance"] == "0.08"
            finally:
                release.set()
                response = await comparison
            assert response.status == 200
            body = await response.json()
            assert body["rows"][0]["closing_cash"] == "0.1"
            assert body["rows"][0]["delta_cash"] is None
    finally:
        event.remove(store.engine, "before_cursor_execute", hold)
