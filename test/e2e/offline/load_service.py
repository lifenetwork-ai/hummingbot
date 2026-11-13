"""Test-only measurement wrapper; calls the unmodified foreground service main.

No control/quote/recorder decisions change. Samples are bounded and exported to
the container's ephemeral /tmp, never used as bot state. No metrics HTTP route.
"""
import asyncio
import atexit
import contextvars
import faulthandler
import gc
import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web
from load_incidents import IncidentBuffer, capture_limit
from load_process_metrics import MetricsExporter, collect_requested
from load_trace import (
    DeferredGCTrace,
    FullGCProbe,
    TraceRecorder,
    TraceSink,
    backend_pid,
    instrument_admin_queue,
    instrument_sync,
    runtime_trace_sink,
    sample_heartbeat,
    sql_metadata,
    stack_snapshot,
    timed_cpu_sample,
)
from sqlalchemy import event
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.util.queue import Queue as PoolQueue

sys.path.insert(0, str(Path.cwd()))
from hummingbot.cli import service  # noqa: E402
from hummingbot.cli.admin_api import AdminAPI  # noqa: E402
from hummingbot.cli.admin_database import AdminDatabaseExecutor  # noqa: E402
from hummingbot.cli.admin_models import APIModel  # noqa: E402
from hummingbot.connector.markets_recorder import MarketsRecorder  # noqa: E402
from hummingbot.core.bot_ownership import BotOwnership  # noqa: E402
from hummingbot.strategy.strategy_v2_base import StrategyV2Base  # noqa: E402
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator  # noqa: E402

if os.environ.get('HBOT_LOAD_GOOGLE_FIXTURE'):
    from security_fixture import install_identity_fixture, install_provider_fault_fixture
    install_identity_fixture(os.environ['HBOT_LOAD_GOOGLE_FIXTURE'])
    install_provider_fault_fixture()

# Memory diagnostics are test-only and keep weak references, never old runners.
MEMORY_DIAGNOSTICS = os.environ.get("HBOT_MEMORY_DIAGNOSTICS") == "1"
if MEMORY_DIAGNOSTICS:
    from weakref import WeakSet
    memory_strategies, memory_providers = WeakSet(), WeakSet()
    original_strategy_init = StrategyV2Base.__init__

    def memory_strategy_init(self, *args, **kwargs):
        original_strategy_init(self, *args, **kwargs)
        memory_strategies.add(self)
        memory_providers.add(self.market_data_provider)

    StrategyV2Base.__init__ = memory_strategy_init
memory_gc_runs = 0


def retention_counts(core, orchestrator):
    from hummingbot.core.event.events import OrderFilledEvent
    connectors = core.connector_manager.connectors if core else {}
    resident = [executor for rows in orchestrator.active_executors.values() for executor in rows] if orchestrator else []
    positions = [position for rows in orchestrator.positions_held.values() for position in rows] if orchestrator else []
    return dict(tasks=len(asyncio.all_tasks()), strategies=len(memory_strategies), providers=len(memory_providers),
                gc_runs=memory_gc_runs, resident_executors=len(resident),
                processed_executor_ids=len(orchestrator.executors_ids_position_held) if orchestrator else 0,
                position_order_ids=sum(len(position.order_ids) for position in positions),
                resident_held_order_ids=len({order.get("client_order_id") for executor in resident
                                             for order in executor.executor_info.custom_info.get("held_position_orders", [])}),
                connectors={name: dict(fill_events=sum(isinstance(event, OrderFilledEvent) for event in connector.event_logs),
                                       legacy_fills=len(connector._current_trade_fills),
                                       legacy_order_ids=len(connector._exchange_order_ids))
                            for name, connector in connectors.items()})


