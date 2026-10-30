"""Opt-in isolated-network load generator. Rates use open-loop scheduled slots."""
import argparse
import asyncio
import atexit
import json
import math
import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import aiohttp
from load_health import response_problem
from load_trace import TraceSink, sql_metadata

trace_directory = os.environ.get("HBOT_LOAD_TRACE_DIRECTORY")
trace_sink = TraceSink(Path(trace_directory) / "client.jsonl", append=True) if trace_directory else None
trace_sequence = 0
trace_prefix = uuid.uuid4().hex[:12]
sampler_stop = threading.Event()
sampler_counts = {"samples": 0, "errors": 0}


def request_identity():
    global trace_sequence
    trace_sequence += 1
    return f"{trace_prefix}-{trace_sequence}"


def database_sampler(ready):
    cpu_started = time.thread_time_ns()
    import psycopg2
    from sqlalchemy.engine import make_url
    url = make_url(os.environ["HBOT_LOAD_TRACE_DATABASE_URL"])
    assert url.host == "db" and url.database == "hbot_test_offline"
    connection = None
    while not sampler_stop.is_set():
        started = time.monotonic_ns()
        try:
            if connection is None or connection.closed:
                connection = psycopg2.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                                              user=url.username, password=url.password, connect_timeout=3,
                                              application_name="hbot-load-trace",
                                              options="-c statement_timeout=200 -c timezone=UTC")
                connection.autocommit = True
                ready.set()
            with connection.cursor() as cursor:
                cursor.execute("""SELECT pid,backend_type,state,wait_event_type,wait_event,
                    pg_blocking_pids(pid),extract(epoch FROM query_start),extract(epoch FROM xact_start),query
                    FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
                    AND (state IS DISTINCT FROM 'idle' OR wait_event_type IN ('Lock','IO')
                         OR backend_type<>'client backend') ORDER BY pid LIMIT 64""")
                rows = []
                for pid, kind, state, wait_type, wait, blockers, query_start, xact_start, statement in cursor:
                    rows.append(dict(backend_pid=pid, backend_type=kind, state=state, wait_type=wait_type,
                                     wait_event=wait, blocking_pids=blockers,
                                     query_start=float(query_start) if query_start else None,
                                     transaction_start=float(xact_start) if xact_start else None,
                                     **sql_metadata(statement or "UNKNOWN")))
            sampler_counts["samples"] += 1
            trace_sink.emit(dict(kind="postgres_sample", at=time.time(), start_ns=started,
                                 duration_ms=(time.monotonic_ns() - started) / 1e6, backends=rows))
        except Exception as error:
            sampler_counts["errors"] += 1
            trace_sink.emit(dict(kind="postgres_sampler_error", at=time.time(), error=type(error).__name__))
            if connection:
                connection.close()
                connection = None
            ready.set()
        sampler_stop.wait(.25)
    if connection:
        connection.close()
    sampler_counts["thread_cpu_ms"] = (time.thread_time_ns() - cpu_started) / 1e6


def start_tracing():
    if not trace_sink:
        return
    ready = threading.Event()
    thread = threading.Thread(target=database_sampler, args=(ready,), name="load-pg-sampler", daemon=True)
    thread.start()
    if not ready.wait(10):
        raise RuntimeError("Trace database sampler did not initialize")

    def close():
        sampler_stop.set()
        thread.join(timeout=4)
        trace_sink.close()
        (Path(trace_directory) / "client-summary.json").write_text(json.dumps(
            dict(sink=trace_sink.summary(), database_sampler=sampler_counts), indent=2))
    atexit.register(close)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


async def measure_market_burst(exchange, rate, duration=10):
    """Require real delivered throughput; restore steady load even on failure."""
    target = rate * 5
    await exchange("/control", {"target_updates_per_second": target})
    started = time.time()
    try:
        before = await exchange("/market-metrics")
        await asyncio.sleep(duration)
        after = await exchange("/market-metrics")
        elapsed = after["observed_at_monotonic"] - before["observed_at_monotonic"]
        updates = after["market_update_count"] - before["market_update_count"]
        measured = updates / elapsed
        assert measured + 4 / elapsed >= target, f"Burst feed delivered {measured:.3f}/s below {target}/s minimum"
    finally:
        await exchange("/control", {"target_updates_per_second": rate})
    return dict(burst_started_at=started, burst_ended_at=time.time(), burst_updates_per_second=measured,
                burst_updates=updates, burst_measurement_seconds=elapsed, burst_minimum_updates_per_second=target)


