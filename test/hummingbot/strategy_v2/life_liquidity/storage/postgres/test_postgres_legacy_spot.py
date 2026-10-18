"""Actual interrupted SPOT/funding File saga → native original-ID cold recovery."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter


async def recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency, cashflow=None):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, spot_fill=(side, fee_currency), file_result=expected, cashflow=cashflow)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        imported = await financial.snapshot(cfg.binding)
        assert imported.balance == before.balance and imported.budget == before.budget
        assert await c.fetchval('SELECT count(*) FROM life_financial.financial_events WHERE account_id=$1', cfg.account_id) == 0
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        now = await c.fetchval('SELECT clock_timestamp()')
        at, b = int(now.timestamp() * 1000), command.batch.funding.bundle
        fresh = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                        mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                        funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
        replacement = replace(command.batch, operation_id='spot-funding-applied',
                              funding=replace(command.batch.funding, operation_id='spot-funding-paid', bundle=fresh))
        repair = ResolveJointSources('spot-funding-repair', command.operation_id, replacement)
        proof = JointRepairFacts(replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1', 'wire1')), (('SPOT', 'trade1'),), ('777',), True,
                                 int(store._accounting_policy.opened_at.timestamp() * 1000), at)
        repair_cut = (await cold.observation_cut(cfg.binding)).cut
        signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(c, batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(repair, approval=signed, cut=repair_cut, facts=proof)
        assert not (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).trading_permission
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        view = await cold.snapshot(cfg.binding)
        assert view.balance == expected['balance']
        assert view.budget == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval("SELECT gross_debits_usdt FROM life_financial.funding_observations WHERE account_id=$1 AND state='APPLIED'", cfg.account_id) == D('.1')
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=view.cut, owner=owner)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'floor_after', 'receipt'])
@pytest.mark.parametrize('side,fee_currency', [('BUY', 'USDT'), ('BUY', 'LIFE'), ('SELL', 'USDT'), ('SELL', 'LIFE')])
async def test_interrupted_file_spot_funding_recovers_identical_money_holds_and_gross_budgets_after_two_restarts(
        database, tmp_path, monkeypatch, stage, side, fee_currency):
    await recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency)


@pytest.mark.asyncio
@pytest.mark.parametrize('side,fee_currency,cashflow', [
    ('BUY', 'USDT', ('LIFE', D('2'), D('1.2'))),
    ('SELL', 'LIFE', ('USDT', D('-2'), D('1'))),
])
async def test_approved_transfer_spot_fill_and_funding_recover_together_with_full_file_parity(database, tmp_path, monkeypatch, side, fee_currency, cashflow):
    await recover_case(database, tmp_path, monkeypatch, 'wallet_after', side, fee_currency, cashflow)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['price', 'fee', 'fee_currency', 'quantity', 'trade', 'wire', 'session', 'exchange_order', 'time', 'missing', 'native_preparation', 'native_arm', 'native_arm_id'])
async def test_resigned_import_cannot_change_original_spot_or_independent_native_preparation(database, tmp_path, monkeypatch, fault):
    values, _, command, _, facts, _, importer, store, _ = await setup(database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'))
    financial, cfg, _ = values[:3]
    c = database[0]
    try:
        fill = command.batch.spot_fills[0]
        leaf = dict(price=dict(price_usdt=D('1.1')), fee=dict(signed_fee=D('-.01')), fee_currency=dict(fee_currency='LIFE'),
                    quantity=dict(quantity_base_life=D('.5')), trade=dict(trade_id='changed'), time=dict(fill_at_ms=fill.fills[0].fill_at_ms + 1))
        group = dict(wire=dict(client_order_id='changed'), session=dict(session_id='changed'), exchange_order=dict(exchange_order_id='999'))
        if fault in leaf:
            fill = replace(fill, fills=(replace(fill.fills[0], **leaf[fault]),),
                           **({'cumulative_base_life': D('.5')} if fault == 'quantity' else {}))
        elif fault in group:
            fill = replace(fill, **group[fault])
        elif fault == 'native_preparation':
            await c.execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{reference_price_usdt}','\"2\"') WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        elif fault in ('native_arm', 'native_arm_id'):
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute(("UPDATE life_financial.operations SET operation_id='forged-arm'" if fault == 'native_arm_id' else
                             "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}','\"other\"')") +
                            " WHERE account_id=$1 AND operation_id='arm:operation1'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        command = replace(command, batch=replace(command.batch, spot_fills=() if fault == 'missing' else (fill,)))
        now = await c.fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        cut = (await financial.observation_cut(cfg.binding)).cut
        signature = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        reason = 'PG_LEGACY_APPLYING_ORIGINAL_SPOT_BASIS_REQUIRED' if fault.startswith('native_') else 'PG_LEGACY_APPLYING_FACTS_CHANGED'
        with pytest.raises(StorageError, match=reason):
            await importer.import_pending(command, source_store=store, approval=signature, cut=cut, facts=facts)
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'spot_fill_facts', 'funding_cash_payments'):
            assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table} WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['native_preparation', 'native_arm', 'native_arm_id'])
async def test_original_native_basis_corruption_after_import_blocks_cold_source_readback_and_snapshot(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'))
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert await c.fetchval('SELECT life_financial.legacy_spot_verified($1)', cfg.account_id)
        if fault == 'native_preparation':
            await c.execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{reference_price_usdt}','\"2\"') WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        else:
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute(("UPDATE life_financial.operations SET operation_id='forged-arm'" if fault == 'native_arm_id' else
                             "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}','\"other\"')") +
                            " WHERE account_id=$1 AND operation_id='arm:operation1'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        assert not await c.fetchval('SELECT life_financial.legacy_spot_verified($1)', cfg.account_id)
        assert not await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', cfg.account_id)
        await financial.close()
        cold, _, _, importer = await restore(cfg, owner, c)
        assert (await cold.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await c.fetchval('SELECT count(*) FROM life_financial.financial_events WHERE account_id=$1', cfg.account_id) == 0
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
