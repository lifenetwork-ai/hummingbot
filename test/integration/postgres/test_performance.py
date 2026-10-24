"""Query budgets and reproducible local measurements against disposable PostgreSQL.

Set HBOT_PERFORMANCE_REPORT to save measurements. Timings are evidence, not flaky
pass/fail thresholds. Data volume and query counts are deterministic.
"""
import gc
import json
import math
import os
import time
import tracemalloc
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from test.integration.postgres import test_ownership as ownership_tests, test_recorder as recorder_tests
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import event, func, inspect, select, text

from hummingbot.cli.data import get_trades
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.db_migration.postgres.runner import SCRIPTS, bootstrap_postgres
from hummingbot.model.executors import Executors
from hummingbot.model.position import Position
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.executors import CloseType

recording = recorder_tests.recording
owned = ownership_tests.owned


@contextmanager
def queries(engine):
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def measure(operation, samples=7):
    operation()  # Warm pool, database buffers and Python imports.
    durations = []
    for _ in range(samples):
        gc.collect()
        start = time.perf_counter()
        operation()
        durations.append((time.perf_counter() - start) * 1000)
    gc.collect()
    tracemalloc.start()
    try:
        operation()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    ordered = sorted(durations)
    return {"samples": samples, "p50_ms": ordered[len(ordered) // 2],
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1],
            "python_peak_mib": peak / (1024 * 1024)}


def report(name, values):
    print(f"\n{name}: {json.dumps(values, sort_keys=True)}")
    target = os.environ.get("HBOT_PERFORMANCE_REPORT")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        data[name] = values
        path.write_text(json.dumps(data, indent=2) + "\n")


def seed_executors(engine):
    # 20k realistic JSON snapshots, 2k belonging to this strategy (20 controllers).
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        connection.execute(text("""
            INSERT INTO executors
                (id, timestamp, type, close_type, close_timestamp, status, config,
                 net_pnl_pct, net_pnl_quote, cum_fees_quote, filled_amount_quote,
                 is_active, is_trading, custom_info, controller_id)
            SELECT 'executor-' || n, n, 'position_executor',
                   CASE WHEN n % 4 = 0 THEN :hold ELSE :close END, n, 3,
                   json_build_object('id', 'executor-' || n, 'timestamp', n,
                       'type', 'position_executor', 'connector_name', 'okx',
                       'trading_pair', 'BTC-USDT', 'side', 1, 'amount', '0.1'),
                   .01, CASE WHEN n % 2 = 0 THEN .1 ELSE -.3 END, .01, 10.1,
                   false, false, json_build_object('detail', repeat('x', 1024)),
                   CASE WHEN n <= 2000 THEN 'selected-' || (n % 20)
                        ELSE 'other-' || (n % 100) END
            FROM generate_series(1, 20000) AS n
        """), {"hold": CloseType.POSITION_HOLD.value, "close": CloseType.TAKE_PROFIT.value})
        connection.execute(text("ANALYZE executors"))


def test_performance_v2_startup(recording, pg_engine):
    recorder, _, _ = recording
    seed_executors(pg_engine)
    strategy = SimpleNamespace(controllers={f"selected-{i}": None for i in range(20)},
                               markets={"okx": {"BTC-USDT"}})
    # Reference the previous whole-row path, including its Float -> Decimal semantics.
    expected = {}
    for item in recorder.get_all_executors():
        if item.controller_id not in strategy.controllers:
            continue
        pnl, volume, counts = expected.setdefault(item.controller_id, [Decimal(0), Decimal(0), {}])
        if item.close_type != CloseType.POSITION_HOLD:
            pnl += item.net_pnl_quote
            volume += item.filled_amount_quote
        if item.close_type:
            counts[item.close_type] = counts.get(item.close_type, 0) + 1
        expected[item.controller_id] = [pnl, volume, counts]

    def startup():
        return ExecutorOrchestrator(strategy)

    with queries(pg_engine) as statements:
        orchestrator = startup()
    assert len(statements) == 2  # Bulk executor + position reads, independent of controllers.
    assert "executors.config" not in statements[0] and "executors.custom_info" not in statements[0]
    assert "WHERE executors.controller_id IN" in statements[0]
    for controller_id, (pnl, volume, counts) in expected.items():
        cached = orchestrator.cached_performance[controller_id]
        assert (cached.realized_pnl_quote, cached.volume_traded, cached.close_type_counts) == (pnl, volume, counts)
    report("v2_startup_20000_executors_2000_selected", {**measure(startup), "queries": len(statements)})
    # The existing cache serves live PnL; adding another database/cache trip is a regression.
    with queries(pg_engine) as live_statements:
        for _ in range(100):
            for controller_id in strategy.controllers:
                current = orchestrator.generate_performance_report(controller_id)
                assert current.realized_pnl_quote == expected[controller_id][0]
    assert live_statements == []
    # Empty strategies must not scan history. A one-controller strategy still reads in bulk.
    strategy.controllers = {}
    with queries(pg_engine) as empty_statements:
        assert startup().cached_performance == {}
    assert empty_statements == []
    strategy.controllers = {"selected-1": None}
    with queries(pg_engine) as single_statements:
        assert set(startup().cached_performance) == {"selected-1"}
    assert len(single_statements) == 2
    stream = recorder.iter_executor_performance(["selected-1"])
    next(stream)
    stream.close()
    assert pg_engine.pool.checkedout() == 0


