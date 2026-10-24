"""Explicit operator migration: independent of ENABLE_AUTO_MIGRATION."""
import argparse
import logging
import sys

from hummingbot.client.config.runtime_config import RuntimeSettings, load_environment
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PostgreSQL SQL and validate its schema.")
    parser.add_argument("--legacy-cutover", action="store_true", help="Explicit transactional legacy-name conversion.")
    parser.add_argument("--writers-stopped", action="store_true", help="Operator attests all legacy writers are stopped.")
    args = parser.parse_args()
    if args.legacy_cutover and not args.writers_stopped:
        parser.error("--legacy-cutover requires --writers-stopped; stop all legacy revisions/processes first.")
    logging.basicConfig(level=logging.INFO)
    engine = None
    try:
        load_environment()
        settings = RuntimeSettings.from_environment()
        engine = create_postgres_engine(settings)
        bootstrap_postgres(engine, auto_migrate=True, legacy_cutover=args.legacy_cutover)
        return 0
    except Exception as error:
        # Only the deliberately redacted exception types are safe to display.
        from hummingbot.client.config.runtime_config import RuntimeConfigurationError
        from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError
        logging.error("%s", error if isinstance(error, (RuntimeConfigurationError, PostgreSQLBootstrapError))
                      else "PostgreSQL migration failed.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
