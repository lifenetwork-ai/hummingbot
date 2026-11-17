"""Disposable-test child for projection crash and unknown-COMMIT boundaries."""
import json
import os
import sys

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.overview_fees import session_fees


def main():
    engine = create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL': os.environ['HBOT_IO_TEST_URL']}))
    mode = sys.argv[1]
    armed = [True]

    def pause():
        if armed[0]:
            armed[0] = False
            print('READY', flush=True)
            input()

    def after_statement(connection, cursor, statement, parameters, context, executemany):
        sql = statement.lstrip().upper()
        if (mode == 'read' and '/* OVERVIEW_FEE_SOURCE_BATCH */' in sql or
                mode == 'contribution' and sql.startswith('INSERT INTO OVERVIEW_FEE_CONTRIBUTIONS') or
                mode == 'aggregate' and sql.startswith('UPDATE OVERVIEW_FEE_SCOPES')):
            pause()

    def before_commit(connection):
        if mode == 'unknown':
            pause()

    event.listen(engine, 'after_cursor_execute', after_statement)
    event.listen(engine, 'commit', before_commit)

    def run():
        return session_fees(engine, bot_id='hummingbot', session_id='session', account_ids=['acct-1'],
                            environment='live', start_ms=1000, end_ms=2000)

    try:
        value = run()
    except DBAPIError:
        print('UNCERTAIN', flush=True)
        input()
        value = run()
    if mode == 'committed':
        pause()
    print(json.dumps(dict(total=str(value['value']['total_usdt']), count=value['value']['processed_fill_count'])), flush=True)
    engine.dispose()


if __name__ == '__main__':
    main()
