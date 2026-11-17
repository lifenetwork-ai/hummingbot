"""Test-only SQL timeout injection, loaded in the API child of the simulator.

An arm file triggers a bounded number of real PostgreSQL statement timeouts in the daily
fee report. It does not patch trading, ownership, the recorder or connection pool.
"""
SLOW_REPORT_MODE = "wait"
SLOW_REPORT_TIMEOUTS = 1
SLOW_REPORT_TARGET = "daily"


def slow_report_statement(statement, *, target, mode):
    if target == 'overview':
        if mode != 'wait':
            raise ValueError('Overview injection supports bounded server wait only')
        return ('WITH injected_wait AS MATERIALIZED (SELECT pg_sleep(2.1)) ' + statement.lstrip()).replace(
            'FROM trade_fills f', 'FROM injected_wait CROSS JOIN trade_fills f', 1)
    if mode == 'wait':
        statement = statement.replace('WITH scoped AS (', 'WITH injected_wait AS MATERIALIZED (SELECT pg_sleep(2.1)), scoped AS (', 1)
        return statement.replace('FROM scoped CROSS JOIN LATERAL', 'FROM scoped CROSS JOIN injected_wait CROSS JOIN LATERAL', 1)
    return statement.replace('FROM scoped CROSS JOIN LATERAL', 'FROM scoped CROSS JOIN generate_series(1,20) AS injected_cpu CROSS JOIN LATERAL', 1)


if process_role == "api":  # noqa: F821 -- supplied by the test instrumentation wrapper
    import threading

    _slow_daily_lock = threading.Lock()
    _slow_daily_attempts = []

    def save_slow_daily_attempts():
        for filename, value in (("slow-daily-attempts.json", _slow_daily_attempts),
                                ("slow-daily-result.json", _slow_daily_attempts[0])):
            path = Path("/tmp") / filename  # noqa: F821
            temporary = path.with_suffix(".next")
            temporary.write_text(json.dumps(value))  # noqa: F821
            temporary.replace(path)

    @event.listens_for(Engine, "before_cursor_execute", retval=True)  # noqa: F821
    def inject_slow_daily_fee(connection, cursor, statement, parameters, context, many):
        marker = Path("/tmp/slow-daily-arm")  # noqa: F821
        claimed = False
        with _slow_daily_lock:
            if (marker.exists() and len(_slow_daily_attempts) < SLOW_REPORT_TIMEOUTS
                    and ((SLOW_REPORT_TARGET == 'daily' and "json_to_recordset" in statement and "FROM scoped CROSS JOIN LATERAL" in statement)
                         or (SLOW_REPORT_TARGET == 'overview' and '/* overview_fee_source_batch */' in statement))):
                context._slow_daily_index = len(_slow_daily_attempts)
                _slow_daily_attempts.append({"started_at": time.time(), "report_target": SLOW_REPORT_TARGET})  # noqa: F821
                if len(_slow_daily_attempts) == SLOW_REPORT_TIMEOUTS:
                    marker.unlink()
                save_slow_daily_attempts()
                context._slow_daily_injected = claimed = True
        if claimed:
            logging.getLogger(__name__).info("Test-only slow report query started",  # noqa: F821
                                             extra={"event": "test_slow_daily_started", "report_target": SLOW_REPORT_TARGET})
            statement = slow_report_statement(statement, target=SLOW_REPORT_TARGET, mode=SLOW_REPORT_MODE)
        return statement, parameters

    @event.listens_for(Engine, "handle_error")  # noqa: F821
    def record_slow_daily_timeout(error_context):
        context = error_context.execution_context
        if context is not None and getattr(context, "_slow_daily_injected", False):
            with _slow_daily_lock:
                record = _slow_daily_attempts[context._slow_daily_index]
                record.update(finished_at=time.time(), sqlstate=getattr(error_context.original_exception, "pgcode", None))  # noqa: F821
                save_slow_daily_attempts()
