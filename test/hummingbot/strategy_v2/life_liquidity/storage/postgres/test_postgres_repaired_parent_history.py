"""Original signed repaired parents remain immutable recovery history."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


def interrupted_parent(database):
    apply = PgJointBatchRepository.apply

    async def interrupted(repo, command, *, cut, owner):
        if command.operation_id != 'native-prior-parent':
            return await apply(repo, command, cut=cut, owner=owner)
        b = command.funding.bundle
        bad = replace(command, operation_id='native-prior-pending', funding=replace(command.funding, operation_id='native-prior-bad-funding',
                      bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=b.account.snapshot.spot_cash_quote + 1)))))
        assert (await apply(repo, bad, cut=cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        c, financial, binding = database[0], repo.financial, repo.binding
        now = await c.fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=b.account.snapshot.sequence + 1, observed_at_ms=at)),
                    mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=at), index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=at),
                    funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=at, coverage_through_ms=at),
                    terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=at))
        replacement = replace(command, funding=replace(command.funding, bundle=b))
        repair = ResolveJointSources('native-prior-repair', bad.operation_id, replacement)
        trades = tuple((product, f.trade_id) for product, groups in (('SPOT', command.spot_fills), ('SWAP', command.swap_fills)) for row in groups for f in row.fills)
        wires = tuple(v['wire_id'] for v in await c.fetch('SELECT wire_id FROM life_financial.send_attempts WHERE account_id=$1 ORDER BY wire_id', repo.runtime.config.account_id))
        account = SpotFinancialRecoveryFacts(binding, now, b.account.snapshot.spot_life_base, b.account.snapshot.spot_cash_quote, D(0), D(0),
                                             tuple(v for _, v in trades), tuple(v.bill_id for v in command.cashflows), wires, (), True, True, True)
        opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        facts = JointRepairFacts(account, trades,
                                 tuple(v.bill_id for v in b.funding.payments), True, opening, at)
        original = (await financial.observation_cut(binding)).cut
        approval = KEYS.sign('operator', binding, 'RECOVER', repair.json(), original, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(c, repo, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(repair, approval=approval, cut=original, facts=facts)
        assert (await PgJointRepairRepository(repo).resolve(repair, cut=original, owner=owner)).state == 'REPAIRED'
        return await repo.lookup(command.operation_id, binding)
    return interrupted


async def setup_repaired(database, tmp_path, monkeypatch, side='BUY', stage='wallet_after', expected=None):
    with monkeypatch.context() as patch:
        patch.setattr(PgJointBatchRepository, 'apply', interrupted_parent(database))
        return await setup(database, tmp_path, monkeypatch, stage, cashflow=('USDT', D('2'), D('1')), spot_fill=(side, 'USDT'),
                           spot_prefix=True, book_native_prefix=True, book_native_accounting_parent=True, file_result=expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
async def test_repaired_original_parent_recovers_only_missing_economics_and_retains_original_pending(database, tmp_path, monkeypatch, side, stage):
    expected = {}
    prepared = await setup_repaired(database, tmp_path, monkeypatch, side, stage, expected)
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', cashflow=('USDT', D('2'), D('1')),
                       spot_prefix=True, book_native_prefix=True, book_native_cashflow=True, native_parent=True, prepared=prepared, expected=expected)


@pytest.mark.asyncio
async def test_repaired_parent_prefix_requires_original_committed_root_at_the_historical_cut(database, tmp_path, monkeypatch):
    import copy
    import json
    values, _, command, cut, _, _, _, store, _ = await setup_repaired(database, tmp_path, monkeypatch)
    financial, cfg = values[:2]
    c = database[0]
    try:
        async def valid(body=command.batch.json(), version=cut.checkpoint.revision):
            return await c.fetchval('SELECT life_financial.legacy_parent_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(body), version)
        assert await valid()
        pending = await c.fetchval("SELECT committed_version FROM life_financial.operations WHERE account_id=$1 AND operation_id='native-prior-pending'", cfg.account_id)
        assert not await valid(version=pending)  # Future repair is not an old-cut resolution.
        missing = copy.deepcopy(command.batch.json())
        missing['funding']['bundle']['funding']['payments'] = []
        assert not await valid(missing)
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='native-prior-pending'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='native-prior-pending'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='native-prior-repair'"),
            ('joint_repair_facts', "DELETE FROM life_financial.joint_repair_facts WHERE account_id=$1 AND operation_id='native-prior-repair'"),
            ('joint_repair_approvals', "UPDATE life_financial.joint_repair_approvals SET source_hash=repeat('f',64) WHERE account_id=$1 AND operation_id='native-prior-repair'"),
            ('joint_financial_batches', "UPDATE life_financial.joint_financial_batches SET money_after=jsonb_set(money_after,'{quote}','999') WHERE account_id=$1 AND operation_id='native-prior-pending'"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(), statement
            finally:
                await tx.rollback()
        assert await valid()
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cashflow', [('USDT', D('2'), D('1')), ('LIFE', D('-2'), D('1.2'))])
async def test_repaired_original_swap_parent_retains_full_file_parity_and_two_cold_restores(database, tmp_path, monkeypatch, cashflow):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import (
        test_original_native_swap_cashflow_funding_parent_preserves_all_children_during_file_recovery,
    )
    with monkeypatch.context() as patch:
        patch.setattr(PgJointBatchRepository, 'apply', interrupted_parent(database))
        await test_original_native_swap_cashflow_funding_parent_preserves_all_children_during_file_recovery(database, tmp_path, monkeypatch, cashflow)


@pytest.mark.asyncio
async def test_repaired_parent_root_corruption_denies_cold_import_proof_without_releasing_claims(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, _ = await setup_repaired(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='native-prior-repair'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [63], indirect=True)
async def test_actual_63_signed_repaired_parent_upgrades_and_recovers_original_money_without_rewriting_history(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, expected = runtime.load_migrations, {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(v for v in load(*a, **k) if v.version <= 63))
        prepared = await setup_repaired(database, tmp_path, monkeypatch, 'SELL', 'wallet_after', expected)
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before = await financial.snapshot(cfg.binding)
        rows = await c.fetchval('SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1', cfg.account_id)
        await financial.close()
        assert (await migrate(c, action='up'))['applied'] == ['000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql']
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await c.fetchval('SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1', cfg.account_id) == rows
        updated = list(values)
        updated[0], updated[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'wallet_after', 'SELL', 'USDT',
                           prepared=(tuple(updated), batch, command, cut, facts, approval, importer, store, source), expected=expected,
                           book_native_prefix=True, book_native_cashflow=True, native_parent=True)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
