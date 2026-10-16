"""Account serialization, immutable operation readback and physical COMMIT guards."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance


@pytest.mark.asyncio
async def test_concurrent_allocators_cannot_double_spend_and_exact_replay_retains_identity(database):
    store, config, owner = await backend(database)
    try:
        results = await asyncio.gather(*(store.allocate(AllocateBalance('allocate' + str(i), 0, 'USDT', D('8')), owner) for i in range(2)), return_exceptions=True)
        assert sum(isinstance(r, StorageError) for r in results) == 1
        assert (await store.balances())[1].reserved == D('8')
        winning = next(r for r in results if not isinstance(r, BaseException))
        cmd = AllocateBalance(winning.operation_id, 0, 'USDT', D('8'))
        assert (await store.allocate(cmd, owner)).replayed
        assert (await store.operation(cmd.operation_id)).operation_id == cmd.operation_id
        with pytest.raises(StorageError):
            await store.allocate(replace(cmd, amount=D('7')), owner)
        with pytest.raises(StorageError):
            await store.allocate(AllocateBalance('overdraw', 1, 'USDT', D('3')), owner)
        assert await store.operation('overdraw') is None
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['process_id', 'generation', 'epoch', 'session_id', 'valid_from', 'expires_at'])
async def test_stale_owner_fields_create_no_operation_or_hold(database, field):
    from datetime import timedelta
    store, config, owner = await backend(database)
    value = uuid4() if field in ('process_id', 'generation', 'session_id') else 2 if field == 'epoch' else getattr(owner, field) + timedelta(milliseconds=1)
    try:
        with pytest.raises(StorageError):
            await store.allocate(AllocateBalance('stale', 0, 'USDT', D('1')), replace(owner, **{field: value}))
        assert await store.operation('stale') is None
        assert (await store.balances())[1].reserved == 0
    finally:
        await store.close()
