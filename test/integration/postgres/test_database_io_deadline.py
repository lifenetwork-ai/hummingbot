"""Server-side SQL budgets must not be mistaken for a client response deadline."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from test.integration.postgres.response_loss_proxy import response_loss_proxy

import pytest

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['query', 'commit', 'rollback', 'fetch', 'owner'])
async def test_database_io_deadline_discards_unresponsive_session(pg_engine, mode):
    if mode == 'owner':
        bootstrap_postgres(pg_engine, auto_migrate=True)
        BotStateStore(pg_engine, 'hummingbot').ensure()
    async with response_loss_proxy(pg_engine.url) as proxy:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('io_deadline_worker.py')), mode],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'HBOT_IO_TEST_URL': proxy.url.render_as_string(hide_password=False)})
        try:
            ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
            if ready != b'READY\n':
                await asyncio.to_thread(process.wait, timeout=3)
                target = Path('build/availability-fixes')
                target.mkdir(parents=True, exist_ok=True)
                (target / ('io-' + mode + '-setup-stderr.log')).write_bytes(process.stderr.read())
            assert ready == b'READY\n'
            proxy.drop_responses = True
            process.stdin.write(b'go\n')
            process.stdin.flush()
            try:
                line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 4.5)
            except asyncio.TimeoutError:
                pytest.fail('Established DB operation exceeded the client I/O deadline')
            result = json.loads(line)
            assert 2.5 <= result['elapsed'] < 4.5
            assert result['closed'] and result['invalidated']
            if mode == 'owner':
                assert result['lost'] and not result['allow_new_orders'] and result['reacquire_blocked']
            assert await asyncio.to_thread(process.wait, timeout=3) == 0
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, timeout=3)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    if mode == 'owner':
        # The old fenced session is discarded; a replacement retains recovery.
        replacement = BotOwnership(pg_engine, 'hummingbot')
        try:
            assert replacement.try_acquire()
            assert replacement.check_owner()['recovery_state']['halt']
        finally:
            replacement.release()
