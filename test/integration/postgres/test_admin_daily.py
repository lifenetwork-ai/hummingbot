from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.funding_payment import FundingPayment
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill


def millis(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


@pytest.fixture
def daily_store(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    return AdminStore(state)


def add_fill(engine, identity, market, timestamp, fee, converted):
    with Session(engine) as session, session.begin():
        order = Order(id=identity, config_file_path="life.yaml", strategy="v2_with_controllers", market=market,
                      symbol="LIFE-USDT", base_asset="LIFE", quote_asset="USDT", creation_timestamp=timestamp,
                      order_type="LIMIT_MAKER", amount=Decimal("10"), price=Decimal("0.1"), leverage=1,
                      last_status="BuyOrderCompleted", last_update_timestamp=timestamp)
        session.add(order)
        session.add(TradeFill(order=order, config_file_path="life.yaml", strategy=order.strategy, market=market,
                              symbol=order.symbol, base_asset="LIFE", quote_asset="USDT", timestamp=timestamp,
                              trade_type="BUY", order_type="LIMIT_MAKER", price=Decimal("0.1"), amount=Decimal("10"),
                              leverage=1, trade_fee={"percent": "0", "flat_fees": [{"token": "USDT", "amount": fee}]},
                              trade_fee_in_quote=converted, exchange_trade_id=identity))


def test_daily_groups_local_event_day_markets_fees_rebates_and_observed_funding(daily_store):
    before = millis("2026-01-01T16:59:59.999+00:00")
    boundary = millis("2026-01-01T17:00:00+00:00")
    add_fill(daily_store.engine, "spot-fee", "okx", before, "0.1", Decimal("0.1"))
    add_fill(daily_store.engine, "spot-rebate", "okx", boundary, "-0.02", Decimal("-0.02"))
    add_fill(daily_store.engine, "perp-unconverted", "okx_perpetual", boundary, "0.3", None)
    add_fill(daily_store.engine, "legacy-zero", "okx_perpetual", boundary + 1, "0.4", Decimal("0"))
    with Session(daily_store.engine) as session, session.begin():
        session.add(FundingPayment(timestamp=boundary, config_file_path="life.yaml", market="okx_perpetual",
                                   symbol="LIFE-USDT", rate=0.01, amount=-0.5))
    result = daily_store.daily(start_day="2026-01-01", end_day="2026-01-03", reporting_timezone="Asia/Ho_Chi_Minh")
    spot, perp = result["markets"]
    assert spot["market"] == "spot" and perp["market"] == "perpetual"
    assert [str(row["day"]) for row in spot["days"]] == ["2026-01-01", "2026-01-02"]
    assert spot["days"][0]["buy_volume_quote"] == Decimal("1")
    assert spot["days"][1]["fees"][0]["rebates"] == Decimal("0.02")
    assert spot["funding_status"] == "not_applicable"
    assert perp["days"][0]["unconverted_fill_count"] == 2
    assert perp["days"][0]["fee_in_usdt"] is None
    assert perp["days"][0]["funding_paid"] == Decimal("0.5")
    # Inserted late: aggregate from event time, with no cached total to invalidate.
    add_fill(daily_store.engine, "late", "okx", before, "0.2", Decimal("0.2"))
    again = daily_store.daily(start_day="2026-01-01", end_day="2026-01-03", reporting_timezone="Asia/Ho_Chi_Minh", market="spot")
    assert again["markets"][0]["days"][0]["fill_count"] == 2
    assert again["markets"][0]["days"][0]["fee_in_usdt"] == Decimal("0.3")


@pytest.mark.parametrize("zone,start_day,end_day,start,end", [
    ("America/New_York", "2026-03-08", "2026-03-09", "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
    ("America/New_York", "2026-11-01", "2026-11-02", "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
    ("UTC", "1969-12-31", "1970-01-01", "1969-12-31T00:00:00+00:00", "1970-01-01T00:00:00+00:00"),
    ("UTC", "9998-06-01", "9998-06-02", "9998-06-01T00:00:00+00:00", "9998-06-02T00:00:00+00:00"),
])
def test_daily_preserves_dst_day_and_half_open_millisecond_boundaries(daily_store, zone, start_day, end_day, start, end):
    for identity, timestamp in (("before", millis(start) - 1), ("first", millis(start)),
                                ("last", millis(end) - 1), ("after", millis(end))):
        add_fill(daily_store.engine, identity, "okx", timestamp, "0.00000001", Decimal("0.00000001"))
    result = daily_store.daily(start_day=start_day, end_day=end_day, reporting_timezone=zone, market="spot")
    days = result["markets"][0]["days"]
    assert len(days) == 1 and str(days[0]["day"]) == start_day
    assert days[0]["fill_count"] == 2
    assert days[0]["buy_volume_quote"] == Decimal("2")
    assert days[0]["fee_in_usdt"] == Decimal("0.00000002")


def test_daily_snapshot_upsert_is_fenced_rate_limited_and_keeps_timezone_gaps(daily_store):
    from hummingbot.model.admin_reporting import save_daily_balances
    observation = datetime.now(timezone.utc) - timedelta(seconds=10)
    accounts = [dict(account_id="17", environment="live", identity_verified=True, balances=[dict(
        currency="USDT", cash_balance="100", available_cash="80", equity="110", available_equity="90",
        observed_at=observation.timestamp(), connector="okx", source="okx_account_balance")])]
    with daily_store.engine.begin() as connection:
        assert save_daily_balances(connection, accounts, "Asia/Ho_Chi_Minh") == 1
        assert save_daily_balances(connection, accounts, "Asia/Ho_Chi_Minh") == 0
        accounts[0]["balances"][0]["observed_at"] += 1
        accounts[0]["balances"][0]["cash_balance"] = "200"
        assert save_daily_balances(connection, accounts, "Asia/Ho_Chi_Minh") == 0
    bootstrap_postgres(daily_store.engine, auto_migrate=True)
    with daily_store.engine.begin() as connection:
        assert save_daily_balances(connection, accounts, "Asia/Ho_Chi_Minh") == 0
        assert save_daily_balances(connection, accounts, "UTC") == 1
    with pytest.raises(ValueError):
        daily_store.daily(start_day="2026-01-01", end_day="2027-01-01", reporting_timezone="UTC")


def test_snapshot_write_occurs_only_after_successful_owner_fence_and_does_not_invent_gaps(daily_store):
    from sqlalchemy import func, select, text, update

    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot
    owner = BotOwnership(daily_store.engine, daily_store.bot_id)
    assert owner.try_acquire()
    observed = datetime.now(timezone.utc) - timedelta(seconds=10)
    account = dict(account_id="17", identity_verified=True, environment="live", balances=[dict(
        currency="LIFE", connector="okx", source="okx_account_balance", observed_at=observed.timestamp(),
        cash_balance="200", available_cash="180", equity="200", available_equity="180")])
    status = dict(reporting={"accounts": [account]}, reporting_timezone="UTC")
    try:
        assert not daily_store.store.publish(owner_uuid="old-owner", owner_generation=owner.generation, version=0, status=status)
        with daily_store.engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(DailyBalanceSnapshot)).scalar_one() == 0
        assert daily_store.store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=0, status=status)
        with daily_store.engine.begin() as connection:
            connection.execute(update(DailyBalanceSnapshot).values(updated_at=func.clock_timestamp() - text("interval '61 seconds'")))
        account["balances"][0].update(cash_balance="300", observed_at=observed.timestamp() + 1)
        assert daily_store.store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=0, status=status)
        today = observed.date()
        result = daily_store.daily(start_day=str(today - timedelta(days=2)), end_day=str(today + timedelta(days=1)), reporting_timezone="UTC")
        assert len(result["balances"]) == 1
        assert result["balances"][0]["cash_balance"] == Decimal("300")
        assert result["balances"][0]["observed_at"].utcoffset() == timedelta(0)
    finally:
        owner.release()


def test_daily_totals_use_one_database_snapshot_during_concurrent_fill(daily_store):
    from sqlalchemy import event
    timestamp = millis("2026-01-01T12:00:00+00:00")
    add_fill(daily_store.engine, "first", "okx", timestamp, "0.1", Decimal("0.1"))
    inserted = []

    def arrive_between_aggregates(connection, cursor, statement, *args):
        if "AS fill_count" in statement and not inserted:
            inserted.append(True)
            add_fill(daily_store.engine, "concurrent", "okx", timestamp, "0.2", Decimal("0.2"))

    event.listen(daily_store.engine, "after_cursor_execute", arrive_between_aggregates)
    try:
        result = daily_store.daily(start_day="2026-01-01", end_day="2026-01-02", reporting_timezone="UTC", market="spot")
        assert inserted
        day = result["markets"][0]["days"][0]
        assert day["fill_count"] == 1
        assert day["fees"][0]["paid"] == Decimal("0.1")
    finally:
        event.remove(daily_store.engine, "after_cursor_execute", arrive_between_aggregates)
    assert daily_store.daily(start_day="2026-01-01", end_day="2026-01-02", reporting_timezone="UTC", market="spot")["markets"][0]["days"][0]["fill_count"] == 2


def test_daily_multiple_fee_currencies_and_empty_fees_do_not_multiply_volumes(daily_store):
    from sqlalchemy import update
    timestamp = millis("2026-01-01T12:00:00+00:00")
    for identity in ("multi", "empty", "percent"):
        add_fill(daily_store.engine, identity, "okx", timestamp, "0", Decimal("0"))
    with daily_store.engine.begin() as connection:
        connection.execute(update(TradeFill).where(TradeFill.exchange_trade_id == "multi").values(trade_fee={
            "percent": "0", "flat_fees": [{"token": "USDT", "amount": "0.123456789012345678"},
                                          {"token": "LIFE", "amount": "2"},
                                          {"token": "USDT", "amount": "-0.003456789012345678"}]}))
        connection.execute(update(TradeFill).where(TradeFill.exchange_trade_id == "empty").values(
            trade_fee={"percent": "0", "flat_fees": []}))
        connection.execute(update(TradeFill).where(TradeFill.exchange_trade_id == "percent").values(
            trade_type="SELL", trade_fee={"percent": "0.01", "flat_fees": []}, trade_fee_in_quote=None))
    day = daily_store.daily(start_day="2026-01-01", end_day="2026-01-02", reporting_timezone="UTC", market="spot")["markets"][0]["days"][0]
    assert day["fill_count"] == 3
    assert (day["buy_volume_base"], day["sell_volume_base"], day["buy_volume_quote"], day["sell_volume_quote"]) == (
        Decimal("20"), Decimal("10"), Decimal("2"), Decimal("1"))
    assert (day["converted_fill_count"], day["unconverted_fill_count"], day["percentage_fee_fill_count"]) == (1, 2, 1)
    assert day["fees"] == [dict(currency="LIFE", paid=Decimal("2"), rebates=Decimal("0"), net=Decimal("2")),
                           dict(currency="USDT", paid=Decimal("0.123456789012345678"),
                                rebates=Decimal("0.003456789012345678"), net=Decimal("0.12"))]


def test_snapshot_batch_filters_ineligible_rows_and_keeps_latest_duplicate(daily_store):
    from sqlalchemy import select

    from hummingbot.model.admin_reporting import save_daily_balances
    from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot
    observed = datetime.now(timezone.utc) - timedelta(seconds=10)

    def balance(currency, offset=0, **fields):
        return dict(currency=currency, connector="okx", source="okx_account_balance",
                    observed_at=observed.timestamp() + offset, **fields)

    accounts = [dict(account_id="17", identity_verified=True, environment="demo", balances=[
        balance("LIFE", cash_balance="100"), balance("LIFE", 1, cash_balance="200"),
        balance("LIFE", 100, cash_balance="999"), balance("STALE", -120), balance("FUTURE", 100),
        balance("ABSENT", present_in_latest_snapshot=False), balance("NULL")]),
        dict(account_id="unverified", identity_verified=False, environment="demo", balances=[balance("USDT")])]
    with daily_store.engine.begin() as connection:
        assert save_daily_balances(connection, accounts, "UTC") == 2
        rows = connection.execute(select(DailyBalanceSnapshot).order_by(DailyBalanceSnapshot.currency)).mappings().all()
        assert [row["currency"] for row in rows] == ["LIFE", "NULL"]
        assert rows[0]["cash_balance"] == Decimal("200")
        assert rows[1]["cash_balance"] is rows[1]["equity"] is None
        assert save_daily_balances(connection, [], "UTC") == 0
