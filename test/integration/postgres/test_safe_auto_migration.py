"""Automatic DDL must never queue behind an active trading owner."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import inspect, text

from hummingbot.core.bot_ownership import OWNER_KEY, OWNER_NAMESPACE, BotOwnership
from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError, bootstrap_postgres

owned = _owned
HISTORY = 'hbot_schema_migrations'


def test_committed_non_idempotent_script_runs_once_and_rejects_checksum_drift(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    script = tmp_path / '900_insert_once.sql'
    script.write_text('CREATE TABLE migration_once(n integer); INSERT INTO migration_once VALUES (1);')
    bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM migration_once')).scalar_one() == 1
        assert db.execute(text(f"SELECT count(*) FROM {HISTORY} WHERE name='900_insert_once.sql'")).scalar_one() == 1
    script.write_text('INSERT INTO migration_once VALUES (2);')
    with pytest.raises(PostgreSQLBootstrapError, match='checksum'):
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM migration_once')).scalar_one() == 1
        assert db.execute(text('SELECT pg_try_advisory_xact_lock(:ns,:key)'), {'ns': OWNER_NAMESPACE, 'key': OWNER_KEY}).scalar_one()


def test_script_and_history_rollback_together_then_retry(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    script = tmp_path / '900_atomic.sql'
    script.write_text('CREATE TABLE migration_atomic(n integer); SELECT 1/0;')
    with pytest.raises(PostgreSQLBootstrapError, match='900_atomic.sql'):
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    with pg_engine.connect() as db:
        assert 'migration_atomic' not in inspect(db).get_table_names()
        assert not db.execute(text(f"SELECT 1 FROM {HISTORY} WHERE name='900_atomic.sql'")).first()
    script.write_text('CREATE TABLE migration_atomic(n integer); INSERT INTO migration_atomic VALUES (1);')
    bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM migration_atomic')).scalar_one() == 1


def test_migration_timeout_rolls_back_history_and_releases_both_locks(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    (tmp_path / '900_timeout.sql').write_text('CREATE TABLE migration_timeout(n integer); SELECT pg_sleep(2);')
    with pytest.raises(PostgreSQLBootstrapError):
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path, total_timeout=.15)
    with pg_engine.connect() as db:
        assert 'migration_timeout' not in inspect(db).get_table_names()
        assert not db.execute(text(f"SELECT 1 FROM {HISTORY} WHERE name='900_timeout.sql'")).first()
        assert not db.execute(text("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted")).first()


def test_competing_runners_apply_non_idempotent_script_once(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    (tmp_path / '900_concurrent.sql').write_text('CREATE TABLE migration_concurrent(n integer); SELECT pg_sleep(.1); INSERT INTO migration_concurrent VALUES (1);')
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(bootstrap_postgres, pg_engine, auto_migrate=True, scripts=tmp_path) for _ in range(2)]
        for task in tasks:
            task.result(timeout=10)
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM migration_concurrent')).scalar_one() == 1


@pytest.mark.parametrize('untracked_schema', [False, True])
def test_active_owner_defers_all_ddl_including_history_adoption(owned, tmp_path, untracked_schema):
    if untracked_schema:
        with owned.engine.begin() as db:
            db.execute(text(f'DROP TABLE {HISTORY}'))
        scripts = None
    else:
        (tmp_path / '900_pending.sql').write_text('ALTER TABLE bot_state ADD COLUMN migration_probe integer;')
        scripts = tmp_path
    with owned.manager.get_new_session() as recorder:
        owned.owner.fence_session(recorder)
        assert bootstrap_postgres(owned.engine, auto_migrate=True, scripts=scripts, allow_standby=True) is False
        owned.owner.fence_session(recorder)
        recorder.commit()
    assert owned.manager.persistence_healthy and not owned.owner.lost
    with owned.engine.connect() as db:
        assert not db.execute(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE relation='bot_state'::regclass AND mode='AccessExclusiveLock' AND NOT granted)")).scalar_one()
        if untracked_schema:
            assert HISTORY not in inspect(db).get_table_names()
        else:
            assert 'migration_probe' not in {c['name'] for c in inspect(db).get_columns('bot_state')}
    owned.owner.release()
    assert bootstrap_postgres(owned.engine, auto_migrate=True, scripts=scripts, allow_standby=True) is True
    with owned.engine.connect() as db:
        assert HISTORY in inspect(db).get_table_names()
        if not untracked_schema:
            assert 'migration_probe' in {c['name'] for c in inspect(db).get_columns('bot_state')}


def test_pending_incompatible_schema_cannot_claim_standby_readiness(owned, tmp_path):
    with owned.engine.begin() as db:
        db.execute(text('ALTER TABLE orders DROP COLUMN amount'))
    (tmp_path / '900_missing.sql').write_text('ALTER TABLE orders ADD COLUMN amount NUMERIC;')
    with pytest.raises(PostgreSQLBootstrapError, match='missing column orders.amount'):
        bootstrap_postgres(owned.engine, auto_migrate=True, scripts=tmp_path, allow_standby=True)
    assert not owned.owner.lost


def test_pending_explicit_migration_refuses_live_owner_without_ddl(owned, tmp_path):
    (tmp_path / '900_explicit.sql').write_text('ALTER TABLE bot_state ADD COLUMN migration_probe integer;')
    with pytest.raises(PostgreSQLBootstrapError, match='trading owner') as raised:
        bootstrap_postgres(owned.engine, auto_migrate=True, scripts=tmp_path)
    assert raised.value.retryable
    owned.owner.check_owner()


def test_migration_excludes_new_trading_owner_until_commit(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    from hummingbot.model.bot_state_store import BotStateStore
    BotStateStore(pg_engine, 'hummingbot').ensure()
    (tmp_path / '900_slow.sql').write_text('SELECT pg_sleep(.5); ALTER TABLE bot_state ADD COLUMN migration_probe integer;')
    owner = BotOwnership(pg_engine, 'hummingbot')
    started = threading.Event()

    def migrate():
        started.set()
        bootstrap_postgres(pg_engine, auto_migrate=True, scripts=tmp_path)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(migrate)
            assert started.wait(2)
            deadline = time.monotonic() + 5
            while True:
                with pg_engine.connect() as db:
                    locked = db.execute(text('SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype=\'advisory\' AND classid=:ns AND objid=:key AND objsubid=2 AND granted)'), {'ns': OWNER_NAMESPACE, 'key': OWNER_KEY}).scalar_one()
                if locked:
                    break
                assert not task.done() and time.monotonic() < deadline
                time.sleep(.01)
            assert not owner.try_acquire()
            task.result(timeout=10)
        assert owner.try_acquire()
    finally:
        owner.release()
