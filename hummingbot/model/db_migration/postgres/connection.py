"""Bounded synchronous PostgreSQL connections shared by runtime and operator tools."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.engine_diagnostics import trace_runtime
from hummingbot.model.db_migration.postgres.io_deadline import DeadlineConnection, install_io_deadline
from hummingbot.model.ownership_lock_observation import observe_ownership_locks

IDLE_SESSION_TIMEOUT_SECONDS = 30
_OBSERVATION_ENGINE = ContextVar('postgres_observation_engine', default=None)


@contextmanager
def connect_for_observation(engine: Engine):
    """Use the observer's immediate SELECT as the pooled connection probe."""
    token = _OBSERVATION_ENGINE.set(engine)
    try:
        connection = engine.connect()
    finally:
        _OBSERVATION_ENGINE.reset(token)
    with connection:
        yield connection


def create_postgres_engine(settings: RuntimeSettings) -> Engine:
    install_io_deadline()
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Retain warm reader connections within the configured ceiling.
        # API DB concurrency is bounded separately; never create overflow.
        pool_size=settings.database_pool_size,
        max_overflow=0,
        pool_timeout=2,
        hide_parameters=True,
        connect_args={
            "connection_factory": DeadlineConnection,
            "connect_timeout": 5,
            "application_name": "hummingbot",
            # Runtime requests have a smaller budget than migration DDL; the runner
            # explicitly sets its own LOCAL statement/lock budgets per SQL file.
            # A dropped client socket is not proof that a proxy discarded its
            # upstream session. Bound idle (not active SQL) sessions on the
            # server too, so an orphan cannot retain ownership indefinitely.
            # The lock-owning session gets periodic physical reads; pooled readers use pre_ping.
            "options": ("-c search_path=public -c timezone=UTC -c statement_timeout=2000 -c lock_timeout=1000 "
                        "-c idle_in_transaction_session_timeout=5000 "
                        f"-c idle_session_timeout={IDLE_SESSION_TIMEOUT_SECONDS * 1000}"),
        },
    )
    # Pool pre-ping executes through DBAPI before the SQL cursor hooks.
    native_ping = trace_runtime('db.ping')(engine.dialect.do_ping)

    def observed_ping(dbapi_connection):
        if _OBSERVATION_ENGINE.get() is engine and not dbapi_connection.closed:
            return True
        return native_ping(dbapi_connection)

    engine.dialect.do_ping = observed_ping
    if settings.engine_diagnostics_enabled:
        observe_ownership_locks(engine)
    return engine
