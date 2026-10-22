"""Synthetic scale gates. These ceilings never configure production trading."""
import asyncio
import json
import math
import os
import statistics
import sys
import time
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.offline_inputs import BUDGETS

import pytest

from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent, _Reservation


def limit(name):
    policy = json.loads(BUDGETS.read_text())
    tracing = sys.gettrace() is not None or "COVERAGE_RUN" in os.environ
    factor = policy["instrumentation_headroom"] if tracing else 1
    return policy["limits_ms"][name] * factor


def stats(values):
    return {"samples_ms": values, "median_ms": statistics.median(values),
            "p95_ms": sorted(values)[math.ceil(.95 * len(values)) - 1], "max_ms": max(values)}


def make_ledger(path, n):
    limits = RiskLimits(D("0"), D("100000"), D("100000"), D("100000"))
    ledger = ReservationLedger(life_balance=D("10"), usdt_balance=D("100000"), limits=limits, path=path)
    records = {str(i): _Reservation(SpotIntent(str(i), "BUY", D("1"), D("1"), "past", 1),
                                    D("0"), D("1"), "TERMINAL") for i in range(n)}
    trades = {str(i): (str(i), D("1"), D("1")) for i in range(n)}
    ledger._commit(D("10"), D("100000"), records, trades)
    return ledger


@pytest.mark.asyncio
@pytest.mark.o8_performance
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_scaled_restore_and_actual_authorization_with_watchdog(tmp_path, history, record_property):
    ledger = make_ledger(tmp_path / "scale.json", history)
    restore_times, authorization_times, lag_times = [], [], []
    async with system(tmp_path / "system") as f:
        c = f.c
        quote = c._quote_action_planner
        journal = quote.action_journal
        session = c._order_safety_manager.current_session
        # Terminal pre-dispatch rejections are valid complete action history.
        # Filled reservation history is measured separately above, not fabricated
        # as reconciled joint/account history in the actual sender fixture.
        past = {"past-" + str(i): QuoteActionRecord("past-" + str(i), "life", session.session_id,
                                                    session.epoch, session.config_version, "LIFE-USDT", "BUY", 0,
                                                    "REJECTED") for i in range(history)}
        journal._commit(past)
        c.order_safety_watchdog_task.cancel()
        await asyncio.gather(c.order_safety_watchdog_task, return_exceptions=True)
        watchdog = asyncio.create_task(c._order_safety_watchdog(.01))
        c.order_safety_watchdog_task = watchdog
        ids = iter("scaled-" + str(i) for i in range(100))
        quote.intent_id_factory = lambda: next(ids)
        try:
            for sample in range(5):
                await asyncio.sleep(.015)
                start = time.perf_counter()
                restored = ReservationLedger.restore(ledger.path, limits=ledger.limits)
                restore_times.append((time.perf_counter() - start) * 1000)
                assert len(restored._trades) == history
                if sample == 0:
                    start_auth = time.perf_counter()
                    actions = quote.propose()
                    assert actions, quote.reason_code
                    send(f, actions[0])  # Actual V2 filter -> executor -> protected final wire.
                    authorization_times.append((time.perf_counter() - start_auth) * 1000)
                else:
                    start_auth = time.perf_counter()
                    assert c.allow_create_executor_actions()
                    authorization_times.append((time.perf_counter() - start_auth) * 1000)
                before_yield = time.perf_counter()
                await asyncio.sleep(.01)
                # Upper bound includes synchronous restore + authorization and
                # the independent production watchdog's own safety work.
                lag_times.append((time.perf_counter() - start) * 1000 - .01 * 1000)
                assert time.perf_counter() >= before_yield
            assert max(restore_times) < limit("ledger_restore"), restore_times
            assert max(authorization_times) < limit("authorization_final_send"), authorization_times
            assert max(lag_times) < limit("event_loop_lag"), lag_times
            report = {"history": history, "restore": stats(restore_times), "propose_filter_final_send": stats(authorization_times[:1]),
                      "repeated_allow_create": stats(authorization_times[1:]),
                      "loop_lag_upper_bound": stats(lag_times), "journal_bytes": ledger.path.stat().st_size,
                      "action_journal_bytes": journal.path.stat().st_size}
            record_property("o8_scale", json.dumps(report, sort_keys=True))
            print("O8_SCALE " + json.dumps(report, sort_keys=True))
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
