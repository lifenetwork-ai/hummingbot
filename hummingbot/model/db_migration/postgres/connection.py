"""Bounded synchronous PostgreSQL connections shared by runtime and operator tools."""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.io_deadline import DeadlineConnection, install_io_deadline


def create_postgres_engine(settings: RuntimeSettings) -> Engine:
    install_io_deadline()
    return create_engine(
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
            "options": ("-c search_path=public -c timezone=UTC -c statement_timeout=2000 -c lock_timeout=1000 "
                        "-c idle_in_transaction_session_timeout=5000"),
        },
    )
