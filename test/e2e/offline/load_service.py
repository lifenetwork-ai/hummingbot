"""Test-only measurement wrapper; calls the unmodified foreground service main.

No control/quote/recorder decisions change. Samples are bounded and exported to
the container's ephemeral /tmp, never used as bot state. No metrics HTTP route.
"""
import asyncio
import atexit
import contextvars
import gc
import json
import logging
import math
import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web
from load_trace import TraceRecorder, TraceSink, backend_pid, instrument_sync, sql_metadata, stack_snapshot
from sqlalchemy import event
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.util.queue import Queue as PoolQueue

sys.path.insert(0, str(Path.cwd()))
from hummingbot.cli import service  # noqa: E402
from hummingbot.cli.admin_api import AdminAPI  # noqa: E402
from hummingbot.cli.admin_models import APIModel  # noqa: E402
from hummingbot.connector.markets_recorder import MarketsRecorder  # noqa: E402
from hummingbot.strategy.strategy_v2_base import StrategyV2Base  # noqa: E402
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator  # noqa: E402

request_queries = contextvars.ContextVar("load_request_queries", default=None)
samples = defaultdict(lambda: deque(maxlen=12000))
totals = defaultdict(int)
route_queries = defaultdict(lambda: {"calls": 0, "queries": 0, "min": None, "max": 0})
trace_directory = os.environ.get("HBOT_LOAD_TRACE_DIRECTORY")
trace_sink = TraceSink(Path(trace_directory) / "server.jsonl") if trace_directory else None
tracer = TraceRecorder(trace_sink.emit) if trace_sink else None
loop_beat_ns = time.monotonic_ns()
watchdog_stop = threading.Event()
gc_started = {}
if trace_sink:
    atexit.register(trace_sink.close)


def timed_sync(owner, name, key):
    def details(args, result):
        pid = backend_pid(result) if key == "pool_checkout_ms" and result is not None else None
        if key in ("commit_ms", "rollback_ms", "db_ping_ms"):
            pid = backend_pid(args[1])
        return {"backend_pid": pid}
    instrument_sync(owner, name, key, samples, totals, tracer, details)


timed_sync(QueuePool, "connect", "pool_checkout_ms")
timed_sync(PoolQueue, "get", "pool_queue_ms")
timed_sync(QueuePool, "_create_connection", "db_connect_ms")
timed_sync(PGDialect_psycopg2, "do_ping", "db_ping_ms")
timed_sync(MarketsRecorder, "_did_fill_order", "recorder_ms")
timed_sync(MarketsRecorder, "store_or_update_executors", "executor_batch_store_ms")
timed_sync(PGDialect_psycopg2, "do_commit", "commit_ms")
timed_sync(PGDialect_psycopg2, "do_rollback", "rollback_ms")
timed_sync(StrategyV2Base, "update_executors_info", "executor_update_ms")
timed_sync(ExecutorOrchestrator, "get_all_reports", "executor_reports_ms")
timed_sync(APIModel, "model_validate", "response_validation_ms")
timed_sync(APIModel, "model_dump", "response_serialization_ms")


from hummingbot.core.mm_liquidity import LiquidityObserver  # noqa: E402
from hummingbot.core.spot_outflow import SpotOutflowProjection  # noqa: E402
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard  # noqa: E402

# Reuse bounded wall/thread-CPU instrumentation for the E0–E5 policies.
from hummingbot.strategy_v2.utils import mm_spread  # noqa: E402

timed_sync(mm_spread, "natr_value", "natr_ms")
timed_sync(mm_spread.SpreadPolicy, "refresh", "candle_sample_ms")
timed_sync(mm_spread.SpreadPolicy, "compose", "spread_compose_ms")
timed_sync(SpotOutflowProjection, "advance", "outflow_advance_ms")
timed_sync(SpotOutflowGuard, "_load", "outflow_rebuild_ms")
timed_sync(LiquidityObserver, "observe", "liquidity_observe_ms")


@event.listens_for(Engine, "before_cursor_execute")
def query_started(connection, cursor, statement, parameters, context, many):
    context._load_started_at = time.perf_counter()
    counter = request_queries.get()
    if counter is not None:
        counter[0] += 1
    totals["sql_statements"] += 1
    if tracer:
        context._load_trace_span = tracer.begin("sql", backend_pid=backend_pid(connection.connection),
                                                **sql_metadata(statement))


@event.listens_for(Engine, "after_cursor_execute")
def query_finished(connection, cursor, statement, parameters, context, many):
    samples["sql_ms"].append((time.perf_counter() - context._load_started_at) * 1000)
    if tracer:
        tracer.end(context._load_trace_span)
        context._load_trace_span = None


