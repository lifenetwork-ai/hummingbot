"""Original booked derivative identities cannot be adopted from target balances."""
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


async def original(database, tmp_path, monkeypatch, product):
    if product == 'SWAP':
        return await outer_setup(database, tmp_path, monkeypatch, 'wallet_after', entry=D('1.02'), book_native_swap=True)
    return await core_setup(database, tmp_path, monkeypatch, book_native_funding=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SWAP', 'FUNDING'])
async def test_legacy_derivative_prefix_requires_original_economics_hash_receipt_and_operation(database, tmp_path, monkeypatch, product):
    values, _, command, cut, _, _, _, store, _ = await original(database, tmp_path, monkeypatch, product)
    financial, cfg = values[:2]
    c = database[0]
    raw = command.batch.json()
    try:
        async def valid(body):
            return await c.fetchval('SELECT life_financial.legacy_derivative_prefix_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(body), cut.checkpoint.revision)
        assert await valid(raw)
        changes = (dict(trade_id='other', quantity_base_life='0.1', price_usdt='1.1', signed_fee='0',
                        realized_pnl_usdt='1', fill_at_ms=1, independent_price_usdt='2', independent_value_at_ms=1,
                        independent_observed_at_ms=1, source_kind='quote_unit', sequence=1)
                   if product == 'SWAP' else dict(bill_id='778', signed_payment_quote='1', settled_at_ms=1))
        for key, value in changes.items():
            changed = copy.deepcopy(raw)
            leaf = changed['swap_fills'][0]['fills'][0] if product == 'SWAP' else changed['funding']['bundle']['funding']['payments'][0]
            leaf[key] = value
            assert not await valid(changed), key
        if product == 'SWAP':
            for key, value in dict(intent_id='other', exchange_order_id='123', prepare_operation_id='other', client_order_id='other',
                                   session_id='00000000-0000-0000-0000-000000000001', epoch=999, side='BUY', market='BTC-USDT').items():
                changed = copy.deepcopy(raw)
                changed['swap_fills'][0][key] = value
                assert not await valid(changed), key
        changed = copy.deepcopy(raw)
        if product == 'SWAP':
            changed['swap_fills'] = []
        else:
            changed['funding']['bundle']['funding']['payments'] = []
        assert not await valid(changed)
        table = 'swap_fill_facts' if product == 'SWAP' else 'funding_cash_payments'
        operation = 'native-prior-swap' if product == 'SWAP' else 'native-prior-funding'
        statements = [
            f"UPDATE life_financial.{table} SET source_hash=repeat('f',64) WHERE account_id=$1",
            f"UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='{operation}'",
            f"UPDATE life_financial.operations SET command=jsonb_set(command,'{{owner,process_id}}','\"00000000-0000-0000-0000-000000000001\"') WHERE account_id=$1 AND operation_id='{operation}'",
            f"UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{{version}}','0') WHERE account_id=$1 AND operation_id='{operation}'",
            f"UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{{state}}','\"RECOVERY_REQUIRED\"') WHERE account_id=$1 AND operation_id='{operation}'",
            f"DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='{operation}'",
        ]
        if product == 'FUNDING':
            statements.append("UPDATE life_financial.funding_observations SET state='RECOVERY_REQUIRED',reason='SYNTHETIC_PENDING',cash_delta_usdt=0 WHERE account_id=$1")
        for statement in statements:
            tx = c.transaction()
            await tx.start()
            try:
                # Owner-only disposable corruption. System FK triggers remain
                # enabled; the transaction rolls back before deferred checks.
                for name in (table, 'operations', 'funding_observations'):
                    await c.execute(f'ALTER TABLE life_financial.{name} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(raw), statement
            finally:
                await tx.rollback()
        assert await valid(raw)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SWAP', 'FUNDING'])
async def test_legacy_derivative_prefix_corruption_after_import_denies_cold_proof_and_retains_claims(database, tmp_path, monkeypatch, product):
    values, _, command, cut, facts, approval, importer, store, source = await original(database, tmp_path, monkeypatch, product)
    financial, cfg, owner = values[:3]
    c = database[0]
    cold = None
    try:
        held = await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', cfg.account_id)
        table = 'swap_fill_facts' if product == 'SWAP' else 'funding_cash_payments'
        await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await c.execute(f"UPDATE life_financial.{table} SET source_hash=repeat('f',64) WHERE account_id=$1", cfg.account_id)
        await c.execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
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
@pytest.mark.parametrize('database', [59], indirect=True)
@pytest.mark.parametrize('product', ['SWAP', 'FUNDING'])
async def test_actual_59_derivative_prefix_upgrades_without_rewriting_original_money_or_receipt(database, tmp_path, monkeypatch, product):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import migrations, runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import PgSwapFillRepository
    load = runtime.load_migrations
    with monkeypatch.context() as patches:
        patches.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 59))
        values, _, command, cut, facts, approval, _, store, source = await original(database, tmp_path, monkeypatch, product)
    financial, cfg, owner = values[:3]
    c = database[0]
    cold = None
    try:
        body = json.dumps(command.batch.json())
        before = await financial.balance(cfg.binding)
        held = await values[5].claims(cfg.binding)
        operations = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id)
        assert not await c.fetchval('SELECT life_financial.legacy_spot_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, body, cut.checkpoint.revision)
        await financial.close()
        assert (await migrations.migrate(c, action='up'))['applied'] == ['000060_legacy_derivative_prefix.sql', '000061_legacy_parent_prefix.sql']
        assert await c.fetchval('SELECT life_financial.legacy_spot_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, body, cut.checkpoint.revision)
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id) == operations
        cold, capital, batch, importer = await restore(cfg, owner, c)
        repo = PgSwapFillRepository(batch.swap) if product == 'SWAP' else PgFundingRepository(batch.swap)
        operation = 'native-prior-swap' if product == 'SWAP' else 'native-prior-funding'
        result = await repo.lookup(operation, cfg.binding)
        assert result.state == 'APPLIED' and result.replayed and not result.trading_permission
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert (await importer.lookup(command.operation_id)).replayed
        assert await cold.balance(cfg.binding) == before
        assert await capital.claims(cfg.binding) == held
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
