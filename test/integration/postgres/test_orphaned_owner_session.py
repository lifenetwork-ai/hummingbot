"""An orphan SQL session must not retain the advisory lock indefinitely."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from test.integration.postgres.response_loss_proxy import response_loss_proxy

import pytest
from sqlalchemy import text

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.mark.asyncio
async def test_orphaned_owner_expires_before_proxy_teardown_and_takeover_retains_halt(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, 'hummingbot').ensure()
    replacement = BotOwnership(pg_engine, 'hummingbot')
    async with response_loss_proxy(pg_engine.url) as proxy:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('io_deadline_worker.py')), 'owner-idle'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'HBOT_IO_TEST_URL': proxy.url.render_as_string(hide_password=False)})
        try:
            assert await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15) == b'READY\n'
            proxy.drop_responses = proxy.drop_requests = proxy.retain_upstream_after_client_close = True
            process.stdin.write(b'go\n')
            process.stdin.flush()
            result = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 5))
            assert result['closed'] and result['lost'] and result['reacquire_blocked']
            assert await asyncio.to_thread(process.wait, timeout=3) == 0
            # The middlebox, not the discarded client, still owns the physical
            # server session. Immediate takeover would be unsafe/incorrect.
            assert not await asyncio.to_thread(replacement.try_acquire)
            deadline = time.monotonic() + 35
            acquired = False
            while time.monotonic() < deadline:
                acquired = await asyncio.to_thread(replacement.try_acquire)
                if acquired:
                    break
                await asyncio.sleep(.25)
            assert acquired, 'Orphaned backend retained ownership beyond the server idle-session budget'
            row = replacement.check_owner()
            assert replacement.generation == 2 and row['recovery_state']['halt']
            assert row['recovery_state']['runtime_active']
            with pytest.raises(SubmissionBlocked):
                replacement.check_owner(new_risk=True)
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            replacement.release()


@pytest.mark.asyncio
async def test_active_owner_survives_idle_budget_while_expired_warm_reader_is_replaced(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, 'hummingbot').ensure()
    owner = BotOwnership(pg_engine, 'hummingbot')
    assert owner.try_acquire()
    original_owner_pid = owner.backend_pid
    try:
        with pg_engine.connect() as reader:
            old_reader_pid = reader.execute(text('SELECT pg_backend_pid()')).scalar_one()
            reader.commit()
        # Exceed the production idle budget without altering it for the test.
        # Normal owner reads, unlike the unused reader, keep the lease alive.
        for _ in range(17):
            await asyncio.sleep(2)
            assert owner.check_owner()['backend_pid'] == original_owner_pid
        with pg_engine.connect() as reader:
            assert reader.execute(text('SELECT pg_backend_pid()')).scalar_one() != old_reader_pid
        assert owner.check_owner()['backend_pid'] == original_owner_pid
        assert not owner.lost
    finally:
        owner.release()
