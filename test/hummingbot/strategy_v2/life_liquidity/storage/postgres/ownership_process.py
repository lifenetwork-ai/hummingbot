"""Owned disposable DB process; IPC contains public registration/receipts only."""
import asyncio

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease, ProcessCapability
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick


def run(config, owner, pipe):
    async def exercise():
        cap = ProcessCapability.create(config, owner)
        pipe.send(('registration', cap.registration))
        await asyncio.to_thread(pipe.recv)
        financial = PgSpotRepository(await PgRuntime.open(config))
        try:
            lease = PgProcessLease(financial._runtime, cap)
            try:
                receipt = await lease.claim('process-claim')
            except StorageError as error:
                pipe.send(('claim-denied', str(error)))
                return
            pipe.send(('claimed', receipt))
            await asyncio.to_thread(pipe.recv)
            before = await financial.snapshot(config.binding)
            tick = SessionTick('old-process-late-write', str(owner.session_id), owner.epoch, False, False, False, 1000)
            try:
                await financial.tick_session(tick, cut=before.cut, owner=owner)
            except StorageError as error:
                pipe.send(('write-denied', str(error)))
            else:
                pipe.send(('unsafe-write', None))
        finally:
            await financial.close()
    try:
        asyncio.run(exercise())
    finally:
        pipe.close()
