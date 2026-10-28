"""Child isolates a truly blocked DBAPI call so a Red test can safely kill it."""
import json
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
from hummingbot.model import get_declarative_base
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine


def main():
    engine = create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL': os.environ['HBOT_IO_TEST_URL']}))
    mode = sys.argv[1]
    owner = None
    if mode == 'owner':
        get_declarative_base()
        owner = BotOwnership(engine, 'hummingbot')
        assert owner.try_acquire()
        owner.prepare(0, 'audit.yml')
        owner.allow_new_orders = True
        connection = owner.connection
    else:
        connection = engine.connect()
    raw = connection.connection.driver_connection
    result = None
    if mode == 'fetch':
        result = connection.execution_options(stream_results=True).execute(text('SELECT generate_series(1,10000)'))
        result.fetchone()
    else:
        connection.exec_driver_sql('SELECT 1')
    print('READY', flush=True)
    input()
    started = time.monotonic()
    try:
        if mode == 'query':
            connection.exec_driver_sql('SELECT pg_sleep(10)')
        elif mode == 'commit':
            connection.commit()
        elif mode == 'rollback':
            connection.rollback()
        elif mode == 'fetch':
            result.fetchall()
        else:
            owner.check_owner()
    except (DBAPIError, OwnershipLost) as error:
        output = dict(elapsed=time.monotonic() - started, error_type=type(error).__name__,
                      closed=bool(raw.closed), invalidated=connection.invalidated)
        if owner is not None:
            output.update(lost=owner.lost, allow_new_orders=owner.allow_new_orders)
            try:
                owner.try_acquire()
            except OwnershipLost:
                output['reacquire_blocked'] = True
        print(json.dumps(output), flush=True)
    else:
        raise AssertionError('Response-loss operation unexpectedly returned successfully')
    finally:
        connection.close()
        engine.dispose()


if __name__ == '__main__':
    main()
