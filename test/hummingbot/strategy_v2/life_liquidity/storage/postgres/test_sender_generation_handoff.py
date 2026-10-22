"""Retained unsafe lease-only counterexample at the actual SDK HTTP boundary.

The test passes by reproducing the unsafe external effect. It does not qualify
credential exclusion or successor takeover. All HTTP is the local fake session.
"""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_managed_service import managed_service

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgLeaseGrantIssuer, ProcessCapability
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
async def test_retained_red_old_cached_key_can_send_after_final_database_check_and_lease_expiry(database):
    t, service, state = await managed_service(database, lease_ms=2000, heartbeat_ms=100)
    entered, release = asyncio.Event(), asyncio.Event()
    http, task, cold, waiting = t.session.request, None, None, None

    async def paused(**kwargs):
        if kwargs['method'] == 'POST' and kwargs['url'].rsplit('/', 1)[1] == 'order':
            entered.set()
            await release.wait()  # After the actual SDK's final synchronous guard.
        return await http(**kwargs)
    t.session.request = paused
    try:
        await service.arm_process()
        task = asyncio.create_task(service.quote('paused-old-wire', TradeType.BUY, 0))
        waiting = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait((task, waiting), timeout=service._trading_model.quote_schedule_timeout_ms / 1000,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # Preserve the original cause if no POST was attempted.
        assert waiting in done and entered.is_set()
        guard = service.quote_application._guards['paused-old-wire']
        assert guard._consumed
        before = await service.financial.snapshot(t.cfg.binding)
        assert before.balance.reserved_quote_usdt > 0
        assert (await service.financial.attempt('paused-old-wire', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        service.revoke()
        expiry = await database[0].fetchval('SELECT expires_at-clock_timestamp() FROM life_financial.process_leases')
        await asyncio.sleep(max(0, expiry.total_seconds()) + .03)
        assert not service.runtime.lease_valid and service.process_mode == 'STOPPED'
        # A replacement with identical public owner/config cannot turn a row,
        # cached grant or expired head into a new generation's permission.
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        issuer = PgLeaseGrantIssuer(PgControlRepository(database[0], cold, verifier=state.keys.verifier()))
        replacement = ProcessCapability.create(t.cfg, t.owner)
        with pytest.raises(StorageError, match='HANDOFF_REQUIRED'):
            await issuer.register_first_generation(state.grant, replacement.registration)
        assert not cold._runtime.lease_ready
        release.set()
        if waiting is not None:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        with pytest.raises(StorageError):
            await task
        # Unsafe reproduction: the cached old key still reaches the exchange
        # despite local/DB fencing. This is why external exclusion is required.
        assert len(t.service_orders) == 1
        assert (await service.financial.attempt('paused-old-wire', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.process_lease_commands WHERE lease_epoch>1)')
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        # Expired lease deliberately denies late reporting/ACK publication.
        # The owned cleanup must still close all resources and retain that
        # failure; no missing manifest/unknown attempt is treated as released.
        try:
            await service.close()
        except StorageError:
            assert service._close_task.done() and service.runtime._closing
        if cold is not None:
            await cold.close()
    assert not service.runtime._tasks and service._process.task.done()
