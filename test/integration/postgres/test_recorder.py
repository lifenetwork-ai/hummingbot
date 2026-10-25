import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import JSON, BigInteger, Boolean, Float, Integer, Numeric, Sequence, String, Text, event, text
from sqlalchemy.exc import DBAPIError, StatementError

from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import (
    BuyOrderCreatedEvent,
    FundingPaymentCompletedEvent,
    MarketEvent,
    OrderFilledEvent,
)
from hummingbot.model import get_declarative_base
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.decimal_type_decorator import SqliteDecimal
from hummingbot.model.funding_payment import FundingPayment
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.model.trade_fill import TradeFill


@pytest.fixture
def recording(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    previous = MarketsRecorder._shared_instance
    with patch.object(SQLConnectionManager, "_postgres_engine", pg_engine):
        manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    market = MagicMock()
    market.display_name = "okx"
    market.name = "okx"
    market.tracking_states = {"order-1": {"state": "pending"}}
    market.persisted_fills = set()
    market.add_trade_fills_from_market_recorder.side_effect = market.persisted_fills.update
    recorder = MarketsRecorder(manager, [market], "test.yml", "pure_market_making",
                               MarketDataCollectionConfigMap(market_data_collection_enabled=False))
    try:
        yield recorder, manager, market
    finally:
        MarketsRecorder._shared_instance = previous
        loop.close()
        asyncio.set_event_loop(None)


def fill(**changes):
    values = dict(timestamp=1700000000.123, order_id="order-1", trading_pair="BTC-USDT",
                  trade_type=TradeType.BUY, order_type=OrderType.LIMIT, price=Decimal("123.456789"),
                  amount=Decimal("0.012345"), exchange_trade_id="fill-1",
                  trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", Decimal("0.2"))]))
    values.update(changes)
    return OrderFilledEvent(**values)


def create():
    return BuyOrderCreatedEvent(timestamp=1700000000, creation_timestamp=1699999999,
                                type=OrderType.LIMIT, trading_pair="BTC-USDT", amount=Decimal("0.1"),
                                price=Decimal("120"), order_id="order-1", exchange_order_id="exchange-1")


def test_fill_before_create_duplicate_events_and_recovery(recording):
    recorder, manager, market = recording
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        order = session.get(Order, "order-1")
        assert order.creation_pending is True
        assert order.last_status == "OrderFilled"
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(exchange_trade_id="fill-2", amount=Decimal("0.02")))
    with manager.get_new_session() as session:
        order = session.get(Order, "order-1")
        assert order.creation_pending is False
        assert order.amount == Decimal("0.1") and order.price == Decimal("120")
        assert order.last_status == "OrderFilled"
        assert order.creation_timestamp == 1699999999000
        assert session.query(TradeFill).count() == 2
        assert session.query(OrderStatus).count() == 2
        stored = session.query(TradeFill).filter_by(exchange_trade_id="fill-1").one()
        assert stored.price == Decimal("123.456789") and stored.amount == Decimal("0.012345")
        assert stored.trade_fee_in_quote == Decimal("0.2")
        assert stored.timestamp == 1700000000123
        assert session.query(MarketState).one().saved_state == market.tracking_states
        assert len(order.trade_fills) == 2
    market.restore_tracking_states.reset_mock()
    recorder.restore_market_states("test.yml", market)
    market.restore_tracking_states.assert_called_once_with(market.tracking_states)
    assert len(market.persisted_fills) == 2
    assert manager.persistence_healthy


def test_fee_conversion_failure_is_unavailable_not_a_zero_cost_trade(recording):
    recorder, manager, market = recording
    with patch.object(AddedToCostTradeFee, "fee_amount_in_token", side_effect=ValueError("rate unavailable")):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        stored = session.query(TradeFill).one()
        assert stored.trade_fee_in_quote is None
        assert stored.trade_fee["flat_fees"] == [{"token": "USDT", "amount": "0.2"}]
    assert manager.persistence_healthy


def test_commit_failure_rolls_back_all_state_without_acknowledgment(recording):
    recorder, manager, market = recording

    def fail_commit(session):
        raise RuntimeError("injected commit failure")

    event.listen(manager._session_cls, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    finally:
        event.remove(manager._session_cls, "before_commit", fail_commit)
    assert not market.persisted_fills
    assert not manager.persistence_healthy
    with manager.get_new_session() as session:
        for model in (Order, OrderStatus, TradeFill, MarketState):
            assert session.query(model).count() == 0
    with pytest.raises(RuntimeError, match="Persistence failed"):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    market.tracking_states = {"order-1": {"unpersisted_fill": True}}
    with pytest.raises(RuntimeError, match="Persistence failed"):
        with manager.get_new_session() as session, session.begin():
            recorder.save_market_states("test.yml", market, session)
    with pytest.raises(RuntimeError, match="Persistence failed"):
        with manager.get_new_session() as session, session.begin():
            session.execute(text("SELECT 1"))
            session.query(Order).update({Order.last_status: "unsafe"})
    with manager.get_new_session() as session:
        assert session.query(MarketState).count() == 0
    # Recovery reconstructs persistence/tracking from DB and exchange truth in a
    # fresh process; the failed manager must never persist its advanced cache.
    with patch.object(SQLConnectionManager, "_postgres_engine", manager.engine):
        fresh_manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    recorder._sql_manager = fresh_manager
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1
    assert len(market.persisted_fills) == 1
    # A later write alone cannot reopen the send gate without reconciliation.
    with pytest.raises(RuntimeError, match="reconciliation"):
        manager.assert_persistence_healthy()


def test_conflicting_duplicate_fill_is_rejected(recording):
    recorder, manager, market = recording
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with pytest.raises(ValueError, match="conflicting"):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(amount=Decimal("0.9")))
    with manager.get_new_session() as session:
        assert session.query(TradeFill).one().amount == Decimal("0.012345")
    assert not manager.persistence_healthy


def test_precision_partial_fill_replay_and_exact_daily_totals(recording):
    from hummingbot.model.admin_reporting import daily

    recorder, manager, market = recording
    amount = Decimal("0.00024818")
    price = Decimal("80584.123456789012345678")
    fee = Decimal("0.000000198544123456")
    first = fill(amount=amount, price=price,
                 trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", fee)]))
    # The real Demo failure also made replay look like a conflicting duplicate.
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, first)
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, first)
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market,
                             fill(exchange_trade_id="fill-2", amount=Decimal("0.00000001"), price=price,
                                  trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", fee)])))
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    with manager.get_new_session() as session:
        rows = session.query(TradeFill).order_by(TradeFill.exchange_trade_id).all()
        assert len(rows) == 2
        assert rows[0].amount == amount and rows[0].price == price
        assert rows[1].amount == Decimal("0.00000001")
        assert all(row.trade_fee_in_quote == fee for row in rows)
    stats = daily(manager.engine, start_day="2023-11-14", end_day="2023-11-15", reporting_timezone="UTC")
    day = stats["markets"][0]["days"][0]
    assert day["buy_volume_base"] == Decimal("0.00024819")
    assert day["buy_volume_quote"] == price * Decimal("0.00024819")
    assert day["fee_in_usdt"] == fee * 2
    assert manager.persistence_healthy and len(market.persisted_fills) == 2


def test_actual_db_session_loss_before_commit_keeps_fill_replayable(recording):
    recorder, manager, market = recording

    def terminate_backend(session):
        pid = session.connection().connection.driver_connection.get_backend_pid()
        with manager.engine.connect() as killer:
            killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            killer.commit()

    event.listen(manager._session_cls, "before_commit", terminate_backend)
    try:
        with pytest.raises(DBAPIError):
            recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    finally:
        event.remove(manager._session_cls, "before_commit", terminate_backend)
    assert not market.persisted_fills
    assert not manager.persistence_healthy
    with pytest.raises(RuntimeError, match="Persistence failed"):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with patch.object(SQLConnectionManager, "_postgres_engine", manager.engine):
        recorder._sql_manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1
        assert session.query(OrderStatus).count() == 1


@pytest.mark.parametrize("price", [Decimal("1E+131072"), Decimal("Infinity")])
def test_decimal_overflow_rolls_back_and_never_marks_fill_persisted(recording, price):
    recorder, manager, market = recording
    with pytest.raises(StatementError):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(price=price))
    assert not market.persisted_fills
    assert not manager.persistence_healthy


