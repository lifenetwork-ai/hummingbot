"""Original provisional acceptance cannot outlive stop or source ingress."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_spot_backend import spot_money
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'ingress'])
@pytest.mark.parametrize('after_commit', [False, True])
async def test_provisional_revocation_at_prepare_commit_keeps_holds_without_cold_acceptance(
        database, monkeypatch, fault, after_commit):
    t, service = await configured(database, provisional_quotes=True)
    commit = service.runtime._commit
    prepare = service.journal.births.prepare
    captured, boundaries = [], []

    async def saved(ticket, command, facts, **options):
        captured.append((ticket, command, facts))
        return await prepare(ticket, command, facts, **options)

    async def revoked(transaction):
        if _current_trace.get().operation != 'quote_birth_prepare':
            return await commit(transaction)
        assert not boundaries and len(captured) == 1 and not t.service_orders
        entry = service.journal._entries['revoked-provisional']
        assert not entry.accepted and entry.prepared is None and entry.prepare_started
        if after_commit:
            await commit(transaction)
        if fault == 'stop':
            service.revoke()
        else:
            # Same ingress epoch advanced by the actual financial intake
            # barrier. The source must be recaptured, never redated/adopted.
            service.runtime._ingress_epoch += 1
        if not after_commit:
            await commit(transaction)
        boundaries.append(True)

    monkeypatch.setattr(service.journal.births, 'prepare', saved)
    monkeypatch.setattr(service.runtime, '_commit', revoked)
    try:
        with pytest.raises(StorageError):
            await service.quote('revoked-provisional', TradeType.BUY, 0)
        assert boundaries == [True] and service._revoked and not t.service_orders
        ticket, command, facts = captured[0]
        action = CreateExecutorAction(controller_id=ticket.action.controller_id,
                                      executor_config=OrderExecutorConfig.model_validate_json(ticket.action.payload_json))
        assert service.journal._revoked and not service.journal.local_runner_create_fence(action)
        assert not await service.journal.async_authorize_runner_create_action(action)
        c = database[0]
        # Revocation does not cancel a physically committed prepare or infer
        # that its holds can be released. No ARM/send has occurred.
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == 1
        assert await c.fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id=$1", ticket.action.intent_id) == 'PREPARED'
        assert await c.fetchval("SELECT count(*) FROM life_financial.operations WHERE command->>'kind'='SPOT_QUOTE_ARM'") == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await c.fetchval('SELECT sum(reserved) FROM life_financial.balances') > 0
        before = await spot_money(c, t.cfg.account_id)
    finally:
        try:
            await service.close()
        except StorageError as error:
            assert str(error) in ('PG_RECORDER_DEFERRED_BACKPRESSURE', 'RECORDER_APPLICATION_UNAVAILABLE')
        finally:
            if service._close_task is not None:
                try:
                    await service.wait_closed()
                except StorageError as error:
                    assert str(error) in ('PG_RECORDER_DEFERRED_BACKPRESSURE', 'RECORDER_APPLICATION_UNAVAILABLE')
        assert not service.runtime._tasks
        assert all(pool._closed for pool in service.runtime._pools.values())

    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    try:
        cold = PgQuoteBirthRepository(financial)
        historical = await cold.read(ticket.ticket_id)
        assert historical.historical and not historical.trading_permission
        assert historical.action == ticket.action and historical.born_at_ms == ticket.born_at_ms
        assert historical.body_json == ticket.body_json
        for candidate in (historical, ticket):
            # Even an original DTO brought into a new repository cannot
            # recreate the original in-process preparation/acceptance origin.
            with pytest.raises(StorageError, match='PG_BIRTH_ORIGINAL_PREPARATION_REQUIRED'):
                await cold.prepare(candidate, command, facts, cut=(await financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert await spot_money(database[0], t.cfg.account_id) == before
        assert not t.service_orders
    finally:
        await financial.close()
