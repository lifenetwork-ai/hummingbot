"""Managed startup streams real PostgreSQL history on a worker-owned session."""
import asyncio
import os
import threading
import time
from contextlib import closing
from test.integration.postgres.test_admin_load_plans import nodes
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_performance import report, seed_executors
from test.integration.postgres.test_recorder import recording as _recording
from types import SimpleNamespace

import pytest
from psycopg2.extensions import cursor
from sqlalchemy import event, text

from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.executors import Executors
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from scripts.v2_with_controllers import V2WithControllers

recording = _recording
owned = _owned


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('runtime', ['recording', 'owned'])
def test_history_worker_closes_stream_and_preserves_native_totals(request, pg_engine, cancel, runtime):
    context = request.getfixturevalue(runtime)
    recorder = context[0] if runtime == 'recording' else context.recorder
    checkedout = pg_engine.pool.checkedout()
    physical = None if runtime == 'recording' else context.owner.connection
    if physical is not None:
        context.owner.check_owner()
    seed_executors(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE executors SET controller_id = 'selected-' || (timestamp::int % 2) WHERE timestamp <= 2000"))
    config = SimpleNamespace(mm_drawdown_scope={'selected-0': object(), 'selected-1': object()})
    reference = ExecutorOrchestrator(SimpleNamespace(config=config, controllers={}, markets={}))
    entered, release = threading.Event(), threading.Event()
    history_threads, completed_waits, constructed = [], [], []
    main = threading.get_ident()

    def fetching(connection, cursor, statement, *args):
        if 'FROM executors' in statement:
            assert 'executors.config' not in statement and 'executors.custom_info' not in statement
            history_threads.append(threading.get_ident())
            entered.set()
            completed_waits.append(release.wait(1))

    class Strategy:
        def __init__(self, connectors, config, *, startup_performance):
            constructed.append(threading.get_ident())
            self.config, self.controllers, self.markets = config, {}, {}
            self.executor_orchestrator = ExecutorOrchestrator(self, startup_performance=startup_performance)

    async def run():
        task = asyncio.create_task(V2WithControllers.create_with_async_history.__func__(Strategy, {}, config))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            if cancel:
                task.cancel()
                await asyncio.sleep(.01)
                task.cancel()
                await asyncio.sleep(.01)
                assert not task.done() and pg_engine.pool.checkedout() == checkedout + 1
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.executor_orchestrator.cached_performance == reference.cached_performance
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    event.listen(pg_engine, 'after_cursor_execute', fetching)
    try:
        asyncio.run(run())
    finally:
        event.remove(pg_engine, 'after_cursor_execute', fetching)
    assert completed_waits == [True]
    assert len(history_threads) == 1 and history_threads[0] != main
    assert constructed == ([] if cancel else [main])
    assert pg_engine.pool.checkedout() == checkedout
    assert recorder.sql_manager.persistence_healthy
    if physical is not None:
        assert context.owner.connection is physical
        context.owner.check_owner()


def test_managed_startup_counts_inactive_history_without_streaming_each_row(recording, pg_engine, monkeypatch):
    recorder = recording[0]
    benchmark = os.getenv('HBOT_PERFORMANCE_REPORT')
    row_count = 220000 if benchmark else 12000
    with pg_engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 30):
        connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
        connection.execute(text("""
            INSERT INTO executors (
                id, timestamp, type, close_type, status, config, net_pnl_pct, net_pnl_quote,
                cum_fees_quote, filled_amount_quote, is_active, is_trading, custom_info, controller_id
            )
            SELECT 'history-' || n, n, 'order_executor',
                   CASE n % 20 WHEN 1 THEN 3 WHEN 2 THEN 10 WHEN 3 THEN NULL ELSE 5 END,
                   3, '{}', 0, CASE n % 20
                       WHEN 1 THEN CASE (n / 60) % 4 WHEN 0 THEN 1e27 WHEN 1 THEN 0.0001
                           WHEN 2 THEN -1e27 ELSE -0.123 END
                       WHEN 2 THEN -0.3 ELSE 0 END,
                   0, CASE n % 20 WHEN 1 THEN 15.1 WHEN 2 THEN 9.4 ELSE 0 END,
                   false, false, '{}', 'life-' || (n % 3)
            FROM generate_series(1, :rows) AS n
        """), {'rows': row_count})
    controller_ids = ['life-0', 'life-2']
    config = SimpleNamespace(mm_drawdown_scope=dict.fromkeys(controller_ids))
    raw_reader = recorder.iter_executor_performance
    rows_seen = []

    def counted(*args, **kwargs):
        with closing(raw_reader(*args, **kwargs)) as rows:
            for row in rows:
                rows_seen.append(1)
                yield row

    monkeypatch.setattr(recorder, 'iter_executor_performance', counted)

    def build(connectors, config, *, startup_performance):
        return startup_performance

    statements = []

    def capture(connection, cursor, statement, parameters, *args):
        if 'FROM executors' in statement:
            statements.append((statement, parameters))

    measured = {}
    for label, read in [
        ('uncompressed', lambda: ExecutorOrchestrator.read_startup_performance(recorder, controller_ids)),
        ('managed', lambda: asyncio.run(V2WithControllers.create_with_async_history.__func__(build, {}, config))),
    ]:
        elapsed = []
        for _ in range(3 if benchmark else 1):
            rows_seen.clear()
            statements.clear()
            start = time.perf_counter()
            event.listen(pg_engine, 'after_cursor_execute', capture)
            try:
                result = read()
            finally:
                event.remove(pg_engine, 'after_cursor_execute', capture)
            elapsed.append((time.perf_counter() - start) * 1000)
        assert len(statements) == 1
        measured[label] = dict(rows=len(rows_seen), elapsed_ms=elapsed)
        if label == 'uncompressed':
            reference = dict((key, (pnl, volume, dict(counts))) for key, pnl, volume, counts in result.totals)
        else:
            assert dict((key, (pnl, volume, dict(counts))) for key, pnl, volume, counts in result.totals) == reference
    report('managed_startup_history', measured)
    assert measured['managed']['rows'] < measured['uncompressed']['rows'] / 10
    assert pg_engine.pool.checkedout() == 0
    statement, parameters = statements[0]
    explain = 'EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) ' + statement
    with pg_engine.connect() as connection:
        plan = connection.exec_driver_sql(explain, parameters).scalar_one()[0]['Plan']
    numbered_rows = sum(node['Actual Rows'] * node['Actual Loops'] for node in nodes(plan) if node['Node Type'] == 'WindowAgg')
    assert numbered_rows <= measured['managed']['rows'], 'Only individual contributions need a scan ordinal'


