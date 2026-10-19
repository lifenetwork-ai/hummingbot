"""Zero-fill SWAP orders keep independently admitted originals during recovery."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'receipt', 'cash_done'])
@pytest.mark.parametrize('funding', [D('-.1'), D('.1')])
async def test_unfilled_original_swap_books_funding_once_without_fabricating_trade_ack_or_release(database, tmp_path, monkeypatch, stage, funding):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, funding=funding, swap_filled=False, file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        original = await database[0].fetch('SELECT operation_id,command,fingerprint,receipt FROM life_financial.operations WHERE account_id=$1 ORDER BY operation_id', cfg.account_id)
        claims, before = await values[5].claims(cfg.binding), await financial.snapshot(cfg.binding)
        assert command.batch.swap_fills == () and source.facts.account_trade_ids == ()
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        basis = joint_profile(source, command)
        assert await database[0].fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2,$3)', cfg.account_id, json.dumps(basis), cut.checkpoint.revision)
        for fault in ('false', 'text', 'prepare', 'product'):
            wrong = json.loads(json.dumps(basis))
            claim = wrong['claims'][0]
            if fault == 'false':
                claim['unfilled'] = False
            elif fault == 'text':
                claim['unfilled'] = 'true'
            elif fault == 'prepare':
                claim['prepare_operation_id'] = 'invented-preparation'
            else:
                claim['claim']['product'] = 'SPOT'
            assert not await database[0].fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2,$3)', cfg.account_id, json.dumps(wrong), cut.checkpoint.revision)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, database[0])
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, database[0])
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        current = {r['operation_id']: r for r in await database[0].fetch('SELECT operation_id,command,fingerprint,receipt FROM life_financial.operations WHERE account_id=$1', cfg.account_id)}
        assert all(current[r['operation_id']] == r for r in original)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_pending_fills WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_ack', 'different_ack'])
async def test_file_unfilled_source_cannot_supply_a_missing_or_different_native_ack(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', funding=D('-.1'), swap_filled=False,
        native_swap_ack=fault != 'missing_ack', native_swap_ack_id='987654322' if fault == 'different_ack' else '987654321')
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert after.cut == before.cut and (after.balance, after.budget) == (before.balance, before.budget)
        assert await values[5].claims(cfg.binding) == claims
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 0
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [False, True])
async def test_unfilled_proof_uses_original_version_and_refuses_later_known_or_pending_fill_at_new_cut(database, tmp_path, monkeypatch, pending):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
        FillSwap,
        PgSwapFillRepository,
        SwapFillObservation,
    )
    values, batch, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', funding=D('-.1'), swap_filled=False)
    financial, cfg, owner = values[:3]
    try:
        basis = json.dumps(joint_profile(source, command))
        assert await database[0].fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2,$3)', cfg.account_id, basis, cut.checkpoint.revision)
        claim = source.joint_state['bundle']['orders'][0]['claim']
        now = await database[0].fetchval('SELECT clock_timestamp()')
        stamp = int(now.timestamp() * 1000)
        leaf = SwapFillObservation('native-later-trade', D('.25'), D('1'), 'USDT', D('-100') if pending else D('0'),
                                   stamp, D('1'), stamp, stamp, 'independent_market', D('0'), None)
        fill = FillSwap('native-later-fill', 'original-prepare', claim['intent_id'], claim['wire_id'], 'LIFE-USDT', claim['side'],
                        claim['session_id'], claim['epoch'], '987654321', D('.25'), (leaf,), now)
        receipt = await PgSwapFillRepository(batch.swap).apply_fills(fill, cut=cut, owner=owner)
        assert receipt.state == ('RECOVERY_REQUIRED' if pending else 'APPLIED')
        after = await financial.snapshot(cfg.binding)
        assert after.cut.checkpoint.revision > cut.checkpoint.revision
        # These are read-only historical diagnostics, never a replacement for
        # a stale approval/cut or a grant to ignore a current received fill.
        assert await database[0].fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2,$3)', cfg.account_id, basis, cut.checkpoint.revision)
        assert not await database[0].fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2,$3)', cfg.account_id, basis, after.cut.checkpoint.revision)
        assert await financial.snapshot(cfg.binding) == after
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