def test_same_timestamp_funding_across_markets_and_pairs(recording):
    recorder, manager, market = recording
    for name in ("okx", "okx_perpetual"):
        market.display_name = name
        for pair in ("BTC-USDT", "ETH-USDT"):
            evt = FundingPaymentCompletedEvent(timestamp=1700000000000, market=name, trading_pair=pair,
                                               amount=Decimal("1.23"), funding_rate=Decimal("0.001"))
            for _ in range(2):
                recorder._did_complete_funding_payment(MarketEvent.FundingPaymentCompleted.value, market, evt)
    with manager.get_new_session() as session:
        assert session.query(FundingPayment).count() == 4


def test_runtime_query_budget_and_exact_config_filter(recording):
    recorder, manager, market = recording
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    assert recorder.get_trades_for_config("test.yml")
    assert recorder.get_trades_for_config("test") == []
    with manager.engine.connect() as connection:
        assert connection.execute(text("SHOW statement_timeout")).scalar() == "2s"
        assert connection.execute(text("SHOW lock_timeout")).scalar() == "1s"
    with pytest.raises(RuntimeError, match="export"):
        recorder.append_to_csv(None)


def test_all_registered_models_roundtrip(pg_engine):
    from datetime import date, datetime, timezone

    from sqlalchemy import Date, DateTime
    bootstrap_postgres(pg_engine, auto_migrate=True)
    rows = []
    inserted_values = {}
    classes = {mapper.local_table.name: mapper.class_ for mapper in get_declarative_base().registry.mappers}
    from sqlalchemy.orm import Session
    with Session(pg_engine) as session, session.begin():
        for table in get_declarative_base().metadata.sorted_tables:
            values = {}
            for column in table.columns:
                if isinstance(column.default, Sequence) or column.server_default is not None:
                    continue
                if column.nullable:
                    continue
                kind = column.type
                if column.foreign_keys:
                    target = next(iter(column.foreign_keys)).column
                    value = inserted_values[target.table.name][target.name]
                elif isinstance(kind, DateTime):
                    value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
                elif isinstance(kind, Date):
                    value = date(2026, 1, 2)
                elif isinstance(kind, SqliteDecimal):
                    value = Decimal("123.456789012345678901234567890123")
                elif isinstance(kind, (BigInteger, Integer)):
                    value = 1700000000123 if isinstance(kind, BigInteger) else 1
                elif isinstance(kind, Boolean):
                    value = True
                elif isinstance(kind, Float):
                    value = 0.25
                elif isinstance(kind, Numeric):
                    value = Decimal("1.123456789012345678")
                elif isinstance(kind, JSON):
                    value = {"nested": ["value", 1, True]}
                elif isinstance(kind, (Text, String)):
                    value = "orders_id" if column.name == "order_id" else f"{table.name}_{column.name}"
                else:
                    raise AssertionError(f"Missing independent round-trip value for {table.name}.{column.name}")
                values[column.name] = value
            obj = classes[table.name](**values)
            session.add(obj)
            # Flush in dependency order; the same order identity is used by FKs.
            session.flush()
            rows.append((classes[table.name], obj, values))
            inserted_values[table.name] = values
    with Session(pg_engine) as session:
        for model, _, expected in rows:
            stored = session.query(model).one()
            for key, value in expected.items():
                assert getattr(stored, key) == value, f"{model.__name__}.{key}"


def test_local_recorder_latency_budget(recording):
    recorder, manager, market = recording
    samples = []
    for number in range(25):
        start = time.perf_counter()
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(exchange_trade_id=f"latency-{number}"))
        samples.append((time.perf_counter() - start) * 1000)
    p95 = sorted(samples)[int(len(samples) * 0.95)]
    print(f"Local PostgreSQL recorder p95={p95:.2f}ms (25 synchronous fill transactions)")
    assert p95 < 100, "Local recorder exceeds the initial 100ms p95 budget; profile before enabling the service."
