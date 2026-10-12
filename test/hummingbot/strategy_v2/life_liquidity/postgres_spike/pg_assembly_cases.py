"""Explicit PostgreSQL + actual V2/OKX/recorder assembly cases."""
import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.postgres_spike import pg_spike_cases
from test.hummingbot.strategy_v2.life_liquidity.postgres_spike.assembly import RunnerAssembly
from test.hummingbot.strategy_v2.life_liquidity.test_o8_performance import limit
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder

import pytest
import pytest_asyncio

from hummingbot.core.data_type.common import TradeType
from hummingbot.model.executors import Executors
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

base_db = pg_spike_cases.db
seed_history = pg_spike_cases.seed_history


def seed_recorder_history(recorder, history):
    # Corresponding terminal projections; cold startup is outside hot-path timing.
    with recorder.sql_manager.get_new_session() as session:
        for first in range(1, history + 1, 500):
            batch = []
            for n in range(first, min(history + 1, first + 500)):
                config = OrderExecutorConfig(
                    id="history-" + str(n), controller_id="life", timestamp=1,
                    connector_name="okx", trading_pair="LIFE-USDT", side=TradeType.BUY if n % 2 == 0 else TradeType.SELL,
                    amount=Decimal("1"), price=Decimal("1"), execution_strategy=ExecutionStrategy.LIMIT_MAKER)
                info = ExecutorInfo(
                    id=config.id, timestamp=1, type="order_executor", status=RunnableStatus.TERMINATED,
                    config=config, net_pnl_pct=Decimal("-0.001"), net_pnl_quote=Decimal("-0.001"),
                    cum_fees_quote=Decimal("0.001"), filled_amount_quote=Decimal("1"), is_active=False, is_trading=False,
                    controller_id="life", custom_info={
                        "recovery_account_uid": "12345", "recovery_order_ids": ["wire-history-" + str(n)],
                        "pg_account": "synthetic-life", "pg_lineage": "synthetic-lineage", "pg_owner": "process-a",
                        "pg_epoch": 1, "pg_session": "past"})
                batch.append(Executors(**json.loads(info.model_dump_json())))
            session.add_all(batch)
        session.commit()


@pytest_asyncio.fixture
async def assembly(base_db, tmp_path, monkeypatch):
    yield base_db, tmp_path, monkeypatch


@pytest.mark.asyncio
async def test_tick_and_queue_reach_real_okx_throttled_db_gate_and_recorder(assembly):
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"), None)
    async with app:
        await app.enqueue("first", route="tick")
        await app.complete("first")
        await app.enqueue("second", route="queue")
        await app.complete("second")
        assert len(app.http.sent) == 2
        assert len(app.recorder.get_executors_by_ids(["first", "second"])) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stop", "config", "expiry", "recorder_missing", "recorder_wire"])
async def test_late_revocation_blocks_actual_okx_transport_and_keeps_hold(assembly, fault):
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"), None)
    async with app:
        app.block_database_gate()
        await app.enqueue("late", route="queue")
        await asyncio.wait_for(app.db_gate_entered.wait(), 2)
        if fault == "stop":
            app.controller.stop()
        elif fault == "config":
            await db.admin.execute("UPDATE account_state SET config_version=2")
        elif fault == "expiry":
            app.controller.commands["late"] = replace(app.controller.commands["late"],
                                                      expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        else:
            app.corrupt_recorder("late", fault)
        app.release_database_gate()
        with pytest.raises((PermissionError, OSError, asyncio.CancelledError)):
            await app.complete("late")
        assert not app.http.sent
        assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["write_failure", "delayed_stop"])
async def test_recorder_failure_or_delay_never_arms_or_late_sends(assembly, fault):
    db, path, monkeypatch = assembly
    recorder = _recorder(path / "recorder.sqlite")
    app = RunnerAssembly(db, recorder)
    async with app:
        if fault == "write_failure":
            def fail(*args):
                raise OSError("synthetic recorder failed")
            monkeypatch.setattr(recorder, "store_or_update_executor", fail)
        else:
            app.recorder_release.clear()
        await app.enqueue("projection")
        await asyncio.wait_for(app.recorder_entered.wait(), 2)
        if fault == "delayed_stop":
            app.controller.stop()
            app.recorder_release.set()
        with pytest.raises((OSError, PermissionError, asyncio.CancelledError)):
            await app.complete("projection")
        assert not app.http.sent
        assert await db.admin.fetchval("SELECT state FROM attempts") == "PREPARED"
        assert await db.admin.fetchval("SELECT state FROM recorder_operations") == "READY"
        assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0


@pytest.mark.asyncio
async def test_unknown_ack_cold_objects_and_recorder_missing_do_not_resend_or_release(assembly):
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"))
    async with app:
        app.http.lose_ack = True
        command = await app.enqueue("unknown")
        with pytest.raises(OSError):
            await app.complete("unknown")
        assert len(app.http.sent) == 1
        assert await db.admin.fetchval("SELECT state FROM attempts") == "SEND_UNKNOWN"
        reopened = RunnerAssembly(db, _recorder(path / "recorder.sqlite"), cold_restore=True)
        assert reopened.controller.stopped
        with pytest.raises(PermissionError):
            await reopened.enqueue("unknown")
        assert await reopened.store.verify_projection(command)
        app.corrupt_recorder("unknown", "recorder_missing")
        with pytest.raises(PermissionError):
            await reopened.store.verify_projection(command)
        with pytest.raises(PermissionError):
            await reopened.store.settle_zero_fill(command, "unproven")
        assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0


