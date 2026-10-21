"""Actual provisional V2 executor, original source and protected SDK boundaries."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


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
async def test_provisional_protocol_runs_original_retained_fill_maker_workload(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import test_postgres_service_load as load
    original = load.configured

    async def selected(*args, **kwargs):
        return await original(*args, provisional_quotes=True, **kwargs)
    monkeypatch.setattr(load, 'configured', selected)
    # Same sources, quotas, one-second lifetime, actual fill, two generations
    # and all original money/history/retirement assertions; only protocol varies.
    await load.test_configured_makers_replenish_after_fill_history_with_competing_shared_sdk_reads(database, monkeypatch)
