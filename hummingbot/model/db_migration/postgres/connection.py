"""Bounded synchronous PostgreSQL connections shared by runtime and operator tools."""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from hummingbot.client.config.runtime_config import RuntimeSettings


def create_postgres_engine(settings: RuntimeSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "application_name": "hummingbot",
            "options": "-c search_path=public -c statement_timeout=30000 -c lock_timeout=5000",
        },
    )
