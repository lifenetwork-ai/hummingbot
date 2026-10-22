"""Actual provisional V2 executor, original source and protected SDK boundaries."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_cycle import cancellation_feed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import (
    policy as hedge_policy,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import UpdateHedgeTarget
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


@pytest.mark.asyncio
async def test_provisional_service_requires_imported_policy_without_fallback_or_leaked_runtime(database):
    starting = []
    with pytest.raises(StorageError, match='PG_SERVICE_BIRTH_POLICY_REQUIRED'):
        await configured(database, provisional_quotes=True, omit_financial_policy='birth', _startup_owner=starting)
    service, = starting
    assert service.quote_application is None and getattr(service, 'journal', None) is None
    assert service._close_task.done() and not service.runtime._tasks
    assert all(pool._closed for pool in service.runtime._pools.values())
    for table in ('quote_births', 'quote_actions', 'shared_capital_claims', 'send_attempts'):
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0


@pytest.mark.asyncio
async def test_provisional_spot_to_swap_cycle_settles_both_original_claims_without_new_money(database):
    t, service = await configured(database, provisional_quotes=True, maker_cycle=True,
                                  hedge_policy=replace(hedge_policy(), initial_target_base=D('5')))
    streams = []
    try:
        before = await service.financial.balance(t.cfg.binding)
        balanced = await service.execute_hedge('provisional-initial-balanced')
        assert balanced.child_operation_id is None
        await service.quote('provisional-original-maker', TradeType.BUY, 0)
        await service.hedge.repository.update_target(UpdateHedgeTarget('provisional-new-target', 0, D('0')),
                                                     cut=(await service.financial.snapshot(t.cfg.binding)).cut,
                                                     owner=service.journal.owner)
        stream, canceled = await cancellation_feed(t)
        streams.append(stream)
        receipt = await service.execute_hedge('provisional-after-maker')
        assert receipt.child_operation_id is not None and len(canceled) == 1
        assert not t.connector._order_tracker.active_orders
        assert not service.runner.executor_orchestrator.active_executors['main']
        spot, swap = t.service_orders
        assert spot['state'] == 'canceled' and swap['instType'] == 'SWAP'
        claims = await service.source.capital.claims(t.cfg.binding)
        assert len(claims) == 1 and claims[0].product == 'SWAP'
        swap['state'] = 'canceled'
        now = datetime.now(timezone.utc).timestamp()
        streams.append(asyncio.create_task(t.swap_connector._user_stream_event_listener()))
        t.swap_connector._set_current_timestamp(now)
        await t.swap_connector._user_stream_tracker.user_stream.put(dict(
            arg=dict(channel='orders'), data=[dict(swap, tradeId='', uTime=str(int(now * 1000)))]))
        async with asyncio.timeout(5):
            while swap['clOrdId'] in t.swap_connector._order_tracker.active_orders:
                await asyncio.sleep(.001)
        settled = await service.settle_hedge('provisional-close-once', 'provisional-after-maker')
        assert settled.state == 'TERMINAL'
        assert (await service.settle_hedge('provisional-close-once', 'provisional-after-maker')).replayed
        assert not await service.source.capital.claims(t.cfg.binding)
        after = await service.financial.balance(t.cfg.binding)
        assert (after.total_base_life, after.total_quote_usdt) == (before.total_base_life, before.total_quote_usdt)
        assert after.reserved_base_life == after.reserved_quote_usdt == 0
        assert (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 2
        assert await database[0].fetchval("SELECT bool_and(state='TERMINAL') FROM life_financial.send_attempts")
        assert len([r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]) == 2
        assert service.runtime.healthy
    finally:
        for stream in streams:
            stream.cancel()
        await asyncio.gather(*streams, return_exceptions=True)
        await service.close()
        assert all(pool._closed for pool in service.runtime._pools.values())
        assert not service.runtime._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_provisional_executor_accepts_only_after_atomic_prepare_then_original_sdk_ack(database, monkeypatch, side):
    t, service = await configured(database, provisional_quotes=True)
    original = PgRuntime._commit
    boundaries = []

    async def capture(runtime, tx):
        operation = _current_trace.get().operation
        if operation == 'quote_birth_prepare':
            executor, = service.runner.executor_orchestrator.active_executors['main']
            entry = service.journal._entries[executor.config.id]
            assert not entry.accepted and entry.birth is not None and entry.dispatch_facts is not None
            assert not t.service_orders
            boundaries.append(operation)
        return await original(runtime, tx)
    monkeypatch.setattr(PgRuntime, '_commit', capture)
    try:
        action = await service.quote('provisional-original', side, 0)
        assert boundaries == ['quote_birth_prepare']
        entry = service.journal._entry(action)
        assert entry.accepted and entry.prepared is not None
        assert (await service.financial.attempt(action.executor_config.id, t.cfg.binding)).state.value == 'ACKED'
        assert len(t.service_orders) == 1
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION')") == 0
        ticket = await service.journal.births.read(entry.birth.ticket_id)
        assert ticket.historical and ticket.action == entry.row and ticket.born_at_ms == entry.birth.born_at_ms
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_provisional_executor_cannot_send_after_unknown_compound_commit(database, monkeypatch, committed):
    t, service = await configured(database, provisional_quotes=True)
    original, seen = PgRuntime._commit, []
    admin = await asyncpg.connect(database[1])

    async def lost(runtime, tx):
        trace = _current_trace.get()
        if trace.operation != 'quote_birth_prepare':
            return await original(runtime, tx)
        assert not seen
        executor, = service.runner.executor_orchestrator.active_executors['main']
        assert not service.journal._entries[executor.config.id].accepted and not t.service_orders
        assert await admin.fetchval('SELECT count(*) FROM life_financial.quote_actions') == 0
        if committed:
            await original(runtime, tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', trace.backend_pid)
        seen.append(True)
        raise ConnectionError('synthetic compound response loss')
    monkeypatch.setattr(PgRuntime, '_commit', lost)
    try:
        with pytest.raises(StorageError):
            await service.quote('provisional-original', TradeType.BUY, 0)
        assert seen == [True] and not t.service_orders
        entry = service.journal._entries['provisional-original']
        assert not entry.accepted and entry.revoked
        assert await admin.fetchval('SELECT count(*) FROM life_financial.quote_actions') == committed
        assert await admin.fetchval('SELECT count(*) FROM life_financial.send_attempts') == committed
        assert await admin.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        reserved = await admin.fetchval('SELECT sum(reserved) FROM life_financial.balances')
        assert (reserved > 0) is committed
    finally:
        try:
            await service.close()
        except StorageError as error:
            # No acceptance exists for the deferred initial executor report.
            # Shutdown must surface the retained reporting refusal and still
            # drain every owned pool; this is not a clean-reporting success.
            assert str(error) == 'PG_RECORDER_DEFERRED_BACKPRESSURE'
        finally:
            await admin.close()
        assert all(p._closed for p in service.runtime._pools.values())


@pytest.mark.asyncio
@pytest.mark.timeout(360)  # Same outer watchdog as the full workload invoked below.
async def test_provisional_protocol_runs_original_retained_fill_maker_workload(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import test_postgres_service_load as load
    original = load.configured

    async def selected(*args, **kwargs):
        return await original(*args, provisional_quotes=True, **kwargs)
    monkeypatch.setattr(load, 'configured', selected)
    # Same sources, quotas, one-second lifetime, actual fill, two generations
    # and all original money/history/retirement assertions; only protocol varies.
    await load.test_configured_makers_replenish_after_fill_history_with_competing_shared_sdk_reads(database, monkeypatch)
