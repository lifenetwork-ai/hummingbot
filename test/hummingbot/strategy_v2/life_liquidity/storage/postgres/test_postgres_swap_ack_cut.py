"""Received SWAP ACK survives source-readiness expiry between primary statements."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_original_swap_ack_survives_carry_expiry_after_observation_cut_without_renewing_source(database, monkeypatch):
    values, repo, command = await setup(database, max_age_ms=4000)
    financial, cfg, owner = values[:3]
    try:
        await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        before = await financial.snapshot(cfg.binding)
        claims = await values[5].claims(cfg.binding)
        source = values[7][0]
        original = financial.observation_cut
        cuts = []

        async def cross_original_source_expiry(binding):
            value = await original(binding)
            cuts.append(value)
            if len(cuts) == 1:
                assert await database[0].fetchval('SELECT life_financial.carry_current_ready($1)', cfg.account_id)
                cutoff = min(source.account.snapshot.observed_at_ms, source.mark.observed_at_ms,
                             source.index.observed_at_ms, source.funding.observed_at_ms, source.terms.observed_at_ms) + 4000
                now_ms = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
                assert now_ms < cutoff, ('carry expired before the tested boundary', now_ms, cutoff, value.observed_at)
                await asyncio.sleep(max(0, (cutoff - now_ms) / 1000) + .1)
                after_expiry = await financial.snapshot(binding)
                assert after_expiry.cut.checkpoint.revision == value.cut.checkpoint.revision
                assert after_expiry.cut.checkpoint.state_digest == value.cut.checkpoint.state_digest
                assert after_expiry.recovery_required
                assert not await database[0].fetchval('SELECT life_financial.carry_current_ready($1)', cfg.account_id)
            return value

        monkeypatch.setattr(financial, 'observation_cut', cross_original_source_expiry)
        receipt = await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        assert receipt.state == 'ACKED' and not receipt.trading_permission
        assert len(cuts) == 1
        assert values[7][0] == source
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert after.recovery_required
        assert await values[5].claims(cfg.binding) == claims
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='SWAP_ACK'", cfg.account_id) == 1
        assert (await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)).replayed
        with pytest.raises(StorageError, match='PG_OPERATION_CONFLICT'):
            await repo.acknowledge(command.operation_id, exchange_order_id='987654322', owner=owner)
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
    finally:
        await financial.close()
