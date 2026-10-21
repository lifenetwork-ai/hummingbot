"""Configured original-ID readback must not cancel a later standing maker."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import policy
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_configured_hedge_replay_does_not_cancel_a_new_standing_maker(database, monkeypatch):
    t, service = await configured(database, hedge_policy=replace(policy(), initial_target_base=D('5')), maker_cycle=True)
    try:
        original = await service.execute_hedge('balanced-original')
        assert original.child_operation_id is None and original.decision['reason_code'] == 'HEDGE_DEADBAND'
        action = await service.quote('standing-after-original', TradeType.BUY, 0)
        executor, = service.runner.executor_orchestrator.active_executors['main']
        wire = service.quote_source.wire_for_action(action)
        sdk_order = t.connector._order_tracker.active_orders[wire]
        before = await service.financial.snapshot(t.cfg.binding)
        claims = await service.source.capital.claims(t.cfg.binding)
        posts = [r for r in t.requests if r['method'] == 'POST']
        cancellations = []

        async def must_not_cancel(targets):
            cancellations.append(targets)
            raise AssertionError('Hedge readback attempted to cancel a later maker')
        monkeypatch.setattr(service, 'cancel_makers', must_not_cancel)
        result = await service.execute_hedge('balanced-original')
        assert result.replayed and result.operation_id == original.operation_id
        assert result.decision == original.decision
        assert not cancellations
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await service.source.capital.claims(t.cfg.binding) == claims
        assert service.runner.executor_orchestrator.active_executors['main'] == [executor]
        assert executor.is_active and t.connector._order_tracker.active_orders[wire] is sdk_order
        assert [r for r in t.requests if r['method'] == 'POST'] == posts
        assert not service._revoked
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_hedge_readback_reserves_cycle_before_primary_await(database, monkeypatch):
    t, service = await configured(database, hedge_policy=replace(policy(), initial_target_base=D('5')))
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        await service.execute_hedge('old-balanced')
        original = service.hedge.repository.lookup

        async def held(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        monkeypatch.setattr(service.hedge.repository, 'lookup', held)
        before = await service.financial.snapshot(t.cfg.binding)
        count = len(t.requests)
        task = asyncio.create_task(service.execute_hedge('old-balanced'))
        await asyncio.wait_for(entered.wait(), 2)
        assert service._quote_task is task
        with pytest.raises(StorageError, match='PG_SERVICE_QUOTE_BACKPRESSURE'):
            await service.quote('new-while-readback', TradeType.BUY, 0)
        with pytest.raises(StorageError, match='PG_SERVICE_QUOTE_BACKPRESSURE'):
            await service.execute_hedge('another-while-readback')
        with pytest.raises(StorageError, match='PG_SERVICE_QUOTE_BACKPRESSURE'):
            await service.cancel_makers(('not-owned',))
        with pytest.raises(StorageError, match='PG_HEDGE_APPLICATION_ORIGIN_REQUIRED'):
            await service.hedge.execute('direct-while-readback')
        release.set()
        assert (await task).replayed
        assert service._quote_task is service._hedge_task is None
        assert len(t.requests) == count
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not service._revoked
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_hedge_primary_probe_and_new_execution_share_original_five_second_budget(database, monkeypatch):
    t, service = await configured(database, hedge_policy=policy())
    original = service.hedge.repository.lookup
    delegated = asyncio.Event()

    async def slow_primary(*args):
        if args[0] == 'original-cycle-budget':
            await asyncio.sleep(4.5)
        return await original(*args)

    async def unavailable_execution(operation_id):
        delegated.set()
        await asyncio.Event().wait()
    try:
        before = await service.financial.snapshot(t.cfg.binding)
        monkeypatch.setattr(service.hedge.repository, 'lookup', slow_primary)
        monkeypatch.setattr(service.hedge, 'execute', unavailable_execution)
        assert service.limits.cycle_timeout_ms == 5000
        start = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await service.execute_hedge('original-cycle-budget')
        elapsed = asyncio.get_running_loop().time() - start
        assert delegated.is_set() and 4.5 <= elapsed < 5.75
        assert service._quote_task is service._hedge_task is None and service._revoked
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not await service.source.capital.claims(t.cfg.binding)
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await service.close()