def test_performance_position_scope_overrides_and_missing_history(recording, pg_engine):
    with pg_engine.begin() as connection:
        for cid in ("selected", "overridden", "unrelated"):
            connection.execute(Position.__table__.insert().values(
                id=cid, controller_id=cid, connector_name="okx", trading_pair="BTC-USDT", side="BUY",
                timestamp=1700000000000, volume_traded_quote=Decimal("10"), amount=Decimal(".1"),
                breakeven_price=Decimal("100"), unrealized_pnl_quote=Decimal(0),
                realized_pnl_quote=Decimal(".2"), cum_fees_quote=Decimal(".01")))
    strategy = SimpleNamespace(controllers={"selected": None, "overridden": None, "new": None},
                               markets={"okx": {"BTC-USDT"}})
    with queries(pg_engine) as statements:
        orchestrator = ExecutorOrchestrator(strategy, initial_positions_by_controller={"overridden": []})
    assert len(statements) == 2
    assert "WHERE positions.controller_id IN" in statements[1]
    assert len(orchestrator.positions_held["selected"]) == 1
    assert orchestrator.positions_held["selected"][0].net_amount_base == Decimal(".1")
    assert orchestrator.positions_held["overridden"] == orchestrator.positions_held["new"] == []
    assert "unrelated" not in orchestrator.positions_held
    assert orchestrator.cached_performance["new"].realized_pnl_quote == 0
    # Empty histories also release their cursor and pool checkout.
    stream = recording[0].iter_executor_performance(["selected"])
    assert list(stream) == []
    assert pg_engine.pool.checkedout() == 0


@pytest.mark.parametrize("invalid", [
    {"net_pnl_quote": float("nan")}, {"filled_amount_quote": float("inf")}, {"close_type": 999},
])
def test_performance_invalid_history_fails_closed_and_releases_cursor(recording, pg_engine, invalid):
    values = dict(id="invalid", controller_id="selected", timestamp=1, type="position_executor",
                  close_type=3, status=3, config={}, custom_info={}, net_pnl_quote=.1,
                  net_pnl_pct=.01, cum_fees_quote=.01, filled_amount_quote=10,
                  is_active=False, is_trading=False)
    with pg_engine.begin() as connection:
        connection.execute(Executors.__table__.insert().values({**values, **invalid}))
    with pytest.raises(ValueError):
        ExecutorOrchestrator(SimpleNamespace(controllers={"selected": None}, markets={}))
    assert pg_engine.pool.checkedout() == 0


def seed_orders(engine, count=100000):
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        connection.execute(text("""
            INSERT INTO orders (id, config_file_path, strategy, market, symbol, base_asset,
                quote_asset, creation_timestamp, order_type, amount, leverage, price,
                last_status, last_update_timestamp, creation_pending)
            SELECT 'history-' || n, 'test.yml', 'pure_market_making', 'okx', 'BTC-USDT',
                'BTC', 'USDT', 1700000000000 + n, 'LIMIT', 100000, 1, 100000000,
                'BuyOrderCompleted', 1700000001000 + n, n = :count
            FROM generate_series(1, :count) AS n
        """), {"count": count})
        connection.execute(text("ANALYZE orders"))


def test_performance_pending_intent_lookup(recording, pg_engine):
    seed_orders(pg_engine)
    values = {}
    with pg_engine.connect() as connection:
        for name, sql in {
            "global": "SELECT id, market FROM orders WHERE creation_pending IS true",
            "recorder": "SELECT id FROM orders WHERE config_file_path = 'test.yml' "
                        "AND market = 'okx' AND creation_pending IS true",
        }.items():
            query = text(sql)

            def lookup():
                return connection.execute(query).all()

            assert lookup()[0][0] == "history-100000"
            values[name] = {**measure(lookup, samples=25),
                            "plan": connection.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)).scalar()}
            plan = values[name]["plan"][0]["Plan"]
            assert plan["Index Name"] == "idx_orders_creation_pending_config_file_path_market"
            assert plan["Actual Rows"] == 1
    report("pending_lookup_100000_orders", values)


