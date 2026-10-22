"""Signed logical owner revocation cannot be crossed by a provisional quote."""
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured
from uuid import uuid4

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['birth', 'prepare'])
async def test_signed_owner_drain_blocks_provisional_send_and_reactivation_over_retained_claim(database, monkeypatch, phase):
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('DRAIN', 'ACTIVATE'))})
    t, service = await configured(database, provisional_quotes=True,
                                  configuration_extra={'operator_verifier': keys.verifier().policy()})
    c = database[0]
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', t.cfg.account_id)
    await c.execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,32,1048576,60000)', t.cfg.account_id, t.cfg.binding.policy_hash)
    control = PgControlRepository(c, service.financial, verifier=keys.verifier())
    original = service.runtime._commit
    seen = []

    async def drained(transaction):
        await original(transaction)
        if _current_trace.get().operation != ('quote_birth_issue' if phase == 'birth' else 'quote_birth_prepare'):
            return
        assert not seen and not t.service_orders
        before = await service.financial.snapshot(t.cfg.binding)
        command = OwnerTransition('drain-provisional', OwnerMode.DRAINING, t.owner)
        receipt = await control.transition_owner(command, **await signed(database, service.financial, t.cfg, keys, command))
        assert receipt.mode is OwnerMode.DRAINING and not receipt.trading_permission
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        seen.append(True)

    monkeypatch.setattr(service.runtime, '_commit', drained)
    cold = None

    async def close_original():
        try:
            await service.close()
        except StorageError as error:
            assert str(error) in ('PG_RECORDER_DEFERRED_BACKPRESSURE', 'RECORDER_APPLICATION_UNAVAILABLE', 'PG_RECORDER_ORIGIN_INVALID')

    try:
        with pytest.raises(StorageError):
            await service.quote('owner-provisional', TradeType.BUY, 0)
        assert seen == [True] and service._revoked and not t.service_orders
        assert await c.fetchval('SELECT mode FROM life_financial.accounts') == 'DRAINING'
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == int(phase == 'prepare')
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await c.fetchval("SELECT count(*) FROM life_financial.operations WHERE command->>'kind'='SPOT_QUOTE_ARM'") == 0
        if phase == 'birth':
            ticket_id = await c.fetchval('SELECT ticket_id FROM life_financial.quote_births')
            result = await PgQuoteBirthRepository(service.financial).retire(
                'drained-retirement', ticket_id, cut=(await service.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
            assert result.retired and not result.trading_permission
            assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        # Join original recorder callbacks before signing the successor's cut.
        # Otherwise a late, valid recorder write can correctly refuse a stale
        # cut before this test reaches the retained-claim activation gate.
        await close_original()
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        control = PgControlRepository(c, cold, verifier=keys.verifier())
        now = await c.fetchval('SELECT clock_timestamp()')
        successor = OwnerToken(uuid4(), uuid4(), t.owner.epoch, t.owner.session_id, now, now + timedelta(seconds=5))
        activate = OwnerTransition('unsafe-successor', OwnerMode.ACTIVE, successor)
        with pytest.raises(StorageError, match='PG_CONTROL_ACTIVATION_UNQUALIFIED'):
            await control.transition_owner(activate, **await signed(database, cold, t.cfg, keys, activate))
        assert await c.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert await c.fetchval('SELECT owner_generation FROM life_financial.accounts') == t.owner.generation
    finally:
        await close_original()
        if cold is not None:
            await cold.close()
        assert all(pool._closed for pool in service.runtime._pools.values())
        assert not service.runtime._tasks
