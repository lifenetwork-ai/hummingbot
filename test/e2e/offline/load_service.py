"""Test-only measurement wrapper; calls the unmodified foreground service main.

No control/quote/recorder decisions change. Samples are bounded and exported to
the container's ephemeral /tmp, never used as bot state. No metrics HTTP route.
"""
import asyncio
import contextvars
import json
import logging
import math
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

sys.path.insert(0, str(Path.cwd()))
from hummingbot.cli import service  # noqa: E402
from hummingbot.cli.admin_api import AdminAPI  # noqa: E402
from hummingbot.connector.markets_recorder import MarketsRecorder  # noqa: E402

request_queries = contextvars.ContextVar("load_request_queries", default=None)
samples = defaultdict(lambda: deque(maxlen=12000))
totals = defaultdict(int)
route_queries = defaultdict(lambda: {"calls": 0, "queries": 0, "min": None, "max": 0})


def timed_sync(owner, name, key):
    original = getattr(owner, name)

    def measured(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            samples[key].append((time.perf_counter() - started) * 1000)
            totals[key + "_count"] += 1
    setattr(owner, name, measured)


timed_sync(QueuePool, "connect", "pool_checkout_ms")
timed_sync(MarketsRecorder, "_did_fill_order", "recorder_ms")


@event.listens_for(Engine, "before_cursor_execute")
def query_started(connection, cursor, statement, parameters, context, many):
    context._load_started_at = time.perf_counter()
    counter = request_queries.get()
    if counter is not None:
        counter[0] += 1
    totals["sql_statements"] += 1


@event.listens_for(Engine, "after_cursor_execute")
def query_finished(connection, cursor, statement, parameters, context, many):
    samples["sql_ms"].append((time.perf_counter() - context._load_started_at) * 1000)


original_register = AdminAPI.register


def register(api, app):
    @web.middleware
    async def measured(request, handler):
        counter = [0]
        token = request_queries.set(counter)
        started = time.perf_counter()
        try:
            return await handler(request)
        finally:
            path = request.path
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
    next_sample, next_export = time.monotonic(), time.monotonic()
    while True:
        next_sample += .05
        await asyncio.sleep(max(0, next_sample - time.monotonic()))
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
                        latest={key: values[-1] for key, values in list(samples.items()) if values},
                        totals=dict(totals), route_queries=dict(route_queries),
                        checked_out=host.engine.pool.checkedout() if host.engine else None,
                        pool_size=host.engine.pool.size() if host.engine else None)
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


original_run = service.ServiceHost.run


async def run(host):
    task = asyncio.create_task(probe(host))
    try:
        return await original_run(host)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


service.ServiceHost.run = run
service.main()