async def main(args):
    start_tracing()
    auth = aiohttp.BasicAuth("operator", "offline-admin")
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout, auth=auth) as client:
        async def exchange(path, payload=None):
            async with client.request("POST" if payload is not None else "GET", "http://exchange:8080" + path, json=payload) as response:
                response.raise_for_status()
                return await response.json()

        async def try_fill(payload):
            async with client.post("http://exchange:8080/control", json=payload) as response:
                body = await response.json()
                if response.status == 409 and body.get("error") == "order_not_fillable":
                    return False
                response.raise_for_status()
                return True

        async def admin(path, due=None):
            identity = request_identity()
            started = time.monotonic_ns()
            status, error = None, None
            try:
                async with client.get("http://bot:8080/api/v1/admin" + path,
                                      headers={"X-Load-Request-ID": identity}) as response:
                    status = response.status
                    return status, await response.json()
            except Exception as exception:
                error = type(exception).__name__
                raise
            finally:
                ended = time.monotonic_ns()
                scheduled = int(due * 1e9) if due is not None else started
                if trace_sink and ((ended - scheduled) / 1e6 >= 100 or error or status != 200):
                    trace_sink.emit(dict(kind="client_request", request_id=identity, path=path, status=status,
                                         scheduled_ns=scheduled, dispatch_ns=started, end_ns=ended, at=time.time(),
                                         queue_wait_ms=(started - scheduled) / 1e6,
                                         network_and_body_ms=(ended - started) / 1e6,
                                         total_ms=(ended - scheduled) / 1e6, error=error))

        acceptance_ms, control_results = [], {}
        fill_lock = asyncio.Lock()

        async def post(path, payload, key):
            started = time.perf_counter()
            started_ns, identity, status, error = time.monotonic_ns(), request_identity(), None, None
            try:
                async with client.post("http://bot:8080/api/v1/admin" + path, json=payload,
                                       headers={"Idempotency-Key": key, "X-Load-Request-ID": identity}) as response:
                    body, status = await response.json(), response.status
                    acceptance_ms.append((time.perf_counter() - started) * 1000)
                    return status, body
            except Exception as exception:
                error = type(exception).__name__
                raise
            finally:
                ended = time.monotonic_ns()
                if trace_sink and ((ended - started_ns) / 1e6 >= 100 or error or status != 202):
                    trace_sink.emit(dict(kind="client_request", request_id=identity, path=path, status=status,
                                         scheduled_ns=started_ns, dispatch_ns=started_ns, end_ns=ended,
                                         at=time.time(), queue_wait_ms=0,
                                         network_and_body_ms=(ended - started_ns) / 1e6,
                                         total_ms=(ended - started_ns) / 1e6, error=error))

        async def controls():
            await asyncio.sleep(min(20, args.duration / 3))
            status, receipt = await post("/reconciliation/check", {"market": "all"}, str(uuid.uuid4()))
            assert status == 202, receipt
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                _, check = await admin("/reconciliation")
                if check["check"] and check["check"]["status"] in ("succeeded", "failed"):
                    control_results["reconciliation"] = check
                    break
                await asyncio.sleep(.2)
            assert "reconciliation" in control_results, "Reconciliation did not complete within its bound"
            if args.burst:
                async with fill_lock:
                    _, state = await admin("/status")
                    payload = dict(action="switch", expected_version=state["control_version"], session_id=state["session"]["id"],
                                   market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=86400)
                    key = str(uuid.uuid4())
                    switch_started = time.perf_counter()
                    replies = await asyncio.gather(*(post("/commands", payload, key) for _ in range(10)))
                    assert {status for status, _ in replies} == {202}
                    assert len({body["id"] for _, body in replies}) == 1
                    stale = await asyncio.gather(*(post("/commands", payload, str(uuid.uuid4())) for _ in range(10)))
                    assert {status for status, _ in stale} == {409}
                    control_results.update(retries=10, expected_conflicts=10, command_id=replies[0][1]["id"])

                    async def applied(receipt, started, maximum):
                        deadline = time.monotonic() + maximum
                        while time.monotonic() < deadline:
                            _, row = await admin("/commands/" + receipt["id"])
                            if row["status"] == "succeeded":
                                return time.perf_counter() - started
                            assert row["status"] not in ("failed", "superseded"), row
                            await asyncio.sleep(.1)
                        raise AssertionError("Actual command application exceeded budget")
                    control_results["command_seconds"] = {"switch": await applied(replies[0][1], switch_started, 15)}
                    _, admitted = await admin("/status")
                    retry_status, retry = await post("/commands", payload, key)
                    _, retried = await admin("/status")
                    assert retry_status == 202 and retry["id"] == replies[0][1]["id"]
                    assert all(admitted["session"][field] == retried["session"][field]
                               for field in ("id", "started_at", "expires_at", "generation"))
                    control_results["retry_deadline_unchanged"] = True
                    for action, maximum in (("stop", 5), ("start", 15)):
                        _, state = await admin("/status")
                        payload = dict(action=action, expected_version=state["control_version"])
                        if action == "stop":
                            payload["session_id"] = state["session"]["id"]
                        else:
                            payload.update(market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=86400)
                        started = time.perf_counter()
                        status, receipt = await post("/commands", payload, str(uuid.uuid4()))
                        assert status == 202, receipt
                        control_results["command_seconds"][action] = await applied(receipt, started, maximum)
                control_results.update(await measure_market_burst(exchange, args.market_rate))
                ledger = await exchange("/ledger")
                if ledger["fills"]:
                    # Old/duplicate private events after newer fills must not
                    # alter the independent ledger or duplicate recorder rows.
                    for fill in reversed(ledger["fills"][-10:]):
                        await exchange("/control", {"replay_trade_id": fill["tradeId"]})

        await exchange("/control", {"target_updates_per_second": args.market_rate})
        before = await exchange("/ledger")
        initial_code, initial_status = await admin("/status")
        assert initial_code == 200
        paths = ["/status", "/orders?limit=1", "/orders?limit=100", "/orders?limit=500", "/fills?limit=100",
                 "/fills?limit=500", "/balances", "/positions", "/performance", "/statistics/daily", "/reconciliation"]
        queue = asyncio.Queue(maxsize=1000)
        latencies, schedule_lag, errors, statuses, per_route = [], [], [], Counter(), {}
        requested, completed, valid_fills, fill_attempts = 0, 0, 0, 0
        auxiliary_requested, auxiliary_completed, auxiliary_latencies, auxiliary_statuses = 0, 0, [], Counter()
        fill_candidate_conflicts = 0
        last_completion, completed_in_window = None, 0
        origin = time.monotonic()
        origin_utc = time.time()
        filling = True
        last_status = None

        async def reader():
            nonlocal completed, last_status, last_completion, completed_in_window, auxiliary_completed
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    return
                due, path, auxiliary = item
                if not auxiliary:
                    schedule_lag.append(max(0, (time.monotonic() - due) * 1000))
                try:
                    resolved = path.replace("{session_id}", ((last_status or initial_status).get("session") or {}).get("id", "unobserved"))
                    status, body = await admin(resolved, due=due)
                    (auxiliary_statuses if auxiliary else statuses)[str(status)] += 1
                    if path == "/status" and status == 200:
                        last_status = body
                    problem = response_problem(path, status, body)
                    if problem:
                        errors.append(dict(path=path, status=status, at=time.time(),
                                           reason=problem,
                                           server_request_id=body.get("request_id") if isinstance(body, dict) else None))
                except Exception as error:
                    errors.append(dict(path=path, error=type(error).__name__, at=time.time()))
                last_completion = time.monotonic()
                if not auxiliary:
                    completed_in_window += last_completion <= origin + args.duration
                # Include dispatch/backlog delay; a slow generator must not
                # hide latency by timing only the eventual network request.
                elapsed = (last_completion - due) * 1000
                (auxiliary_latencies if auxiliary else latencies).append(elapsed)
                per_route.setdefault(path, []).append(elapsed)
                if auxiliary:
                    auxiliary_completed += 1
                else:
                    completed += 1
                queue.task_done()

        async def fill_events():
            nonlocal valid_fills, fill_attempts, fill_candidate_conflicts
            index = 0
            # Generation ends with the measurement window. Drain only requests
            # already dispatched, not hypothetical fills needing future quotes.
            # Fixed-profile throughput is still enforced by the rate budget;
            # Random handover gaps remain visible in the measured fill count.
            while filling:
                if args.random_soak:
                    session = (last_status or {}).get("session") or {}
                    deadline = session.get("step_expires_at")
                    remaining = datetime.fromisoformat(deadline.replace("Z", "+00:00")).timestamp() - time.time() if deadline else 0
                    if not (last_status or {}).get("running") or remaining <= 5:
                        if not filling:
                            return
                        await asyncio.sleep(.1)
                        continue
                target = min(int(args.duration), int(time.monotonic() - origin) + 1)
                if valid_fills >= target:
                    await asyncio.sleep(.05)
                    if not filling:
                        return
                    continue
                async with fill_lock:
                    ledger = await exchange("/working-orders")
                    if not filling:
                        return
                    working = [order for order in ledger["orders"] if order["state"] in ("live", "partially_filled")]
                    # Alternate both markets/sides; tiny valid partial fills retain all
                    # configured risk caps throughout an hour without external capital.
                    market = "LIFE-USDT" if index % 2 == 0 else "LIFE-USDT-SWAP"
                    side = "buy" if (index // 2) % 2 == 0 else "sell"
                    eligible = [order for order in working if order["instId"] == market and order["side"] == side]
                    if eligible:
                        order = eligible[0]
                        amount = Decimal("0.001") if market == "LIFE-USDT" else Decimal("0.1")
                        if Decimal(order["accFillSz"]) + amount <= Decimal(order["sz"]):
                            fill_attempts += 1
                            accepted = await try_fill({"fill": {"clOrdId": order["clOrdId"], "amount": str(amount), "fee": "0.000001"}, "duplicate": index % 7 == 0})
                            if accepted:
                                valid_fills += 1
                                index += 1
                            else:
                                fill_candidate_conflicts += 1
                await asyncio.sleep(.25 if eligible else .1)

        workers = [asyncio.create_task(reader()) for _ in range(10)]
        fills = asyncio.create_task(fill_events()) if args.fills else None
        control_task = asyncio.create_task(controls()) if args.controls else None
        next_minute, summaries = 60, []
        for index in range(int(args.duration * args.read_rate)):
            due = origin + index / args.read_rate
            await asyncio.sleep(max(0, due - time.monotonic()))
            await queue.put((due, paths[index % len(paths)], False))
            requested += 1
            # Preserve every original slot/rate and its latency population. Add
            # two bounded E4 reads/s on top, measured with the same limits.
            for offset, path in ((0, "/sessions/{session_id}/events?limit=100"), (args.read_rate // 2, "/statistics/liquidity")):
                if index % args.read_rate == offset:
                    await queue.put((due, path, True))
                    auxiliary_requested += 1
            if args.operations and index % args.read_rate == 0:
                for path in ("/commands?limit=100", "/sessions?limit=100", "/sessions/{session_id}/configuration"):
                    await queue.put((due, path, True))
                    auxiliary_requested += 1
            if time.monotonic() - origin >= next_minute:
                summaries.append(dict(elapsed_seconds=time.monotonic() - origin, requested=requested, completed=completed,
                                      valid_fills=valid_fills, queued=queue.qsize(), p95_ms=percentile(latencies[-1200:], .95),
                                      errors=len(errors), sample_at=time.time()))
                next_minute += 60
                Path(args.output + ".minutes.json").write_text(json.dumps(summaries, indent=2))
        await asyncio.sleep(max(0, origin + args.duration - time.monotonic()))
        filling = False
        generation_end = time.monotonic()
        queued_at_end = queue.qsize()
        try:
            await asyncio.wait_for(queue.join(), timeout=10)
        except asyncio.TimeoutError:
            errors.append(dict(path="read_generator", error="backlog_not_drained", pending=queue.qsize()))
            for worker in workers:
                worker.cancel()
        if fills:
            try:
                await asyncio.wait_for(fills, timeout=10)
            except asyncio.TimeoutError:
                errors.append(dict(path="fill_generator", error="backlog_not_drained"))
        if control_task:
            try:
                await control_task
            except Exception as error:
                errors.append(dict(path="controls", error=type(error).__name__, detail=str(error)))
        after = await exchange("/ledger")
        drained = time.monotonic() - generation_end
        if all(not worker.cancelled() and not worker.cancelling() for worker in workers):
            for _ in workers:
                await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        result = dict(duration_seconds=args.duration, window_start_ns=int(origin * 1e9),
                      window_end_ns=int((origin + args.duration) * 1e9), window_start_utc=origin_utc,
                      requested_reads=requested, completed_reads=completed,
                      requested_reads_per_second=args.read_rate, completed_reads_per_second=completed / max(args.duration, (last_completion or origin) - origin),
                      completed_reads_in_window=completed_in_window, completed_reads_in_window_per_second=completed_in_window / args.duration,
                      auxiliary=dict(requested_reads=auxiliary_requested, completed_reads=auxiliary_completed,
                                     statuses=dict(auxiliary_statuses), read_p95_ms=percentile(auxiliary_latencies, .95),
                                     read_p99_ms=percentile(auxiliary_latencies, .99)),
                      queued_reads_at_generation_end=queued_at_end,
                      read_p50_ms=percentile(latencies, .5), read_p95_ms=percentile(latencies, .95), read_p99_ms=percentile(latencies, .99),
                      schedule_lag_p95_ms=percentile(schedule_lag, .95), unexpected_errors=len(errors), errors=errors[:30], statuses=dict(statuses),
                      market_updates_per_second=(after["market_update_count"] - before["market_update_count"]) / (after["observed_at_monotonic"] - before["observed_at_monotonic"]),
                      market_updates_sent=after["market_update_count"] - before["market_update_count"],
                      market_measurement_seconds=after["observed_at_monotonic"] - before["observed_at_monotonic"],
                      fills_per_second=valid_fills / args.duration, valid_fills=valid_fills, fill_attempts=fill_attempts,
                      fill_target_shortfall=max(0, int(args.duration) - valid_fills),
                      fill_drain_policy="await_dispatched_requests_only",
                      expected_fill_candidate_conflicts=fill_candidate_conflicts,
                      backlog_drain_seconds=drained, per_minute=summaries,
                      control_results=control_results, command_accept_p95_ms=percentile(acceptance_ms, .95), command_accept_p99_ms=percentile(acceptance_ms, .99),
                      exchange_account_reads=sum(row["path"] in ("/api/v5/account/balance", "/api/v5/account/positions") for row in after["requests"][len(before["requests"]):]),
                      per_route={key: dict(count=len(values), p50=percentile(values, .5), p95=percentile(values, .95), p99=percentile(values, .99)) for key, values in per_route.items()})
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(json.dumps({key: value for key, value in result.items() if key not in ("per_minute", "per_route", "errors")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--read-rate", type=int, default=20)
    parser.add_argument("--market-rate", type=int, default=100)
    parser.add_argument("--output", default="/tmp/load-result.json")
    parser.add_argument("--burst", action="store_true")
    parser.add_argument("--fills", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--controls", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--random-soak", action="store_true", help="Stop injecting synthetic fills in the five-second scheduled handover window; rates remain recorded")
    parser.add_argument("--operations", action="store_true", help="Add one command, session and pinned-configuration read/s; preserve original workload and budgets")
    asyncio.run(main(parser.parse_args()))
