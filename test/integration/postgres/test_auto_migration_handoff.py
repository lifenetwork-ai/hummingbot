"""Real supervisor/API/engine standby while the prior revision owns the DB."""
from test.integration.postgres.test_service import eventually, health, start_service, terminate, wait_for_startup

import pytest
from sqlalchemy import inspect, text

from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError, bootstrap_postgres


def test_liquidity_checkpoint_migration_waits_in_ready_standby_for_old_owner(pg_engine, tmp_path):
    processes = []
    try:
        old, old_port, old_log = start_service(tmp_path / 'before-liquidity-checkpoints')
        processes.append((old, old_log))
        eventually(lambda: health(old_port, '/startup')[0] == 200)
        store = BotStateStore(pg_engine, 'hummingbot')
        previous = eventually(lambda: (row if (row := store.read())['owner_uuid'] else None))
        with pg_engine.begin() as db:
            db.execute(text('DROP TABLE bot_liquidity_checkpoints'))
            db.execute(text("DELETE FROM hbot_schema_migrations WHERE name='025_create_bot_liquidity_checkpoints.sql'"))
        with pytest.raises(PostgreSQLBootstrapError, match='missing table public.bot_liquidity_checkpoints'):
            bootstrap_postgres(pg_engine, auto_migrate=False, allow_standby=True)

        new, new_port, new_log = start_service(tmp_path / 'with-liquidity-checkpoints')
        processes.append((new, new_log))
        wait_for_startup(new, new_port, new_log)
        assert health(new_port, '/startup') == (200, {'state': 'standby'})
        assert health(new_port, '/ready')[0] == 503
        assert store.read(publication=True)['owner_uuid'] == previous['owner_uuid']
        assert 'bot_liquidity_checkpoints' not in inspect(pg_engine).get_table_names()

        terminate(old)

        def replacement_owner():
            row = store.read(publication=True)
            return row if row['owner_generation'] > previous['owner_generation'] else None

        replacement = eventually(replacement_owner)
        assert replacement['owner_uuid'] != previous['owner_uuid']
        assert 'bot_liquidity_checkpoints' in inspect(pg_engine).get_table_names()
        assert store.read()['owner_uuid'] == replacement['owner_uuid']
    finally:
        for process, log in processes:
            terminate(process)
            log.close()


@pytest.mark.parametrize('checkpoint_table_missing', [False, True])
def test_automatic_migration_adopts_untracked_schema_only_after_real_service_handoff(pg_engine, tmp_path, checkpoint_table_missing):
    processes = []
    try:
        first, port, log = start_service(tmp_path / 'old-revision')
        processes.append((first, log))
        eventually(lambda: health(port, '/startup')[0] == 200)
        store = BotStateStore(pg_engine, 'hummingbot')
        previous = eventually(lambda: (row if (row := store.read()) and row['owner_uuid'] else None))
        # Simulate the compatible deployed schema before migration history existed.
        with pg_engine.begin() as db:
            db.execute(text('DROP TABLE hbot_schema_migrations'))
            if checkpoint_table_missing:
                db.execute(text('DROP TABLE bot_liquidity_checkpoints'))
        second, second_port, second_log = start_service(tmp_path / 'new-revision', auto_migrate=True)
        processes.append((second, second_log))
        eventually(lambda: health(second_port, '/startup')[0] == 200)
        eventually(lambda: health(second_port, '/startup')[1] == {'state': 'standby'})
        assert health(second_port, '/ready')[0] == 503
        assert store.read(publication=True)['owner_uuid'] == previous['owner_uuid']
        assert 'hbot_schema_migrations' not in inspect(pg_engine).get_table_names()
        terminate(first)
        assert first.returncode == 0
        replacement = eventually(lambda: (row if (row := store.read(publication=True))['owner_generation'] > previous['owner_generation'] else None))
        assert replacement['owner_uuid'] != previous['owner_uuid']
        assert not store.read()['recovery_state'].get('halt')
        with pg_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM hbot_schema_migrations')).scalar_one() > 0
        terminate(second)
        assert second.returncode == 0
    finally:
        for process, log in processes:
            terminate(process)
            log.close()