@pytest.mark.asyncio
async def test_stop_action_batch_suppresses_create_and_shutdown_leaves_no_late_send(assembly):
    from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"))
    async with app:
        await app.enqueue("stopped-batch")
        actions = app.runner.actions_queue.get_nowait()
        app.runner.actions_queue.put_nowait([*actions, StopExecutorAction(controller_id="life", executor_id="none")])
        await asyncio.sleep(.05)
        assert app.runner.executor_orchestrator.active_executors["life"] == []
        assert not app.http.sent and not app.controller.tasks
        assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 0


@pytest.mark.asyncio
async def test_shared_dispatch_and_queue_caps_prevent_unbounded_children(assembly):
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"))
    async with app:
        app.block_database_gate()
        await app.enqueue("one")
        await app.enqueue("two")
        with pytest.raises(asyncio.QueueFull):
            await app.enqueue("overflow")
        await asyncio.wait_for(app.db_gate_entered.wait(), 2)
        await asyncio.sleep(.05)
        assert len(app.controller.tasks) == 2
        assert len(app.runner.executor_orchestrator.active_executors["life"]) == 2
        with pytest.raises(PermissionError):
            await app.enqueue("more")
        assert not app.http.sent


@pytest.mark.asyncio
async def test_running_recorder_thread_can_finish_after_stop_without_late_send(assembly, monkeypatch):
    db, path, _ = assembly
    recorder = _recorder(path / "recorder.sqlite")
    app = RunnerAssembly(db, recorder)
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = recorder.store_or_update_executor

    def delayed(projection):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2)
        original(projection)
        loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(recorder, "store_or_update_executor", delayed)
    async with app:
        try:
            await app.enqueue("late-thread")
            await asyncio.wait_for(entered.wait(), 2)
            app.controller.stop()
            release.set()
            await asyncio.wait_for(finished.wait(), 2)
            with pytest.raises(asyncio.CancelledError):
                await app.complete("late-thread")
            assert not app.http.sent
            assert await db.admin.fetchval("SELECT state FROM recorder_operations") == "READY"
            assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0
        finally:
            release.set()


@pytest.mark.asyncio
async def test_owned_cancel_runs_while_another_final_send_waits_on_db_lock(assembly):
    db, path, monkeypatch = assembly
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"))
    async with app:
        await app.enqueue("open")
        await app.complete("open")
        app.db_gate_entered.clear()
        app.block_database_gate()
        await app.enqueue("waiting")
        await asyncio.wait_for(app.db_gate_entered.wait(), 2)
        transaction = db.admin.transaction()
        await transaction.start()
        await db.admin.fetchrow("SELECT * FROM account_state FOR UPDATE")
        try:
            app.release_database_gate()
            await asyncio.sleep(.03)
            assert len(app.http.sent) == 1
            start = time.perf_counter()
            app.controller.stop()
            await asyncio.wait_for(app.controller.cancel_owned(), 1)
            assert (app.http.cancels[0][1] - start) * 1000 < limit("healthy_product_cancel_start")
            assert len(app.http.sent) == 1
        finally:
            await transaction.rollback()
        assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_scale_assembled_tick_queue_okx_recorder_and_cancel(assembly, history, record_property):
    db, path, monkeypatch = assembly
    await seed_history(db, history)
    app = RunnerAssembly(db, _recorder(path / "recorder.sqlite"))
    await asyncio.to_thread(seed_recorder_history, app.recorder, history)
    async with app:
        lags, sends, counts = [], [], []

        async def heartbeat():
            while True:
                start = time.perf_counter()
                await asyncio.sleep(.01)
                lags.append(max(0, (time.perf_counter() - start) * 1000 - 10))

        async def watchdog():
            while True:
                if app.controller.stopped:
                    await app.controller.cancel_owned()
                    return
                await asyncio.sleep(.01)

        monitor, watch = asyncio.create_task(heartbeat()), asyncio.create_task(watchdog())
        try:
            await asyncio.sleep(.015)
            start = time.perf_counter()
            assert await app.store.poll() == ()
            poll_ms = (time.perf_counter() - start) * 1000
            for n in range(5):
                q, r = app.store.queries, app.store.rows
                start = time.perf_counter()
                command = await app.enqueue("assembled-" + str(n), route="tick" if n % 2 == 0 else "queue",
                                            side="BUY" if n % 2 == 0 else "SELL")
                exchange = await app.complete(command.intent)
                sends.append((time.perf_counter() - start) * 1000)
                counts.append({"queries": app.store.queries - q, "returned_rows": app.store.rows - r})
                if n != 4:
                    await app.store.settle_zero_fill(command, exchange)
                await asyncio.sleep(.015)
            assert len(app.http.sent) == 5
            start = time.perf_counter()
            app.controller.stop()
            await asyncio.wait_for(watch, 5)
            cancel_ms = (app.http.cancels[0][1] - start) * 1000
            assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") > 0
            report = {"history": history, "poll_ms": poll_ms, "send_samples_ms": sends,
                      "event_loop_lag_ms": max(lags), "cancel_start_ms": cancel_ms, "sample_query_counts": counts,
                      "scope": "SPOT_PG_V2_OKX_SQLITE_PROJECTION_ASSEMBLY_SYNTHETIC_HTTP"}
            record_property("life_pg_spike_scale", json.dumps(report, sort_keys=True))
            breaches = {name: (value, limit(budget)) for name, value, budget in (
                ("poll", poll_ms, "joint_qualification_settlement"),
                ("send", max(sends), "authorization_final_send"),
                ("lag", max(lags), "event_loop_lag"),
                ("cancel", cancel_ms, "healthy_product_cancel_start")) if value >= limit(budget)}
            assert not breaches, breaches
            assert len({(v["queries"], v["returned_rows"]) for v in counts}) == 1
        finally:
            for task in (monitor, watch):
                task.cancel()
            await asyncio.gather(monitor, watch, return_exceptions=True)