request_queries = contextvars.ContextVar("load_request_queries", default=None)
samples = defaultdict(lambda: deque(maxlen=12000))
totals = defaultdict(int)
process_role = sys.argv[sys.argv.index('--role') + 1] if '--role' in sys.argv else 'combined'
if os.environ.get('HBOT_PENDING_POLL_DIAGNOSTICS') == '1' and process_role == 'engine':
    from hummingbot.connector.exchange_py_base import ExchangePyBase

    original_poll_interval = ExchangePyBase._get_poll_interval

    def pending_with_fresh_stream(connector, timestamp):
        stream = connector._user_stream_tracker
        return (stream is not None and 0 <= timestamp - stream.last_recv_time <= connector.TICK_INTERVAL_LIMIT
                and any(getattr(order, '_managed_recovery_order', False) is True and not order.is_done
                        and (order.is_pending_cancel_confirmation or getattr(order, '_terminal_proof_pending', False) is True)
                        for order in connector.in_flight_orders.values()))

    def measured_poll_interval(self, timestamp):
        interval = original_poll_interval(self, timestamp)
        if pending_with_fresh_stream(self, timestamp):
            totals['pending_fresh_ws_ticks.' + self.name] += 1
            if interval == self.SHORT_POLL_INTERVAL:
                totals['pending_fresh_ws_short_ticks.' + self.name] += 1
        return interval

    ExchangePyBase._get_poll_interval = measured_poll_interval
    # Concrete connectors override account reads; observe their actual worker
    # entry without replacing requests, outcomes, clocks or trading authority.
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange

    def account_counter(original):
        async def measured(self):
            if pending_with_fresh_stream(self, self.current_timestamp):
                totals['pending_fresh_ws_account_reads.' + self.name] += 1
            return await original(self)
        return measured

    for pending_connector in (OkxExchange, OkxPerpetualDerivative):
        pending_connector._status_polling_loop_fetch_updates = account_counter(pending_connector._status_polling_loop_fetch_updates)
route_queries = defaultdict(lambda: {"calls": 0, "queries": 0, "min": None, "max": 0})
trace_directory = os.environ.get("HBOT_LOAD_TRACE_DIRECTORY")
incident_directory = os.environ.get('HBOT_LOAD_INCIDENT_DIRECTORY')
trace_sink = runtime_trace_sink(trace_directory, process_role)
incident_sink = TraceSink(Path(incident_directory) / (process_role + '-incidents.jsonl'),
                          max_bytes=32 * 1024**2, queue_limit=256, append=True,
                          reserved_bytes=8 * 1024**2,
                          reserved_kinds={'probe_failure', 'probe_recovered', 'loop_stall',
                                          'loop_stall_trigger', 'gc'}) if incident_directory else None
incidents = IncidentBuffer(lambda row: incident_sink.emit({**row, 'capturing_pid': os.getpid(),
                                                          'capturing_role': process_role}),
                           max_incidents=capture_limit(os.environ.get('HBOT_LOAD_INCIDENT_MAX_CAPTURES', '32'))) if incident_sink else None
tracer = TraceRecorder(trace_sink.emit) if trace_sink else TraceRecorder(incidents.emit, max_children=16) if incidents else None
loop_beat_ns = time.monotonic_ns()
watchdog_stop = threading.Event()
gc_trace = DeferredGCTrace() if tracer else None
full_gc_probe = FullGCProbe() if os.environ.get('HBOT_LOAD_GC_DIAGNOSTICS') == '1' else None
metrics_exporter = MetricsExporter(Path('/tmp/load-metrics-api.json' if process_role == 'api' else '/tmp/load-metrics.json'))
if trace_sink:
    atexit.register(trace_sink.close)
if incident_sink:
    atexit.register(incident_sink.close)
    # C-level signal handler can dump stacks even if every Python thread is
    # waiting on a recorder lock. Parent sends only after established failures.
    faulthandler.register(signal.SIGUSR2, file=sys.stderr, all_threads=True)


def emit_trace(record):
    if trace_sink:
        trace_sink.emit(record)
    if incidents:
        incidents.emit(record)


