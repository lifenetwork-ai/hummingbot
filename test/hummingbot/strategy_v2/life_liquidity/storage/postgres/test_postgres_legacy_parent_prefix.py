"""Exact original native parents compose with signed retired File recovery."""
import copy
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import (
    setup as core_setup,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import setup as outer_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def setup_parent(database, tmp_path, monkeypatch, product):
    if product == 'SPOT':
        return await core_setup(database, tmp_path, monkeypatch, cashflow=('USDT', D('2'), D('1')),
                                spot_fill=('BUY', 'USDT'), spot_prefix=True, book_native_prefix=True,
                                book_native_cashflow=True, prior_funding=(D('-.03'), D('.02')),
                                prior_after_accounting=True, book_native_accounting_parent=True)
    return await outer_setup(database, tmp_path, monkeypatch, 'wallet_after', entry=D('1.02'), funding=D('-.1'),
                             cashflow=('USDT', D('2'), D('1')), cashflow_written=True, book_native_parent=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_original_parent_proof_refuses_changed_leaves_members_receipts_and_orphans(database, tmp_path, monkeypatch, product):
    values, _, command, cut, _, _, _, store, _ = await setup_parent(database, tmp_path, monkeypatch, product)
    financial, cfg = values[:2]
    c = database[0]
    raw = command.batch.json()
    try:
        async def valid(body):
            return await c.fetchval('SELECT life_financial.legacy_parent_prefix_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(body), cut.checkpoint.revision)
        assert await valid(raw)
        for fault in ('cashflow', 'fill', 'missing_fill', 'fill_identity', 'funding', 'funding_time', 'missing_funding'):
            changed = copy.deepcopy(raw)
            group = changed['spot_fills' if product == 'SPOT' else 'swap_fills'][0]
            if fault == 'cashflow':
                changed['cashflows'][0]['signed_amount'] = '1'
            elif fault == 'fill':
                group['fills'][0]['price_usdt'] = '2'
            elif fault == 'missing_fill':
                group['fills'] = []
            elif fault == 'fill_identity':
                group['client_order_id'] = 'other'
            elif fault == 'funding':
                changed['funding']['bundle']['funding']['payments'][0]['signed_payment_quote'] = '1'
            elif fault == 'funding_time':
                changed['funding']['bundle']['funding']['payments'][0]['settled_at_ms'] += 1
            elif fault == 'missing_funding':
                changed['funding']['bundle']['funding']['payments'] = []
            assert not await valid(changed), fault
        for statement in (
            "UPDATE life_financial.joint_financial_batches SET state='RECOVERY_REQUIRED',reason='SYNTHETIC_PENDING' WHERE account_id=$1",
            "UPDATE life_financial.joint_financial_batches SET members=jsonb_set(members,'{0,receipt,state}','\"BROKEN\"') WHERE account_id=$1",
            "UPDATE life_financial.joint_financial_batches SET money_before=jsonb_set(money_before,'{quote}','\"999\"') WHERE account_id=$1",
            "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='native-prior-parent'",
            "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='native-prior-transfer'",
            "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='native-prior-parent'",
            "DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='native-prior-parent'",
            "DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='native-prior-transfer'",
        ):
            tx = c.transaction()
            await tx.start()
            try:
                for table in ('joint_financial_batches', 'operations'):
                    await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(raw), statement
            finally:
                await tx.rollback()
        assert await valid(raw)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_original_parent_corruption_after_import_denies_cold_source_and_retains_claims(database, tmp_path, monkeypatch, product):
    values, _, command, cut, facts, approval, importer, store, source = await setup_parent(database, tmp_path, monkeypatch, product)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        held = await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', cfg.account_id)
        await c.execute('ALTER TABLE life_financial.joint_financial_batches DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.joint_financial_batches SET members=jsonb_set(members,'{0,receipt,state}','\"BROKEN\"') WHERE account_id=$1 AND operation_id='native-prior-parent'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.joint_financial_batches ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert (await cold.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == held
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [60], indirect=True)
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_actual_60_parent_history_upgrades_then_imports_without_rewriting_native_money(database, tmp_path, monkeypatch, product):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import migrations, runtime
    load = runtime.load_migrations
    with monkeypatch.context() as patches:
        patches.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 60))
        values, _, command, cut, facts, approval, _, store, source = await setup_parent(database, tmp_path, monkeypatch, product)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        original = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id)
        before, held = await financial.balance(cfg.binding), await values[5].claims(cfg.binding)
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure)")
        assert 'OR EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account)' in definition
        await financial.close()
        assert (await migrations.migrate(c, action='up'))['applied'] == ['000061_legacy_parent_prefix.sql', '000062_legacy_unsent_spot.sql', '000063_legacy_spot_ack_prefix.sql', '000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql']
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id) == original
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await batch.lookup('native-prior-parent', cfg.binding)).state == 'APPLIED'
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.lookup(command.operation_id)).replayed
        assert (await batch.lookup('native-prior-parent', cfg.binding)).state == 'APPLIED'
        assert await cold.balance(cfg.binding) == before
        assert await capital.claims(cfg.binding) == held
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