def test_performance_history_has_no_n_plus_one(recording, pg_engine):
    seed_orders(pg_engine, count=1000)
    with pg_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO trade_fills (config_file_path, strategy, market, symbol, base_asset,
                quote_asset, timestamp, order_id, trade_type, order_type, price, amount,
                leverage, trade_fee, trade_fee_in_quote, exchange_trade_id, position)
            SELECT config_file_path, strategy, market, symbol, base_asset, quote_asset,
                last_update_timestamp, id, 'BUY', order_type, price, amount, leverage,
                '{"percent": "0", "flat_fees": []}', 0, 'fill-' || id, 'NIL'
            FROM orders
        """))
    with patch("hummingbot.cli.bot.postgres_mode", return_value=True), \
            patch("hummingbot.cli.bot.state_store", return_value=SimpleNamespace(engine=pg_engine)):
        for count in (1, 1000):
            with queries(pg_engine) as statements:
                trades = get_trades("unused", config_file_path="test.yml", limit=count)
                # Includes reading the related Order after the session was closed.
                frame = TradeFill.to_pandas(trades)
            assert len(trades) == len(frame) == count
            assert trades[0].amount == Decimal(".1") and trades[0].price == Decimal("100")
            assert len(statements) == 1


def test_performance_index_upgrade_replays_on_populated_schema(recording, pg_engine):
    seed_orders(pg_engine, count=1000)
    with pg_engine.begin() as connection:
        connection.execute(text("DROP INDEX idx_orders_creation_pending_config_file_path_market"))
        connection.execute(text("DROP INDEX idx_executors_controller_id"))
    for automatic in (True, True, False):
        bootstrap_postgres(pg_engine, auto_migrate=automatic)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM orders")).scalar() == 1000
        assert connection.execute(text("SELECT id FROM orders WHERE creation_pending IS true")).scalar() == "history-1000"
    assert "idx_executors_controller_id" in {i["name"] for i in inspect(pg_engine).get_indexes("executors")}


def test_recorder_bounded_history_keeps_recent_orders_for_each_market(recording, pg_engine):
    recorder, _, market = recording
    seed_orders(pg_engine, count=2005)
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE orders SET exchange_order_id = 'exchange-' || id"))
        connection.execute(text("""
            INSERT INTO trade_fills (config_file_path, strategy, market, symbol, base_asset,
                quote_asset, timestamp, order_id, trade_type, order_type, price, amount,
                leverage, trade_fee, trade_fee_in_quote, exchange_trade_id, position)
            SELECT config_file_path, strategy, source.market, symbol, base_asset, quote_asset,
                last_update_timestamp + source.offset_ms, id, 'BUY', order_type, price, amount, leverage,
                '{"percent": "0", "flat_fees": []}', 0, source.market || id, 'NIL'
            FROM orders CROSS JOIN (VALUES ('okx', 0), ('okx_perpetual', 100000)) AS source(market, offset_ms)
        """))
    # The previous ascending LIMIT loaded the oldest 2000, losing the latest ACK mappings.
    recent = recorder.get_orders_for_config_and_market("test.yml", market, True, 2000)
    assert [o.id for o in recent] == [f"history-{i}" for i in range(6, 2006)]
    assert len(recorder.get_orders_for_config_and_market("test.yml", market)) == 2005
    # A busy second connector must not displace this connector's deduplication window.
    recent_fills = recorder.get_trades_for_config("test.yml", 2000, market="okx")
    assert len(recent_fills) == 2000
    assert {fill.market for fill in recent_fills} == {"okx"}
    assert recent_fills[0].order_id == "history-2005"
    assert recent_fills[-1].order_id == "history-6"
    assert {fill.market for fill in recorder.get_trades_for_config("test.yml", 2000)} == {"okx_perpetual"}


def test_performance_fenced_fill_transaction(owned, pg_engine):
    """Measure the actual recorder with ownership/fencing enabled, not just SELECT 1."""
    ownership_tests.intent(owned)
    owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, recorder_tests.create())
    seed_orders(pg_engine)
    counter = 0

    def record():
        nonlocal counter
        counter += 1
        owned.recorder._did_fill_order(
            MarketEvent.OrderFilled.value, owned.market,
            recorder_tests.fill(exchange_trade_id=f"benchmark-{counter}", amount=Decimal(".0001")))

    measurements = {}
    # Both variants run in the same disposable database with identical safety checks.
    # Only this test drops the index to reproduce pre-optimization behavior.
    for indexed in (False, True):
        with pg_engine.begin() as connection:
            if indexed:
                connection.exec_driver_sql((SCRIPTS / "008_add_recovery_lookup_indexes.sql").read_text())
            else:
                connection.execute(text("DROP INDEX idx_orders_creation_pending_config_file_path_market"))
        record()  # First fill also inserts the OrderFilled status and updates Order.
        with queries(pg_engine) as statements:
            record()
        measurements["indexed" if indexed else "unindexed"] = {**measure(record, samples=25),
                                                               "statements": len(statements)}
    assert measurements["indexed"]["statements"] == measurements["unindexed"]["statements"] == 11
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM trade_fills")).scalar() == counter
        assert connection.execute(select(func.sum(TradeFill.amount))).scalar() == counter * Decimal(".0001")
    assert owned.manager.persistence_healthy
    report("fenced_fill_100000_historical_orders", measurements)