mm_activity = None
mm_activity_sink = None
if os.environ.get('HBOT_MM_ACTIVITY_DIAGNOSTICS') == '1' and process_role == 'engine':
    from load_mm_activity import (
        ActivityRecorder,
        instrument_checkpoint,
        instrument_cycle,
        instrument_order,
        instrument_stop_actions,
        instrument_terminal,
    )

    from controllers.market_making.life_mm import LifeMMController
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor

    if incidents is None:
        raise ValueError('MM activity requires bounded incident capture')
    mm_activity_sink = TraceSink(Path(incident_directory) / 'mm-activity.jsonl',
                                 max_bytes=8 * 1024**2, queue_limit=128, append=True)

    def emit_mm_activity(record):
        emit_trace(record)
        mm_activity_sink.emit({**record, 'capturing_role': process_role, 'capturing_pid': os.getpid()})

    mm_activity = ActivityRecorder(emit_mm_activity)
    instrument_cycle(StrategyV2Base, 'tick', 'strategy.tick', mm_activity,
                     details=lambda actor: dict(ready_to_trade=bool(actor.ready_to_trade)))
    instrument_cycle(LifeMMController, 'control_task', 'controller.cycle', mm_activity, asynchronous=True,
                     details=lambda actor: dict(controller_id=actor.config.id,
                                                data_ready=bool((actor.processed_data or {}).get('ready')),
                                                session_admitted=bool(actor._session_admitted)))
    for connector in (OkxExchange, OkxPerpetualDerivative):
        instrument_order(connector, '_place_order', 'order.submit', mm_activity)
        instrument_order(connector, '_place_cancel', 'order.cancel', mm_activity)
    for method in ('process_order_completed_event', 'process_order_canceled_event'):
        instrument_terminal(OrderExecutor, method, mm_activity)
    instrument_checkpoint(MarketsRecorder, 'checkpoint_held_positions', mm_activity)
    for method, operation in (('stop_core', 'service.stop_core'),
                              ('validate_selection', 'service.prepare'), ('start_strategy', 'service.start')):
        instrument_cycle(service.ServiceHost, method, operation, mm_activity, asynchronous=True,
                         start_details=lambda actor: dict(service_phase=actor.phase))

    def shutdown_context(actor):
        orders = (actor._open_order, actor._close_order, actor._take_profit_limit_order)
        return dict(executor_id=actor.config.id, order_ids=[order.order_id for order in orders if order],
                    all_orders_completed=actor.all_orders_completed())

    instrument_cycle(PositionExecutor, 'control_shutdown_process', 'executor.shutdown', mm_activity, asynchronous=True,
                     start_details=shutdown_context,
                     details=lambda actor: dict(status=actor.status.name, all_orders_completed=actor.all_orders_completed()))
    instrument_cycle(OrderExecutor, 'control_shutdown_process', 'executor.shutdown', mm_activity, asynchronous=True,
                     start_details=lambda actor: dict(executor_id=actor.config.id,
                                                      order_ids=[tracked.order_id for tracked in actor._execution_orders()]),
                     details=lambda actor: dict(status=actor.status.name))
    for method, reason in (('stop_quote_actions', 'readiness_or_inventory'),
                           ('_defense_refresh_actions', 'spread_quote_invalid'),
                           ('executors_to_refresh', 'executor_refresh'),
                           ('executors_to_early_stop', 'executor_early_stop')):
        instrument_stop_actions(LifeMMController, method, reason, mm_activity)


def save_incident_summary():
    destination = Path(incident_directory) / (process_role + '-incident-summary.json')
    temporary = destination.with_suffix('.new')
    temporary.write_text(json.dumps({**incidents.summary(), 'at': time.time(), 'process_id': os.getpid(),
                                     'sink': incident_sink.summary(), 'dropped_spans': tracer.dropped_spans,
                                     'gc_trace': gc_trace.summary()}))
    temporary.replace(destination)


def timed_sync(owner, name, key, *, slow_only=False):
    def details(args, result):
        pid = backend_pid(result) if key == "pool_checkout_ms" and result is not None else None
        if key in ("commit_ms", "rollback_ms", "db_ping_ms"):
            pid = backend_pid(args[1])
        return {"backend_pid": pid}
    instrument_sync(owner, name, key, samples, totals, tracer, details, slow_only=slow_only)


timed_sync(QueuePool, "connect", "pool_checkout_ms")
timed_sync(PoolQueue, "get", "pool_queue_ms")
timed_sync(QueuePool, "_create_connection", "db_connect_ms")
timed_sync(PGDialect_psycopg2, "do_ping", "db_ping_ms")
timed_sync(MarketsRecorder, "_did_fill_order", "recorder_ms")
timed_sync(MarketsRecorder, "_store_fill", "fill_store_ms")
timed_sync(MarketsRecorder, "save_market_states", "market_checkpoint_ms")
timed_sync(BotOwnership, "_read_owner", "ownership_check_ms")
timed_sync(MarketsRecorder, "store_or_update_executors", "executor_batch_store_ms")
timed_sync(PGDialect_psycopg2, "do_commit", "commit_ms")
timed_sync(PGDialect_psycopg2, "do_rollback", "rollback_ms")
timed_sync(StrategyV2Base, "update_executors_info", "executor_update_ms")
timed_sync(ExecutorOrchestrator, "get_all_reports", "executor_reports_ms")
timed_sync(APIModel, "model_validate", "response_validation_ms")
timed_sync(APIModel, "model_dump", "response_serialization_ms")
instrument_admin_queue(AdminDatabaseExecutor, samples, totals, tracer)


from hummingbot.core.mm_liquidity import LiquidityObserver  # noqa: E402
from hummingbot.core.spot_outflow import SpotOutflowProjection  # noqa: E402
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard  # noqa: E402

# Reuse bounded wall/thread-CPU instrumentation for the E0–E5 policies.
from hummingbot.strategy_v2.utils import mm_spread  # noqa: E402

