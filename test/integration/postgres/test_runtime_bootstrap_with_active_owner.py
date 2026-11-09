"""A new revision must not replay DDL behind an active recorder transaction."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from test.integration.postgres.test_ownership import owned as _owned

import pytest
import yaml
from dotenv import dotenv_values
from sqlalchemy import text

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres

owned = _owned
ROOT = Path(__file__).resolve().parents[3]


def migration_setting(source):
    if source == 'demo-image':
        content = (ROOT / 'deploy/cloudrun/Dockerfile.demo').read_text()
        return re.search(r'ENABLE_AUTO_MIGRATION=(\w+)', content).group(1)
    if source == 'demo-env':
        return dotenv_values(ROOT / 'deploy/cloudrun/demo/env.example')['ENABLE_AUTO_MIGRATION']
    document = yaml.safe_load((ROOT / 'deploy/cloudrun/service.example.yaml').read_text())
    env = document['spec']['template']['spec']['containers'][0]['env']
    return next(row['value'] for row in env if row['name'] == 'ENABLE_AUTO_MIGRATION')


@pytest.mark.parametrize('source', ['demo-image', 'demo-env', 'cloud-service'])
@pytest.mark.parametrize('automatic', [None, 'true'])
def test_packaged_runtime_bootstrap_preserves_active_recorder_write_authority(owned, source, automatic):
    settings = RuntimeSettings.from_environment({
        'DATABASE_URL': owned.engine.url.render_as_string(hide_password=False),
        'ENABLE_AUTO_MIGRATION': automatic or migration_setting(source),
    })
    standby = create_postgres_engine(settings)
    started = threading.Event()

    def start_revision():
        started.set()
        bootstrap_postgres(standby, auto_migrate=settings.enable_auto_migration, total_timeout=15)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with owned.manager.get_new_session() as recorder:
                # FOR SHARE retains a row lock and a RowShare table lock until commit.
                owned.owner.fence_session(recorder)
                task = pool.submit(start_revision)
                try:
                    assert started.wait(2)
                    deadline = time.monotonic() + 8
                    while not task.done():
                        with owned.engine.connect() as observer:
                            ddl_waiting = observer.execute(text("""
                                SELECT EXISTS (SELECT 1 FROM pg_locks
                                WHERE relation='public.bot_state'::regclass
                                  AND mode='AccessExclusiveLock' AND NOT granted)
                            """)).scalar_one()
                        if ddl_waiting:
                            break
                        assert time.monotonic() < deadline, 'Standby bootstrap did not progress'
                        time.sleep(.01)
                    # A second ORM autoflush must still prove the physical owner.
                    # On the old presets its SELECT waits behind queued ALTER TABLE
                    # while that ALTER waits for this recorder transaction to finish.
                    owned.owner.fence_session(recorder)
                    assert not owned.owner.lost
                finally:
                    recorder.commit()  # Read-only transaction; release any DDL waiter before joining.
                    task.result(timeout=15)
        assert owned.owner.check_owner()['owner_generation'] == owned.owner.generation
        assert owned.manager.persistence_healthy
    finally:
        standby.dispose()
