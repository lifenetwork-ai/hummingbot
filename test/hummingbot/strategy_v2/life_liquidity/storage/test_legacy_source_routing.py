"""Bound outer histories cannot be downgraded to a core-only import capsule."""
from test.hummingbot.strategy_v2.life_liquidity.storage.test_legacy_joint_applying import interrupted, source

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'loss_after', 'receipt', 'cash_done', 'wal_after', 'shared_after'])
async def test_outer_applying_capture_requires_original_coordinator_even_when_core_cash_is_complete(tmp_path, monkeypatch, stage):
    repo, store, recovery, _, owner = await interrupted(tmp_path, monkeypatch, stage)
    try:
        original, _, _ = await source(store, repo, owner)
        before = await store.snapshot(store.binding)
        native = await store._call(store._raw)
        claims = await store._call(recovery.capital.claim_ids)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_ANCILLARY_CAPTURE_REQUIRED'):
            await store.legacy_applying_source(store.binding, facts=original.facts, cut=before.cut)
        assert await store.snapshot(store.binding) == before
        assert await store._call(store._raw) == native
        assert await store._call(recovery.capital.claim_ids) == claims
        assert await store.legacy_applying_source(store.binding, facts=original.facts, cut=before.cut, joint=repo) == original
    finally:
        await store.close()
