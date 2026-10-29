"""Test-only request/worker/SQL correlation using the existing bounded tracer."""
import inspect
import json
import os
from collections import defaultdict, deque
from contextlib import ExitStack
from pathlib import Path

from aiohttp import web
from load_trace import TraceRecorder, TraceSink, backend_pid, instrument_admin_queue, instrument_sync, sql_metadata
from sqlalchemy import event
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.util.queue import Queue as PoolQueue

from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.cli.admin_models import APIModel


def attach(app):
    directory = Path(os.environ['HBOT_LOAD_TRACE_DIRECTORY'])
    sink = TraceSink(directory / f'api-requests-{os.getpid()}.jsonl')
    tracer = TraceRecorder(sink.emit)
    samples, totals = defaultdict(lambda: deque(maxlen=12000)), defaultdict(int)
    cleanup = ExitStack()

    def timed(owner, name, key):
        original = inspect.getattr_static(owner, name)
        cleanup.callback(setattr, owner, name, original)

        def details(args, result):
            connection = result if key == 'pool_checkout_ms' else args[1] if key in ('commit_ms', 'rollback_ms', 'db_ping_ms') else None
            return dict(backend_pid=backend_pid(connection)) if connection is not None else {}

        instrument_sync(owner, name, key, samples, totals, tracer, details)

    def query_started(connection, cursor, statement, parameters, context, many):
        context._process_trace_span = tracer.begin('sql', backend_pid=backend_pid(connection.connection),
                                                   **sql_metadata(statement))

    def query_finished(connection, cursor, statement, parameters, context, many):
        span = context._process_trace_span
        context._process_trace_span = None
        tracer.end(span)

    def query_failed(error):
        context = error.execution_context
        span = getattr(context, '_process_trace_span', None)
        if span is not None:
            context._process_trace_span = None
            tracer.end(span, error=type(error.original_exception).__name__)

    async def start(app):
        for owner, name, key in ((QueuePool, 'connect', 'pool_checkout_ms'),
                                 (PoolQueue, 'get', 'pool_queue_ms'),
                                 (QueuePool, '_create_connection', 'db_connect_ms'),
                                 (PGDialect_psycopg2, 'do_ping', 'db_ping_ms'),
                                 (PGDialect_psycopg2, 'do_commit', 'commit_ms'),
                                 (PGDialect_psycopg2, 'do_rollback', 'rollback_ms'),
                                 (APIModel, 'model_validate', 'response_validation_ms'),
                                 (APIModel, 'model_dump', 'response_serialization_ms')):
            timed(owner, name, key)
        original = AdminDatabaseExecutor.submit
        cleanup.callback(setattr, AdminDatabaseExecutor, 'submit', original)
        instrument_admin_queue(AdminDatabaseExecutor, samples, totals, tracer)
        for name, listener in (('before_cursor_execute', query_started),
                               ('after_cursor_execute', query_finished), ('handle_error', query_failed)):
            event.listen(Engine, name, listener)
            cleanup.callback(event.remove, Engine, name, listener)

    @web.middleware
    async def measured(request, handler):
        identity = request.headers.get('X-Load-Request-ID', '')
        if not identity or len(identity) > 80 or not all(char.isalnum() or char in '-_' for char in identity):
            identity = None
        span = tracer.begin('http.request', request_id=identity, route=request.path)
        status, error = 500, None
        try:
            response = await handler(request)
            status = response.status
            return response
        except BaseException as exception:
            error = type(exception).__name__
            if isinstance(exception, web.HTTPException):
                status = exception.status
            raise
        finally:
            tracer.end(span, status=status, error=error)

    async def close(app):
        cleanup.close()
        sink.close()
        (directory / f'api-requests-{os.getpid()}-summary.json').write_text(json.dumps(
            dict(sink=sink.summary(), dropped_spans=tracer.dropped_spans,
                 active_spans=len(tracer.active_snapshot())), indent=2))

    # Include authentication/error adapters, and drain workers before removing
    # their tracing hooks. Call attach after AdminAPI.register.
    app.middlewares.insert(0, measured)
    app.on_startup.append(start)
    app.on_cleanup.append(close)
    return tracer
