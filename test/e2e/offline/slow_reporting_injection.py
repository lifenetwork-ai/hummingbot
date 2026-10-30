"""Test-only SQL timeout injection, loaded in the API child of the simulator.

An arm file triggers exactly one real PostgreSQL statement timeout in the daily
fee report. It does not patch trading, ownership, the recorder or connection pool.
"""
SLOW_REPORT_MODE = "wait"

if process_role == "api":  # noqa: F821 -- supplied by the test instrumentation wrapper
    @event.listens_for(Engine, "before_cursor_execute", retval=True)  # noqa: F821
    def inject_slow_daily_fee(connection, cursor, statement, parameters, context, many):
        marker = Path("/tmp/slow-daily-arm")  # noqa: F821
        if marker.exists() and "json_to_recordset" in statement and "FROM scoped CROSS JOIN LATERAL" in statement:
            marker.unlink()
            record = {"started_at": time.time()}  # noqa: F821
            Path("/tmp/slow-daily-result.json").write_text(json.dumps(record))  # noqa: F821
            logging.getLogger(__name__).info("Test-only slow daily query started",  # noqa: F821
                                             extra={"event": "test_slow_daily_started"})
            context._slow_daily_injected = True
            if SLOW_REPORT_MODE == "wait":
                statement = statement.replace("WITH scoped AS (", "WITH injected_wait AS MATERIALIZED (SELECT pg_sleep(2.1)), scoped AS (", 1)
                statement = statement.replace("FROM scoped CROSS JOIN LATERAL", "FROM scoped CROSS JOIN injected_wait CROSS JOIN LATERAL", 1)
            else:
                statement = statement.replace("FROM scoped CROSS JOIN LATERAL", "FROM scoped CROSS JOIN generate_series(1,20) AS injected_cpu CROSS JOIN LATERAL", 1)
        return statement, parameters

    @event.listens_for(Engine, "handle_error")  # noqa: F821
    def record_slow_daily_timeout(error_context):
        context = error_context.execution_context
        if context is not None and getattr(context, "_slow_daily_injected", False):
            path = Path("/tmp/slow-daily-result.json")  # noqa: F821
            record = json.loads(path.read_text())  # noqa: F821
            record.update(finished_at=time.time(), sqlstate=getattr(error_context.original_exception, "pgcode", None))  # noqa: F821
            path.write_text(json.dumps(record))  # noqa: F821