@event.listens_for(Engine, "handle_error")
def query_failed(error_context):
    context = error_context.execution_context
    span = getattr(context, "_load_trace_span", None)
    if tracer and span:
        tracer.end(span, error=type(error_context.original_exception).__name__)
        context._load_trace_span = None


original_register = AdminAPI.register


def register(api, app):
    @web.middleware
    async def measured(request, handler):
        counter = [0]
        token = request_queries.set(counter)
        started = time.perf_counter()
        request_id = request.headers.get("X-Load-Request-ID", "")
        if len(request_id) > 80 or not all(char.isalnum() or char in "-_" for char in request_id):
            request_id = None
        span = tracer.begin("http.request", request_id=request_id or None, route=request.path) if tracer else None
        status = 500
        try:
            response = await handler(request)
            status = response.status
            return response
        except web.HTTPException as error:
            status = error.status
            raise
        finally:
            path = request.path
            if "/sessions/" in path and path.endswith("/events"):
                path = "/api/v1/admin/sessions/{id}/events"
            if "/commands/" in path:
                path = "/api/v1/admin/commands/{id}"
            if path.endswith(("/orders", "/fills")):
                path += "?limit=" + request.query.get("limit", "100")
            row = route_queries[path]
            row["calls"] += 1
            row["queries"] += counter[0]
            row["min"] = counter[0] if row["min"] is None else min(row["min"], counter[0])
            row["max"] = max(row["max"], counter[0])
            samples["handler_ms"].append((time.perf_counter() - started) * 1000)
            request_queries.reset(token)
            if tracer:
                tracer.end(span, status=status)
    app.middlewares.append(measured)
    original_register(api, app)


AdminAPI.register = register


class ShutdownTrace(logging.Filter):
    def filter(self, record):
        if record.getMessage().startswith("Error during shutdown:"):
            record.exc_info = sys.exc_info()
        return True


logging.getLogger("hummingbot.core.trading_core").addFilter(ShutdownTrace())


def wrap_books(owner):
    for name in ("_parse_order_book_snapshot_message", "_parse_order_book_diff_message"):
        original = getattr(owner, name)

        def wrapped(original):
            async def measured(self, raw_message, message_queue):
                for row in raw_message.get("data", []):
                    if row.get("ts"):
                        samples["event_age_ms"].append(max(0, time.time() * 1000 - int(row["ts"])))
                samples["book_backlog"].append(sum(queue.qsize() for queue in self._message_queue.values()))
                totals["book_events"] += 1
                return await original(self, raw_message, message_queue)
            return measured
        setattr(owner, name, wrapped(original))


from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_api_order_book_data_source import (  # noqa: E402
    OkxPerpetualAPIOrderBookDataSource,
)
from hummingbot.connector.exchange.okx.okx_api_order_book_data_source import OkxAPIOrderBookDataSource  # noqa: E402

wrap_books(OkxAPIOrderBookDataSource)
wrap_books(OkxPerpetualAPIOrderBookDataSource)


def distribution(values):
    ordered = sorted(tuple(values))
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    return {"count": len(ordered), **{key: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] for key, fraction in
            (("p50", .5), ("p95", .95), ("p99", .99))}, "max": ordered[-1]}


