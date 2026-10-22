"""Physical provisional process loss never reconstructs a cold sender."""
import json
import os
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    crash_service,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_spot_backend import spot_money

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capture', 'prepare', 'send', 'ack'])
async def test_provisional_process_death_preserves_birth_money_and_denies_cold_dispatch(database, phase):
    manifest = await crash_service(database, phase, provisional_quotes=True)
    assert manifest['process_id'] != os.getpid()
    context = cold_service_context(manifest)
    cfg, c = context.config, database[0]
    before = await spot_money(c, cfg.account_id)
    birth = await c.fetchrow('SELECT * FROM life_financial.quote_births WHERE account_id=$1', cfg.account_id)
    assert birth is not None
    body = json.loads(birth['body'])
    assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == int(phase != 'capture')
    service = await context.reopen(False)
    try:
        ticket = await PgQuoteBirthRepository(service.financial).read(birth['ticket_id'])
        assert ticket.historical and ticket.body_json == json.dumps(body, sort_keys=True, separators=(',', ':'))
        assert not ticket.trading_permission and not service.journal._entries and not service._owned_executors
        assert not service.runner.executor_orchestrator.active_executors['main']
        action = CreateExecutorAction(controller_id='main', executor_config=OrderExecutorConfig.model_validate_json(ticket.action.payload_json))
        assert not await service.journal.async_authorize_runner_create_action(action)
        assert not service.journal.local_runner_create_fence(action)
        with pytest.raises(StorageError, match='PG_SERVICE_TRADING_NOT_QUALIFIED'):
            await service.quote(ticket.action.intent_id, TradeType.BUY, 0)
        attempts = await c.fetch('SELECT state,wire_id FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id)
        if phase == 'capture':
            assert not attempts
            recovery = PgQuoteBirthRepository(service.financial)
            receipt = await service.retire_unprepared_birth('cold-retire-original', ticket.ticket_id)
            assert receipt.retired and not receipt.trading_permission
            assert service._revoked
            assert (await service.retire_unprepared_birth('cold-retire-original', ticket.ticket_id)).replayed
            with pytest.raises(StorageError, match='PG_OPERATION_CONFLICT'):
                await service.retire_unprepared_birth('cold-retire-original', 'other-ticket')
            assert (await recovery.lookup_retirement('cold-retire-original')).replayed
            assert await spot_money(c, cfg.account_id) == before
            # Metadata retirement neither releases the preallocated shared
            # claim nor activates a new generation after this process death.
            assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
            assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
            assert (await recovery.read(ticket.ticket_id)).body_json == ticket.body_json
        else:
            attempt, = attempts
            assert attempt['state'] == {'prepare': 'PREPARED', 'send': 'SEND_UNKNOWN', 'ack': 'ACKED'}[phase]
        assert await spot_money(c, cfg.account_id) == before
        assert len(manifest['orders']) == int(phase in ('send', 'ack'))
        assert context.calls and all(call['method'] == 'GET' for call in context.calls)
    finally:
        await service.close()
    assert await spot_money(c, cfg.account_id) == before
    assert all(pool._closed for pool in service.runtime._pools.values())
