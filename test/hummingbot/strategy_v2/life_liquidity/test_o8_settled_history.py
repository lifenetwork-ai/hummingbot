"""Full settled account history through the actual protected sender and cancel."""
import asyncio
import json
import time
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import seed_settled_history
from test.hummingbot.strategy_v2.life_liquidity.test_o8_performance import limit, stats

import pytest


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
async def test_complete_settled_history_send_poll_and_cancel(tmp_path, history, record_property, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
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
            assert await f.joint.reconcile(), f.joint.reason_code
            times["joint_poll_ms"] = (time.perf_counter() - start) * 1000
            await asyncio.sleep(.015)
            assert c._fill_attributor.ready()
            assert f.joint.measure().nav_quote == 20
            quote = c._quote_action_planner
            quote.intent_id_factory = lambda: "after-history"
            start = time.perf_counter()
            actions = quote.propose()
            assert actions, (quote.reason_code, c.fill_attribution_reason_code, f.joint.reason_code)
            send(f, actions[0])
            times["propose_filter_final_send_ms"] = (time.perf_counter() - start) * 1000
            await asyncio.sleep(.015)
            checks = []
            for _ in range(5):
                start = time.perf_counter()
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
            report = dict(history=history, **times)
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
            for task in (watchdog, monitor, c.order_safety_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (watchdog, monitor, c.order_safety_task) if t is not None),
                                 return_exceptions=True)
