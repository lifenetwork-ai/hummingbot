"""One-use operation READ optimization cannot replace a source or replay grant."""
import asyncio
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.operation_preflight import (
    consume_operation_read,
    preflight_new_operation,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['capital', 'carry'])
@pytest.mark.parametrize('fault', ['clone', 'task'])
async def test_operation_read_preflight_is_original_and_bound_to_its_owning_task(database, kind, fault):
    t = await market(database, clean=True)
    repository = getattr(t.source, kind)
    try:
        before = await t.financial.balance(t.cfg.binding)
        ticket = await preflight_new_operation(repository, 'one-read')
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            if fault == 'clone':
                consume_operation_read(repository, 'one-read', replace(ticket))
            else:
                async def foreign():
                    consume_operation_read(repository, 'one-read', ticket)
                await asyncio.create_task(foreign())
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            consume_operation_read(repository, 'one-read', ticket)
        assert await repository.lookup('one-read', t.cfg.binding) is None
        assert await t.financial.balance(t.cfg.binding) == before
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['capital', 'carry'])
async def test_concurrent_native_replay_never_restores_source_authority_after_preflight(database, monkeypatch, kind):
    t = await market(database, clean=True)
    original, repeated = t.financial._runtime._transaction, []
    native_carry = kind == 'carry' and any(m.version == 112 for m in t.financial._runtime._bundle)
    signature = 'shared_capital_command' if kind == 'capital' else 'carry_after_capital' if native_carry else 'carry_check'

    async def replay(query, *args, **kwargs):
        if signature in query and not repeated:
            repeated.append(await original(query, *args, **kwargs))
            # Exercise actual SQL twice. The new handoff refuses the no-longer
            # immediate capital parent; legacy commands return their permanent
            # replay receipt. Neither path may restore local source authority.
            return await original(query, *args, **kwargs)
        return await original(query, *args, **kwargs)
    monkeypatch.setattr(t.financial._runtime, '_transaction', replay)
    try:
        reason = 'CARRY_CAPITAL_HANDOFF_INVALID' if native_carry else 'ORIGINAL_OPERATION_REQUIRES_RECONCILIATION'
        with pytest.raises(StorageError, match=reason):
            await t.app.observe_sources('replayed-source')
        assert len(repeated) == 1
        first = repeated[0][0] if native_carry else repeated[0]
        assert not first.replayed and not first.trading_permission
        if kind == 'carry':
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_checks WHERE account_id=$1', t.cfg.account_id) == 1
        assert t.source._qualified is None and t.source._stopped
        assert not await t.source.capital.claims(t.cfg.binding)
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()
