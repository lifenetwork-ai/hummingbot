"""Disposable PG6 process killed after restore DDL, before physical COMMIT."""
import asyncio
import json
import os
import sys
from uuid import UUID

import asyncpg

from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import BackupLimits, DatabaseImage
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine, RestoreRequest


async def main():
    c = await asyncpg.connect(os.environ['LIFE_PG_RESTORE_TEST_DSN'])
    try:
        print('CONNECTED', flush=True)
        command = json.loads(await asyncio.to_thread(sys.stdin.readline))
        manifest = DatabaseImage.from_json(command['image'])
        request = RestoreRequest(**{**command['request'], 'source_lineage': UUID(command['request']['source_lineage']),
                                    'target_lineage': UUID(command['request']['target_lineage'])})
        restore = PgRestoreQuarantine(c, limits=BackupLimits(**command['limits']))

        async def stopped(tx):
            print('BEFORE_COMMIT', flush=True)
            await asyncio.Future()
        restore._commit = stopped
        await restore.quarantine(request, manifest=manifest)
    finally:
        await c.close()


if __name__ == '__main__':
    asyncio.run(main())