async def probe(host):
    global loop_beat_ns
    next_sample, next_export = time.monotonic(), time.monotonic()
    while True:
        next_sample += .05
        await asyncio.sleep(max(0, next_sample - time.monotonic()))
        loop_beat_ns = time.monotonic_ns()
        samples["event_loop_lag_ms"].append(max(0, (time.monotonic() - next_sample) * 1000))
        if time.monotonic() >= next_export:
            next_export = time.monotonic() + 5
            fields = {}
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    name, amount, _ = line.split()
                    fields[name.rstrip(":")] = int(amount) * 1024
            data = dict(at=time.time(), memory=fields, distributions={key: distribution(values) for key, values in list(samples.items())},
                        runtime_phase=host.phase, runtime_running=host.running,
                        spot_outflow_fill_count=(host.spot_outflow_state or {}).get("fill_count"),
                        latest={key: values[-1] for key, values in list(samples.items()) if values},
                        totals=dict(totals), route_queries=dict(route_queries),
                        checked_out=host.engine.pool.checkedout() if host.engine else None,
                        pool_size=host.engine.pool.size() if host.engine else None)
            if tracer:
                data["trace"] = {**trace_sink.summary(), "dropped_spans": tracer.dropped_spans,
                                 "slow_trace_threshold_ms": tracer.slow_ms}
            cpu = Path("/sys/fs/cgroup/cpu.stat")
            if cpu.is_file():
                data["cgroup_cpu"] = {key: int(value) for key, value in (line.split() for line in cpu.read_text().splitlines())}
            core = host.hb.trading_core if host.hb is not None else None
            strategy = core.strategy if core else None
            orchestrator = getattr(strategy, "executor_orchestrator", None)
            if orchestrator:
                from hummingbot.core.admin_reconciliation import expected_positions
                expected, unsettled = expected_positions(core)
                data["expected_positions"] = [dict(pair=pair, side=side, amount=str(amount)) for (pair, side), amount in (expected or {}).items()]
                data["positions_unsettled"] = unsettled
                data["held_positions"] = {key: [dict(side=row.side.name if row.side else None, amount=str(row.net_amount_base)) for row in rows]
                                          for key, rows in orchestrator.positions_held.items()}
                data["held_executor_count"] = len(orchestrator.executors_ids_position_held)
                data["executor_states"] = [dict(id=executor.config.id, status=executor.status.name,
                                                close_type=executor.close_type.name if executor.close_type else None,
                                                held_orders=executor.executor_info.custom_info.get("held_position_orders", []))
                                           for rows in orchestrator.active_executors.values() for executor in rows if executor.is_closed][-20:]
            temporary = Path("/tmp/load-metrics.new")
            temporary.write_text(json.dumps(data, default=str))
            temporary.replace("/tmp/load-metrics.json")


def watchdog(loop_thread_id):
    previous = 0
    last_cpu = 0
    while not watchdog_stop.wait(.05):
        now = time.monotonic_ns()
        cpu = Path("/sys/fs/cgroup/cpu.stat")
        if now - last_cpu >= 1000000000 and cpu.is_file():
            last_cpu = now
            trace_sink.emit(dict(kind="cpu_sample", at=time.time(), at_ns=now,
                                 cgroup_cpu={key: int(value) for key, value in
                                             (line.split() for line in cpu.read_text().splitlines())}))
        lag_ms = (now - loop_beat_ns) / 1e6 - 50
        if lag_ms < 200 or now - previous < 100000000:
            continue
        previous = now
        frames = sys._current_frames()
        active = tracer.active_snapshot()
        record = dict(kind="loop_stall", at=time.time(), at_ns=now, last_beat_ns=loop_beat_ns,
                      loop_thread_id=loop_thread_id, lag_ms=lag_ms, active_spans=active,
                      stacks={str(tid): stack_snapshot(frames.get(tid)) for tid in
                              {loop_thread_id, *(span["thread_id"] for span in active)}})
        if cpu.is_file():
            record["cgroup_cpu"] = {key: int(value) for key, value in (line.split() for line in cpu.read_text().splitlines())}
        record["pressure"] = {name: path.read_text() for name in ("cpu", "io", "memory")
                              if (path := Path("/proc/pressure") / name).is_file()}
        trace_sink.emit(record)


def gc_trace(phase, info):
    key = threading.get_ident(), info["generation"]
    if phase == "start":
        gc_started[key] = time.monotonic_ns()
    elif key in gc_started:
        started = gc_started.pop(key)
        duration = (time.monotonic_ns() - started) / 1e6
        if duration >= 10:
            trace_sink.emit(dict(kind="gc", at=time.time(), start_ns=started, duration_ms=duration,
                                 thread_id=key[0], generation=key[1], collected=info["collected"]))


if tracer:
    for name in ("observe_once", "collect_funding", "run_reconciliation"):
        original = getattr(service.ServiceHost, name)

        def wrap_async(original, name):
            async def measured(*args, **kwargs):
                span = tracer.begin("service." + name)
                error = None
                try:
                    return await original(*args, **kwargs)
                except BaseException as exception:
                    error = type(exception).__name__
                    raise
                finally:
                    tracer.end(span, error=error)
            return measured
        setattr(service.ServiceHost, name, wrap_async(original, name))


original_run = service.ServiceHost.run


async def run(host):
    task = asyncio.create_task(probe(host))
    if tracer:
        gc.callbacks.append(gc_trace)
        threading.Thread(target=watchdog, args=(threading.get_ident(),), name="load-loop-watchdog", daemon=True).start()
    try:
        return await original_run(host)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if tracer:
            watchdog_stop.set()
            gc.callbacks.remove(gc_trace)
            trace_sink.close()
            (Path(trace_directory) / "server-summary.json").write_text(json.dumps(
                {**trace_sink.summary(), "dropped_spans": tracer.dropped_spans}, indent=2))


service.ServiceHost.run = run
service.main()
