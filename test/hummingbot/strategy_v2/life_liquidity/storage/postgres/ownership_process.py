"""Owned disposable DB process; IPC contains public registration/receipts only."""
import asyncio

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease, ProcessCapability
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick


def run(config, owner, pipe, accepted_label='unsafe-write'):
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
                pipe.send((accepted_label, None))
        finally:
            await financial.close()
    try:
        asyncio.run(exercise())
    finally:
        pipe.close()


def run_recovery(config, owner, pipe):
    """Replacement creates its own nondurable capability after OS spawn."""
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.recovery_actor import PgRecoveryActor

    async def exercise():
        async def register(public):
            pipe.send(('registration', public))
            assert await asyncio.to_thread(pipe.poll, 10), 'bounded recovery registration wait'
            assert pipe.recv() == 'registered'
        actor = await PgRecoveryActor.open(config, owner=owner, claim_operation_id='spawned-recovery',
                                           heartbeat_interval_ms=250, register=register)
        try:
            receipt = await actor.lease.lookup('spawned-recovery')
            pipe.send(('ready', receipt.lease_epoch, actor.source_owner, actor.mode))
            while await asyncio.to_thread(pipe.poll, 10):
                command = pipe.recv()
                if command is None:
                    return
                await actor.apply_received(command)
                pipe.send(('applied', (await actor.financial.attempt(command.intent_id, config.binding)).state.value))
        finally:
            await actor.close()
    try:
        asyncio.run(exercise())
    finally:
        pipe.close()
