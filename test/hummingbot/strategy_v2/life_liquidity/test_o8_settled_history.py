"""Full settled account history through the actual protected sender and cancel."""
import asyncio
import json
import time
from contextlib import nullcontext
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_o8_performance import limit, stats

import pytest

from hummingbot.strategy_v2.life_liquidity.history_worker import HistoryWorker, HistoryWorkerPolicy


@pytest.mark.asyncio
async def test_committed_history_does_not_recheckpoint_terminal_orders(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)

        def unexpected(*args, **kwargs):
            raise AssertionError("historical terminal reservation was checkpointed again")

        monkeypatch.setattr(f.c._order_safety_reservations, "confirm_terminal", unexpected)
        assert await f.joint.reconcile(), f.joint.reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 1000, 10000])
@pytest.mark.parametrize("archived", [False, True, "worker", "process", "journals"],
                         ids=["legacy", "segmented", "segmented-worker", "segmented-process", "segmented-journals"])
async def test_complete_settled_history_send_poll_and_cancel(tmp_path, history, archived, record_property, monkeypatch):
    await exercise_settled_history(tmp_path, history, archived, record_property, monkeypatch)


async def exercise_settled_history(tmp_path, history, archived, record_property, monkeypatch, *, observe=nullcontext):
    """Shared acceptance workload; optional diagnostics never change its gates."""
    async with system(tmp_path) as f:
        with observe("seed_and_migrate"):
            seed_settled_history(f, history)
            if archived:
                archive_settled_history(f)
            if archived == "journals":
                archive_financial_journals(f)
        worker = None
        startup_ms = None
        if archived in ("worker", "process"):
            # Explicit synthetic bounds; the real constructor also accepts
            # this injected worker. No live/default policy is inferred.
            backend = "process" if archived == "process" else "thread"
            worker = HistoryWorker(HistoryWorkerPolicy(20000, 40000, 64 * 1024 * 1024,
                                                       15000 if backend == "process" else 2000, backend))
            if backend == "process":
                started = time.perf_counter()
                with observe("worker_startup"):
                    await worker.start(timeout_ms=15000)
                startup_ms = (time.perf_counter() - started) * 1000
            f.joint.history_worker = worker
        c, clock = f.c, f.v[6]
        times, lags, cancel_starts = {}, [], []

        def timed_cancel(callback):
            async def invoke(*args, **kwargs):
                cancel_starts.append(time.perf_counter())
                return await callback(*args, **kwargs)
            return invoke
        for name in ("cancel_by_client_id", "cancel_by_exchange_order_id"):
            monkeypatch.setattr(f.v[3], name, timed_cancel(getattr(f.v[3], name)))
        c.order_safety_watchdog_task.cancel()
        await asyncio.gather(c.order_safety_watchdog_task, return_exceptions=True)
        watchdog = None

        async def heartbeat():
            while True:
                start = time.perf_counter()
                await asyncio.sleep(.01)
                lags.append(max(0, (time.perf_counter() - start) * 1000 - 10))
        monitor = asyncio.create_task(heartbeat())
        try:
            await asyncio.sleep(0)  # Arm before the first production watchdog iteration too.
            watchdog = asyncio.create_task(c._order_safety_watchdog(.01))
            c.order_safety_watchdog_task = watchdog
            start = time.perf_counter()
            with observe("joint_poll"):
                assert await f.joint.reconcile(), f.joint.reason_code
            times["joint_poll_ms"] = (time.perf_counter() - start) * 1000
            with observe("watchdog_before_send"):
                await asyncio.sleep(.015)
            with observe("attribution_ready"):
                assert c._fill_attributor.ready()
            with observe("valuation"):
                assert f.joint.measure().nav_quote == 20
            quote = c._quote_action_planner
            quote.intent_id_factory = lambda: "after-history"
            start = time.perf_counter()
            with observe("propose"):
                actions = quote.propose()
            assert actions, (quote.reason_code, c.fill_attribution_reason_code, f.joint.reason_code)
            with observe("filter_dispatch_final_send"):
                send(f, actions[0])
            times["propose_filter_final_send_ms"] = (time.perf_counter() - start) * 1000
            with observe("watchdog_after_send"):
                await asyncio.sleep(.015)
            checks = []
            for index in range(5):
                start = time.perf_counter()
                with observe(f"permission_{index + 1}"):
                    assert c.allow_create_executor_actions()
                checks.append((time.perf_counter() - start) * 1000)
                await asyncio.sleep(.015)
            times["repeated_permission"] = stats(checks)
            assert not cancel_starts  # Affordable owned pending makers stayed active.
            clock.advance(.001)
            c._order_safety_manager.pause("SYNTHETIC_SCALE_CANCEL")
            started = time.perf_counter()
            # Let the real independent watchdog discover revocation and launch
            # cancellation. A hard diagnostic timeout does not widen the gate.
            with observe("cancel_dispatch"):
                while not cancel_starts:
                    if time.perf_counter() - started > 5:
                        pytest.fail("watchdog did not start an owned spot cancel within 5s")
                    await asyncio.sleep(.001)
            # Timestamp at the connector callback, not a potentially late
            # observer wake-up after qualification has occupied the loop.
            times["cancel_start_ms"] = (cancel_starts[0] - started) * 1000
            assert "after-history" in f.v[1].claim_ids()  # Cancel request is not terminal proof.
            await asyncio.sleep(.015)
            times["event_loop_lag"] = stats(lags)
            report = dict(history=history, archived=bool(archived), journal_archived=archived == "journals", worker=worker is not None,
                          worker_backend=None if worker is None else worker.policy.backend,
                          worker_startup_ms=startup_ms, **times)
            record_property("o8_settled_history", json.dumps(report, sort_keys=True))
            print("O8_SETTLED_HISTORY " + json.dumps(report, sort_keys=True))
            breaches = {name: (observed, limit(budget)) for name, observed, budget in (
                ("joint_poll_ms", times["joint_poll_ms"], "joint_qualification_settlement"),
                ("propose_filter_final_send_ms", times["propose_filter_final_send_ms"], "authorization_final_send"),
                ("event_loop_lag_ms", max(lags), "event_loop_lag"),
                ("cancel_start_ms", times["cancel_start_ms"], "healthy_product_cancel_start"))
                if observed >= limit(budget)}
            assert not breaches, breaches
        finally:
            if worker is not None:
                worker.close()
            for task in (watchdog, monitor, c.order_safety_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (watchdog, monitor, c.order_safety_task) if t is not None),
                                 return_exceptions=True)
