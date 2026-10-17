"""A committed product result cannot adopt a changed local owner/configuration."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap import setup as swap_setup
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


def change_context(repo, field):
    if field == 'binding':
        repo.binding = replace(repo.binding, config_hash='f' * 64)
    elif field == 'runtime_config':
        repo.runtime.config = replace(repo.runtime.config, account_id=uuid4())
    else:
        repo.owner = replace(repo.owner, generation=uuid4())


def capital(values):
    return PgSharedCapitalRepository(values[0], owner=values[2], observation=lambda: values[4][0], max_rows=64, max_bytes=1048576)


@pytest.mark.asyncio
@pytest.mark.parametrize('step', ['prepare', 'arm', 'ack'])
@pytest.mark.parametrize('field', ['binding', 'owner', 'runtime_config'])
async def test_swap_commit_context_change_requires_original_readback_without_new_identity(database, monkeypatch, step, field):
    values, repo, command = await swap_setup(database)
    financial, cfg, owner = values[:3]
    original = repo.runtime._commit
    try:
        if step != 'prepare':
            await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        if step == 'ack':
            await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        before = await financial.balance(cfg.binding)

        async def changed(tx):
            await original(tx)
            change_context(repo, field)

        monkeypatch.setattr(repo.runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            if step == 'prepare':
                await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
            elif step == 'arm':
                await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
            else:
                await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        assert not financial._runtime.healthy
        financial._runtime.config = cfg  # Restore original read-only test context; admission remains revoked.
        restored = PgSwapRepository(capital(values))
        operation = command.operation_id if step == 'prepare' else repo._step_id(command.operation_id, step)
        found = await restored.lookup(operation, cfg.binding)
        assert found.replayed and not found.trading_permission
        assert found.state == dict(prepare='PREPARED', arm='SEND_UNKNOWN', ack='ACKED')[step]
        assert await financial.balance(cfg.binding) == before
        assert len(await restored.capital.claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['capital', 'carry'])
@pytest.mark.parametrize('field', ['binding', 'owner', 'runtime_config'])
async def test_capital_and_carry_result_after_context_change_is_unknown_until_original_readback(database, monkeypatch, family, field):
    values = await carry_setup(database)
    financial, cfg, owner = values[:3]
    repo = values[5] if family == 'capital' else values[8]
    original = financial._runtime._commit
    try:
        before = await financial.balance(cfg.binding)
        cut = await repo.cut(cfg.binding)

        async def changed(tx):
            await original(tx)
            change_context(repo, field)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            if family == 'capital':
                await repo.reserve(ReserveCapital('receipt-check', native_claim(owner, 'swap1', product='SWAP', side='SELL', amount='1')), cut, owner)
            else:
                await repo.observe('receipt-check', cut=cut, owner=owner)
        assert not financial._runtime.healthy
        financial._runtime.config = cfg  # Restore original read-only test context; admission remains revoked.
        cap = capital(values)
        restored = cap if family == 'capital' else PgCarryRepository(cap, observation=lambda: values[7][0], max_rows=64, max_bytes=1048576)
        found = await restored.lookup('receipt-check', cfg.binding)
        assert found.replayed and not found.trading_permission and found.decision.allowed
        assert await financial.balance(cfg.binding) == before
        assert len(await cap.claims(cfg.binding)) == (1 if family == 'capital' else 0)
    finally:
        await financial.close()