timed_sync(mm_spread, "natr_value", "natr_ms")
timed_sync(mm_spread.SpreadPolicy, "refresh", "candle_sample_ms")
timed_sync(mm_spread.SpreadPolicy, "compose", "spread_compose_ms")
timed_sync(SpotOutflowProjection, "advance", "outflow_advance_ms", slow_only=True)
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


async def probe(host):
    global loop_beat_ns, memory_gc_runs
    next_sample, next_export = time.monotonic(), time.monotonic()
    while True:
        next_sample += .05
        await asyncio.sleep(max(0, next_sample - time.monotonic()))
        loop_beat_ns = time.monotonic_ns()
        samples["event_loop_lag_ms"].append(max(0, (time.monotonic() - next_sample) * 1000))
        if time.monotonic() >= next_export:
            next_export = time.monotonic() + 5
            data = dict(at=time.time(), process_role=process_role, process_id=os.getpid(),
                        gc_policy=dict(enabled=gc.isenabled(), thresholds=gc.get_threshold(),
                                       permanent_objects=gc.get_freeze_count()),
                        runtime_phase=host.phase, runtime_running=getattr(host, 'running', False),
                        spot_outflow_fill_count=(getattr(getattr(host, 'observations', None), 'outflow', None) or {}).get("fill_count"),
                        latest={key: values[-1] for key, values in list(samples.items()) if values},
                        totals=dict(totals), route_queries={key: dict(value) for key, value in list(route_queries.items())},
                        checked_out=host.engine.pool.checkedout() if host.engine else None,
                        pool_size=host.engine.pool.size() if host.engine else None)
            if full_gc_probe is not None:
                data['full_gc_events'] = list(full_gc_probe.events)
            if mm_activity is not None:
                data['mm_activity'] = mm_activity.summary()
                data['mm_activity_sink'] = mm_activity_sink.summary()
            if tracer:
                data["trace"] = {**(trace_sink.summary() if trace_sink else incident_sink.summary()),
                                 "dropped_spans": tracer.dropped_spans, "slow_trace_threshold_ms": tracer.slow_ms,
                                 'gc_trace': gc_trace.summary()}
            if incidents:
                data['incident_capture'] = incidents.summary()
            core = host.hb.trading_core if getattr(host, 'hb', None) is not None else None
            strategy = core.strategy if core else None
            orchestrator = getattr(strategy, "executor_orchestrator", None)
            if orchestrator:
                from hummingbot.core.admin_reconciliation import expected_positions
                expected, unsettled = expected_positions(core)
                data['positions_scope_supported'] = expected is not None
                data["expected_positions"] = [dict(pair=pair, side=side, amount=str(amount)) for (pair, side), amount in (expected or {}).items()]
                data["positions_unsettled"] = unsettled
                data["held_positions"] = {key: [dict(side=row.side.name if row.side else None, amount=str(row.net_amount_base)) for row in rows]
                                          for key, rows in orchestrator.positions_held.items()}
                data["held_executor_count"] = len(orchestrator.executors_ids_position_held)
                data["executor_states"] = [dict(id=executor.config.id, status=executor.status.name,
                                                close_type=executor.close_type.name if executor.close_type else None,
                                                held_orders=executor.executor_info.custom_info.get("held_position_orders", []))
                                           for rows in orchestrator.active_executors.values() for executor in rows if executor.is_closed][-20:]
            if MEMORY_DIAGNOSTICS:
                marker = Path("/tmp/memory-review-collect")
                if collect_requested(process_role, marker):
                    memory_gc_runs += 1
                data["retention"] = retention_counts(core, orchestrator)
            data['metrics_export_skipped'] = metrics_exporter.skipped
            metrics_exporter.submit(data, samples)


