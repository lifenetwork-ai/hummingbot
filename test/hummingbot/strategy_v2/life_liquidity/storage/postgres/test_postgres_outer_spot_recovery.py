"""Actual coupled SPOT/SWAP/funding crash, File parity and native cold recovery."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('fee_currency', ['USDT', 'LIFE'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'receipt', 'cash_done', 'wal_after', 'shared_after'])
async def test_original_outer_spot_swap_funding_recovers_exact_file_money_without_releasing_claims(database, tmp_path, monkeypatch, side, stage, fee_currency):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, spot_side=side, spot_fee_currency=fee_currency, funding=D('-.1'), file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert len(claims) == 2
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        receipt, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert receipt.state == 'REPAIRED' and not receipt.trading_permission
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_mixed_outer_recovery_requires_exact_original_product_claim_and_spot_preparation(database, tmp_path, monkeypatch):
    import copy
    import json
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='SELL', funding=D('-.1'))
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis = joint_profile(source, command)

        async def valid(body=basis):
            return await c.fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(body), cut.checkpoint.revision)
        assert await valid()
        for fault in ('missing_claim', 'duplicate_claim', 'wire', 'side', 'quantity', 'preparation', 'missing_spot_preparation'):
            changed = copy.deepcopy(basis)
            selected = next(r for r in changed['claims'] if r['claim']['product'] == 'SPOT')
            if fault == 'missing_claim':
                changed['claims'].remove(selected)
            elif fault == 'duplicate_claim':
                changed['claims'].append(selected)
            elif fault == 'missing_spot_preparation':
                changed['spot_preparations'] = []
            elif fault == 'preparation':
                selected['prepare_operation_id'] = 'original-prepare'  # Actual SWAP ID cannot stand for SPOT.
            else:
                selected['claim'][{'wire': 'wire_id', 'side': 'side', 'quantity': 'quantity_base'}[fault]] = {
                    'wire': 'foreignwire', 'side': 'BUY', 'quantity': '3'}[fault]
            assert not await valid(changed), fault
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='spot-prepare'"),
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='arm:spot-prepare'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='spot-prepare'"),
            ('operations', "UPDATE life_financial.operations SET committed_version=999999 WHERE account_id=$1 AND operation_id='original-spot-claim'"),
            ('operations', "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}',to_jsonb('other'::text)) WHERE account_id=$1 AND operation_id='arm:spot-prepare'"),
            ('spot_preparations', "UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{client_order_id}',to_jsonb('foreignwire'::text)) WHERE account_id=$1"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid()
            finally:
                await tx.rollback()
        for key, value in (('signed_fee', D('-.03')), ('quantity_base_life', D('1.1')), ('price_usdt', D('1.1')), ('trade_id', 'invented')):
            group = command.batch.spot_fills[0]
            leaf = replace(group.fills[0], **{key: value})
            altered = replace(command, batch=replace(command.batch, spot_fills=(replace(group, fills=(leaf,), cumulative_base_life=leaf.quantity_base_life),)))
            with pytest.raises(StorageError):
                joint_profile(source, altered)
        # A product switch cannot reinterpret SPOT principal as derivative PnL.
        with pytest.raises(StorageError):
            joint_profile(source, replace(command, batch=replace(command.batch, spot_fills=())))
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_mixed_outer_original_claim_corruption_revokes_cold_import_and_retains_every_hold(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, 'cash_done', spot_side='BUY', funding=D('-.1'))
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='original-spot-claim'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError):
            await importer.lookup(command.operation_id)
        # Read physical claim rows: corrupt provenance deliberately refuses the
        # public claims port; the failure must not remove any held claim.
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=$1', cfg.account_id) == len(claims)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [68], indirect=True)
async def test_actual_68_outer_spot_upgrade_recovers_original_mixed_money_and_claims(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, expected = runtime.load_migrations, {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 68))
        prepared = await setup(database, tmp_path, monkeypatch, 'cash_done', spot_side='SELL',
                               spot_fee_currency='LIFE', funding=D('-.1'), file_result=expected)
    values, _, command, cut, facts, approval, _, store, _ = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await financial.close()
        assert (await migrate(c, action='up'))['applied'] == ['000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql']
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('outer', ['wallet_after', 'cash_done'])
@pytest.mark.parametrize('terminal_stage', ['observation', 'principal', 'wal', 'receipt'])
async def test_original_pending_terminal_composes_with_outer_money_without_repeating_release(database, tmp_path, monkeypatch, side, outer, terminal_stage):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, outer, spot_side=side, funding=D('-.1'), file_result=expected,
        spot_terminal_stage=terminal_stage)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        original = await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-terminal'", cfg.account_id)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-terminal'", cfg.account_id) == original
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('outer', ['wallet_after', 'cash_done'])
@pytest.mark.parametrize('settlement_stage', ['before', 'after', 'receipt'])
async def test_original_pending_inventory_cycle_composes_with_outer_cash_without_cost_refund(database, tmp_path, monkeypatch, side, outer, settlement_stage):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, outer, spot_side=side, funding=D('-.1'), file_result=expected,
        spot_settlement_stage=settlement_stage)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        original = await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-cycle'", cfg.account_id)
        assert len(claims) == 3 and before.budget.session_execution_loss_usdt == D('.04')
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-cycle'", cfg.account_id) == original
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['terminal', 'cycle'])
async def test_interrupted_file_release_requires_independent_original_native_completion(database, tmp_path, monkeypatch, kind):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    options = dict(spot_terminal_stage='principal', native_terminal=False) if kind == 'terminal' else dict(
        spot_settlement_stage='before', native_settlement=False)
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), **options)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.cut, after.balance, after.budget) == (before.cut, before.balance, before.budget)
        assert await values[5].claims(cfg.binding) == claims
        assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
