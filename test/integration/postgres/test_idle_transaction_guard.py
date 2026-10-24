import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_idle_paused_writer_releases_its_row_lock_and_uncommitted_update(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    victim = pg_engine.connect()
    try:
        assert victim.execute(text("SHOW idle_in_transaction_session_timeout")).scalar_one() == "5s"
        victim.execute(text("UPDATE bot_state SET control_version=1 WHERE bot_id='hummingbot'"))
        time.sleep(5.3)
        with pg_engine.begin() as successor:
            assert successor.execute(text("SELECT control_version FROM bot_state WHERE bot_id='hummingbot' FOR UPDATE")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            victim.execute(text("SELECT 1"))
    finally:
        try:
            victim.close()
        except DBAPIError:
            pass
