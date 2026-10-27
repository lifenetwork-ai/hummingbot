"""Bounded synchronous PostgreSQL connections shared by runtime and operator tools."""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from hummingbot.client.config.runtime_config import RuntimeSettings


def create_postgres_engine(settings: RuntimeSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Keep the existing five-backend ceiling, retaining reader connections
        # instead of closing/recreating overflow after a burst beside the owner.
        pool_size=5,
        max_overflow=0,
        pool_timeout=2,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "application_name": "hummingbot",
            # Runtime requests have a smaller budget than migration DDL; the runner
            # explicitly sets its own LOCAL statement/lock budgets per SQL file.
            "options": ("-c search_path=public -c timezone=UTC -c statement_timeout=2000 -c lock_timeout=1000 "
                        "-c idle_in_transaction_session_timeout=5000"),
        },
    )