def watchdog(loop_thread_id):
    previous = 0
    last_cpu = 0
    last_sample = time.monotonic_ns()
    last_summary = 0
    last_sequence = 0
    trigger_path = Path('/tmp/load-incident-trigger.json')
    while not watchdog_stop.wait(.05):
        gc_trace.drain(emit_trace)
        now = time.monotonic_ns()
        cpu = Path("/sys/fs/cgroup/cpu.stat")
        if incidents:
            try:
                for entry in json.loads(trigger_path.read_text())['events']:
                    if entry['sequence'] > last_sequence:
                        incidents.trigger({**entry, 'observed_at_ns': now, 'capturing_role': process_role})
                        last_sequence = entry['sequence']
            except (OSError, ValueError, KeyError):
                pass
            if now - last_summary >= 1000000000:
                last_summary = now
                try:
                    save_incident_summary()
                except OSError:
                    pass  # Evidence filesystem errors cannot affect trading health.
        now = time.monotonic_ns()  # Summary/trigger I/O above may have descheduled this thread.
        if now - last_cpu >= (250000000 if incidents else 1000000000) and cpu.is_file():
            record = timed_cpu_sample(cpu, process_role=process_role, previous_sample_ns=last_sample)
            last_cpu = record['sample_started_ns']
            if incidents:
                record['context_started_ns'] = time.monotonic_ns()
                heartbeat = sample_heartbeat(lambda: loop_beat_ns)
                active = tracer.active_snapshot(limit=16)
                frames = sys._current_frames()
                record.update(active_spans=active, heartbeat=heartbeat,
                              stacks={str(tid): stack_snapshot(frames.get(tid)) for tid in
                                      {loop_thread_id, *(span['thread_id'] for span in active[:7])}},
                              pressure={name: path.read_text() for name in ('cpu', 'io', 'memory')
                                        if (path := Path('/proc/pressure') / name).is_file()})
                record['context_ended_ns'] = time.monotonic_ns()
            emit_trace(record)
            last_sample = record['sample_started_ns']
        heartbeat = sample_heartbeat(lambda: loop_beat_ns)
        now, lag_ms = heartbeat['at_ns'], heartbeat['lag_ms']
        if not heartbeat['consistent'] or lag_ms < 200 or now - previous < 100000000:
            continue
        previous = now
        context_started = time.monotonic_ns()
        frames = sys._current_frames()
        active = tracer.active_snapshot(limit=16 if incidents else None)
        record = dict(kind="loop_stall", at=heartbeat['at'], at_ns=now, last_beat_ns=heartbeat['last_beat_ns'],
                      sample_schema_version=2, heartbeat=heartbeat, context_started_ns=context_started,
                      process_role=process_role,
                      loop_thread_id=loop_thread_id, lag_ms=lag_ms, active_spans=active,
                      stacks={str(tid): stack_snapshot(frames.get(tid)) for tid in
                              {loop_thread_id, *(span["thread_id"] for span in active)}})
        if cpu.is_file():
            record["cgroup_cpu"] = {key: int(value) for key, value in (line.split() for line in cpu.read_text().splitlines())}
        record["pressure"] = {name: path.read_text() for name in ("cpu", "io", "memory")
                              if (path := Path("/proc/pressure") / name).is_file()}
        record['context_ended_ns'] = time.monotonic_ns()
        if incidents:
            incidents.trigger(dict(kind='loop_stall_trigger', at_ns=now, at=heartbeat['at'],
                                   process_role=process_role, lag_ms=lag_ms))
        emit_trace(record)


if tracer:
    from hummingbot.cli.funding_observer import FundingObserver

    for target, method, name in ((service.ServiceHost, "observe_once", "observe_once"),
                                 (FundingObserver, "collect", "collect_funding"),
                                 (service.ServiceHost, "run_reconciliation", "run_reconciliation")):
        original = getattr(target, method)

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
        setattr(target, method, wrap_async(original, name))


def instrument_run(original):
    async def run(host):
        task = asyncio.create_task(probe(host))
        watchdog_thread = None
        if full_gc_probe is not None:
            gc.callbacks.append(full_gc_probe)
        if tracer:
            gc.callbacks.append(gc_trace)
            watchdog_thread = threading.Thread(target=watchdog, args=(threading.get_ident(),), name="load-loop-watchdog", daemon=True)
            watchdog_thread.start()
        try:
            return await original(host)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.to_thread(metrics_exporter.close)
            if full_gc_probe is not None:
                gc.callbacks.remove(full_gc_probe)
            if tracer:
                watchdog_stop.set()
                gc.callbacks.remove(gc_trace)
                watchdog_thread.join(timeout=1)
                gc_trace.drain(emit_trace)
            if trace_sink:
                trace_sink.close()
                name = 'server-api-summary.json' if process_role == 'api' else 'server-summary.json'
                (Path(trace_directory) / name).write_text(json.dumps(
                    {**trace_sink.summary(), "dropped_spans": tracer.dropped_spans}, indent=2))
            if incidents:
                incident_sink.close()
                save_incident_summary()
            if mm_activity_sink:
                mm_activity_sink.close()
                (Path(incident_directory) / 'mm-activity-summary.json').write_text(json.dumps(mm_activity_sink.summary()))
    return run


service.ServiceHost.run = instrument_run(service.ServiceHost.run)
if process_role == 'api':
    from hummingbot.cli.admin_service import AdminService
    AdminService.run = instrument_run(AdminService.run)
service.main()
