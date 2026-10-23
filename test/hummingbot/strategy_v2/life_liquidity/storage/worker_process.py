"""Physical foreground-signal fixture; no DB, keys, network SDK or .env."""
import asyncio
import sys
from types import SimpleNamespace

from hummingbot.strategy_v2.life_liquidity.storage.postgres import worker


async def launch(**kwargs):
    if sys.argv[1] == 'startup_blocked':
        await asyncio.Event().wait()
    service = SimpleNamespace(revoke=lambda: worker.event('FIXTURE_REVOKED'), runtime=SimpleNamespace(healthy=False), _trading_ready=lambda: False)
    await kwargs['on_started'](service)
    if sys.argv[1] == 'failed':
        raise RuntimeError('synthetic-do-not-disclose-credential')
    await kwargs['stop'].wait()
    worker.event('FIXTURE_CLEANUP_STARTED')
    if sys.argv[1] == 'late_cleanup':
        asyncio.create_task(asyncio.Event().wait())  # Original physical cleanup still owns a slot.
        raise TimeoutError('synthetic-do-not-disclose-credential')
    if sys.argv[1] == 'blocked':
        await asyncio.Event().wait()
    worker.event('FIXTURE_CLEANUP_DONE')


original = worker.WorkerSupervisor.__init__


def install(self, config):
    original(self, config, launch=launch)


worker.WorkerSupervisor.__init__ = install
sys.exit(worker.main())
