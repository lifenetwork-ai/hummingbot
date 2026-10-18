"""Retired core File transfers + funding retain their original accounting basis."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import PgLegacyApplyingImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


async def restore(cfg, owner, connection):
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    capital = PgSharedCapitalRepository(financial, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
    batch = PgJointBatchRepository(PgSwapRepository(capital))
    importer = PgLegacyApplyingImporter(connection, batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
    return financial, capital, batch, importer


@pytest.mark.asyncio
@pytest.mark.parametrize('retained_only', [False, True])
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
@pytest.mark.parametrize('currency,amount,price', [('USDT', D('2'), D('1')), ('USDT', D('-2'), D('1')),
                                                   ('LIFE', D('2'), D('1.2')), ('LIFE', D('-2'), D('1.2'))])
async def test_core_transfer_and_funding_import_is_neutral_then_cold_recovery_applies_original_facts_once(
        database, tmp_path, monkeypatch, stage, currency, amount, price, retained_only):
    expected_file = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, cashflow=(currency, amount, price),
        file_result=expected_file, retained_cashflows_only=retained_only)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        holds = await values[5].claims(cfg.binding)
        budget = await financial.budget(cfg.binding)
        file_before = (await store.snapshot(store.binding)).balance
        assert source.scope == 'CORE_JOINT_CASH_APPLYING'
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await financial.balance(cfg.binding)).total_base_life == 5
        for table in ('cashflows', 'funding_cash_payments'):
            assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table} WHERE account_id=$1', cfg.account_id) == 0
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        b = command.batch.funding.bundle
        now = await c.fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                    mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                    funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
        replacement = replace(command.batch, operation_id='core-applied', funding=replace(command.batch.funding, operation_id='core-funding-applied', bundle=b))
        repair = ResolveJointSources('core-recover', command.operation_id, replacement)
        opened = int(store._accounting_policy.opened_at.timestamp() * 1000)
        proof = JointRepairFacts(replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1',)), (), ('777',), True, opened, at)
        repair_cut = (await cold.observation_cut(cfg.binding)).cut
        signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(c, batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(
            repair, approval=signed, cut=repair_cut, facts=proof)
        repository = PgJointRepairRepository(batch)
        receipt = await repository.resolve(repair, cut=repair_cut, owner=owner)
        assert receipt.state == 'REPAIRED' and not receipt.trading_permission
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        wallet = await cold.balance(cfg.binding)
        assert wallet == expected_file['balance']
        assert await cold.budget(cfg.binding) == expected_file['budget']
        assert wallet.total_base_life == facts.account_base_life == D('5') + (amount if currency == 'LIFE' else D('0'))
        assert wallet.total_quote_usdt == facts.account_quote_usdt == D('9.9') + (amount if currency == 'USDT' else D('0'))
        row = await c.fetchrow('SELECT a.*,f.independent_price FROM life_financial.cashflows f JOIN life_financial.bill_approvals a USING(account_id,bill_id) WHERE f.account_id=$1', cfg.account_id)
        assert (row['bill_id'], row['asset'], row['signed_amount'], row['independent_price']) == ('101', currency, amount, price)
        assert int(row['transfer_at'].timestamp() * 1000) == command.batch.cashflows[0].transfer_at_ms
        assert await c.fetchval('SELECT count(*) FROM life_financial.cashflows WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval("SELECT gross_debits_usdt FROM life_financial.funding_observations WHERE account_id=$1 AND state='APPLIED'", cfg.account_id) == D('.1')
        assert await c.fetchval('SELECT count(*) FROM life_financial.budget_events WHERE account_id=$1', cfg.account_id) == 0
        assert await cold.budget(cfg.binding) == budget
        assert await capital.claims(cfg.binding) == holds
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert (await store.snapshot(store.binding)).balance == file_before
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('retained_only', [False, True])
@pytest.mark.parametrize('fault', ['bill', 'amount', 'currency', 'price', 'time', 'value_time', 'reference_time', 'receipt_time', 'missing', 'extra', 'scope', 'unsigned_native_bill'])
async def test_new_target_signature_cannot_change_original_core_transfer_or_supply_its_approval(database, tmp_path, monkeypatch, fault, retained_only):
    values, batch, command, cut, facts, _, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, cashflow=('LIFE', D('2'), D('1.2')), retained_cashflows_only=retained_only)
    financial, cfg, _ = values[:3]
    try:
        flow = command.batch.cashflows[0]
        changed = dict(bill=dict(bill_id='102'), amount=dict(signed_amount=D('3')),
                       currency=dict(currency='USDT', independent_price_usdt=D('1'), source_kind='quote_unit'),
                       price=dict(independent_price_usdt=D('2')), time=dict(transfer_at_ms=flow.transfer_at_ms + 1),
                       value_time=dict(value_at_ms=flow.value_at_ms - 1), receipt_time=dict(observed_at=flow.observed_at + timedelta(microseconds=1)),
                       reference_time=dict(reference_observed_at_ms=flow.reference_observed_at_ms + 1))
        flows = (replace(flow, **changed[fault]),) if fault in changed else command.batch.cashflows
        if fault == 'missing':
            flows = ()
        elif fault == 'extra':
            flows += (replace(flow, operation_id='extra-flow', bill_id='102'),)
        elif fault == 'scope':
            facts = replace(facts, account_cashflow_ids=())
        elif fault == 'unsigned_native_bill':
            await database[0].execute('ALTER TABLE life_financial.bill_approvals DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.bill_approvals SET signature=''::bytea WHERE account_id=$1", cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.bill_approvals ENABLE TRIGGER USER')
        command = replace(command, batch=replace(command.batch, cashflows=flows))
        cut = (await financial.observation_cut(cfg.binding)).cut
        now = await database[0].fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        signed = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        before = await financial.balance(cfg.binding)
        reason = 'PG_LEGACY_APPLYING_ORIGINAL_CASHFLOW_BASIS_REQUIRED' if fault == 'unsigned_native_bill' else 'PG_LEGACY_APPLYING_FACTS_CHANGED'
        with pytest.raises(StorageError, match=reason):
            await importer.import_pending(command, source_store=store, approval=signed, cut=cut, facts=facts)
        assert await financial.balance(cfg.binding) == before
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'cashflows', 'funding_cash_payments'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_financial.{table} WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()