def test_managed_history_uses_a_separate_bounded_budget_from_runtime_queries(recording, pg_engine):
    recorder = recording[0]
    seed_executors(pg_engine)

    def delayed_history(connection, cursor, statement, parameters, _context, _executemany):
        if 'WITH scoped AS NOT MATERIALIZED' in statement:
            statement = 'SELECT history.* FROM (SELECT pg_sleep(2.1)) AS delay CROSS JOIN (' + statement + ') AS history'
        return statement, parameters

    event.listen(pg_engine, 'before_cursor_execute', delayed_history, retval=True)
    try:
        assert list(recorder.iter_executor_performance(['selected-0'], compact=True))
    finally:
        event.remove(pg_engine, 'before_cursor_execute', delayed_history)
    with pg_engine.connect() as connection:
        assert connection.exec_driver_sql('SHOW statement_timeout').scalar_one() == '2s'
    assert pg_engine.pool.checkedout() == 0


@pytest.mark.parametrize('size', [3, 4002])
def test_compact_history_stops_fetching_after_a_short_server_page(recording, pg_engine, size):
    with pg_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO executors (id, timestamp, type, close_type, status, config, net_pnl_pct,
                net_pnl_quote, cum_fees_quote, filled_amount_quote, is_active, is_trading, custom_info, controller_id)
            SELECT 'history-' || n, n, 'order_executor', 3, 3, '{}', 0, 1, 0, 15,
                   false, false, '{}', 'life'
            FROM generate_series(1, :size) AS n
        """), {'size': size})
    fetched = []

    class ObservedCursor(cursor):
        def fetchmany(self, size=None):
            rows = super().fetchmany(size)
            if self.name:
                fetched.append((size, len(rows)))
            return rows

    def observe(raw, *_):
        raw.cursor_factory = ObservedCursor

    event.listen(pg_engine, 'checkout', observe)
    try:
        result = ExecutorOrchestrator.read_startup_performance(recording[0], ['life'], compact=True)
    finally:
        event.remove(pg_engine, 'checkout', observe)
    _, pnl, volume, counts = result.totals[0]
    assert pnl == size and volume == size * 15 and sum(dict(counts).values()) == size
    assert len(fetched) == (2 if size < 4000 else 3)
    assert all(0 < returned <= requested <= 4000 for requested, returned in fetched)
    assert pg_engine.pool.checkedout() == 0


@pytest.mark.parametrize('invalid', [
    {'net_pnl_quote': float('nan'), 'close_type': 10},
    {'filled_amount_quote': float('inf'), 'close_type': 10},
    {'close_type': 999},
])
def test_managed_history_never_hides_invalid_rows(recording, pg_engine, invalid):
    values = dict(id='invalid', controller_id='life', timestamp=1, type='order_executor', close_type=5,
                  status=3, config={}, custom_info={}, net_pnl_quote=0, net_pnl_pct=0, cum_fees_quote=0,
                  filled_amount_quote=0, is_active=False, is_trading=False)
    with pg_engine.begin() as connection:
        connection.execute(Executors.__table__.insert().values({**values, **invalid}))

    def build(*args, **kwargs):
        pytest.fail('Invalid executor history reached strategy construction')

    with pytest.raises(ValueError):
        asyncio.run(V2WithControllers.create_with_async_history.__func__(
            build, {}, SimpleNamespace(mm_drawdown_scope={'life': None})))
    assert pg_engine.pool.checkedout() == 0


def test_profile_handoff_reads_the_final_archive_during_venue_proof(recording, pg_engine):
    from unittest.mock import AsyncMock, MagicMock

    from hummingbot.cli.profile_switch import ProfileSwitchPreparation
    from hummingbot.core.trading_core import TradingCore
    from hummingbot.model.position import Position
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base

    recorder = recording[0]
    scope = ('selected-0', 'selected-1')
    benchmark = bool(os.getenv('HBOT_PERFORMANCE_REPORT'))
    rows = 220000 if benchmark else 12000
    proof_seconds = .4 if benchmark else .1
    config = SimpleNamespace(mm_drawdown_scope={
        name: SimpleNamespace(connector_name='okx', trading_pair=pair)
        for name, pair in zip(scope, ('BTC-USDT', 'ETH-USDT'))})
    with pg_engine.begin() as db, bootstrap_io_budget(db, time.monotonic() + 30):
        db.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        db.execute(text("""
            INSERT INTO executors (id, timestamp, type, close_type, status, config, net_pnl_pct, net_pnl_quote,
                        cum_fees_quote, filled_amount_quote, is_active, is_trading, custom_info, controller_id)
            SELECT 'executor-' || n, n, 'position_executor', 3, 3, '{}', 0,
                   CASE WHEN n % 10 = 1 THEN -.3 ELSE 0 END, 0, CASE WHEN n % 10 = 1 THEN 10.1 ELSE 0 END,
                   false, false, '{}', 'selected-' || (n % 2) FROM generate_series(1, :rows) n
        """), {'rows': rows})
        db.execute(Position.__table__.insert().values(
            id='late-position', controller_id=scope[0], connector_name='okx', trading_pair='BTC-USDT',
            side='BUY', timestamp=1, amount=1, breakeven_price=1, volume_traded_quote=1,
            realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=0))
    reads = []
    entered = threading.Event()

    def fetching(connection, cursor, statement, *args):
        if 'FROM executors' in statement:
            reads.append(threading.get_ident())
            entered.set()

    async def run(prepared):
        provider = SimpleNamespace(connectors={})
        preparation = ProfileSwitchPreparation(SimpleNamespace(id='session', generation=2), provider, [],
                                               history_scope=scope)
        core = object.__new__(TradingCore)
        core.strategy = MagicMock(spec=StrategyV2Base)
        core.strategy.market_data_provider = provider
        core.markets_recorder = recorder
        core.before_position_checkpoint = None
        core._strategy_running = True

        async def final_archive(**_):
            assert not entered.is_set()
            with pg_engine.begin() as db:
                db.execute(text("UPDATE executors SET net_pnl_quote=13, filled_amount_quote=17, close_type=3 "
                                "WHERE id='executor-20'"))
                db.execute(Position.__table__.update().values(amount=7))

        async def stop():
            core.strategy = None
            return True

        async def venue_proof():
            if prepared:
                seen, _ = await asyncio.gather(asyncio.to_thread(entered.wait, 5), asyncio.sleep(proof_seconds))
                assert seen, 'History did not overlap terminal proof'
            else:
                await asyncio.sleep(proof_seconds)

        core.strategy.on_stop = AsyncMock(side_effect=final_archive)
        core.stop_strategy = stop
        core.cancel_outstanding_orders = AsyncMock(return_value=True)
        core.after_order_cancellation = venue_proof

        class TargetStrategy(V2WithControllers):
            markets = {}

            def __init__(self, connectors, config, *, startup_performance, market_data_provider):
                assert market_data_provider is provider
                self.config, self.controllers = config, {}
                self.snapshot = startup_performance
                orchestrator = ExecutorOrchestrator(self, startup_performance=startup_performance)
                assert orchestrator.positions_held[scope[0]][0].net_amount_base == 7

        core.strategy_name = 'v2_with_controllers'
        core.connector_manager = SimpleNamespace(get_all_connectors=lambda: provider.connectors)
        core.load_v2_class = lambda _: (TargetStrategy, config)
        preparation.promoted = True
        options = {'after_executor_checkpoint': preparation.start_history} if prepared else {}
        started = time.perf_counter()
        try:
            assert await core.shutdown(keep_positions=True, keep_market_data=True, **options)
            assert core.retained_market_data_provider is provider
            history = preparation.claim_history(preparation.session, core.retained_market_data_provider)
            await core._initialize_v2_strategy(prepared_history=history)
            assert len(reads) == 1 and reads[0] != threading.get_ident()
            return core.strategy.snapshot, (time.perf_counter() - started) * 1000
        finally:
            await preparation.close()

    results, durations = {}, {False: [], True: []}
    event.listen(pg_engine, 'after_cursor_execute', fetching)
    try:
        for prepared in ((False, True, True, False) * 3 if benchmark else (False, True)):
            with pg_engine.begin() as db:
                db.execute(text("UPDATE executors SET net_pnl_quote=0, filled_amount_quote=0 WHERE id='executor-20'"))
                db.execute(Position.__table__.update().values(amount=1))
            reads.clear()
            entered.clear()
            results[prepared], elapsed = asyncio.run(run(prepared))
            durations[prepared].append(elapsed)
    finally:
        event.remove(pg_engine, 'after_cursor_execute', fetching)
    assert results[True] == results[False]
    assert dict((key, (pnl, volume)) for key, pnl, volume, _ in results[True].totals)['selected-0'] == (13, 17)
    assert pg_engine.pool.checkedout() == 0
    report('profile_history_handoff', {
        'rows': rows, 'venue_proof_ms': proof_seconds * 1000,
        'sequential_ms': durations[False], 'overlap_ms': durations[True],
    })
