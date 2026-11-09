"""Real supervisor/API/engine standby while the prior revision owns the DB."""
from test.integration.postgres.test_service import eventually, health, start_service, terminate

from sqlalchemy import inspect, text

from hummingbot.model.bot_state_store import BotStateStore


def test_automatic_migration_adopts_untracked_schema_only_after_real_service_handoff(pg_engine, tmp_path):
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
        second, second_port, second_log = start_service(tmp_path / 'new-revision', auto_migrate=True)
        processes.append((second, second_log))
        eventually(lambda: health(second_port, '/startup')[0] == 200)
        eventually(lambda: health(second_port, '/startup')[1] == {'state': 'standby'})
        assert health(second_port, '/ready')[0] == 503
        assert store.read()['owner_uuid'] == previous['owner_uuid']
        assert 'hbot_schema_migrations' not in inspect(pg_engine).get_table_names()
        terminate(first)
        assert first.returncode == 0
        replacement = eventually(lambda: (row if (row := store.read())['owner_generation'] > previous['owner_generation'] else None))
        assert replacement['owner_uuid'] != previous['owner_uuid']
        assert not replacement['recovery_state'].get('halt')
        with pg_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM hbot_schema_migrations')).scalar_one() > 0
        terminate(second)
        assert second.returncode == 0
    finally:
        for process, log in processes:
            terminate(process)
            log.close()
