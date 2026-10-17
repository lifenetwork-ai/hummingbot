"""Signed direct funding recovery uses original bills and never releases HALT."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, extra_pending=False, portfolio=False):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic test key.
    values, funding, original = await funding_setup(
        database, carry_options=dict(refresh_initial_facts=True),
        spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    c = database[0]
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    if portfolio:
        await c.execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await c.execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await c.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    b = original.bundle
    bad = replace(original, bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('11')))))
    assert (await funding.apply_funding(bad, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    if extra_pending:
        assert (await funding.apply_funding(replace(bad, operation_id='another-pending'),
                                            cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    now = await c.fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
    batch = PgJointBatchRepository(funding.swap)
    command = ResolveJointSources('repair-direct', bad.operation_id,
                                  ApplyJointBatch('batch-repaired', (), (), (), replace(original, operation_id='funding-repaired', bundle=b)))
    account = SpotFinancialRecoveryFacts(cfg.binding, now, D('5'), D('9.9'), D('0'), D('0'), (), (), ('SwapWire1',), (), True, True, True)
    opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
    facts = JointRepairFacts(account, (), ('777',), True, opening, at)
    cut = (await financial.observation_cut(cfg.binding)).cut
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgJointRepairApprovalImporter(c, batch, verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, funding, bad, command, facts, cut, approval, importer, keys


@pytest.mark.asyncio
async def test_signed_direct_funding_keeps_original_pending_and_books_bill_once(database):
    values, funding, bad, command, facts, cut, approval, importer, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        raw = await database[0].fetchval('SELECT to_jsonb(f) FROM life_financial.funding_observations f WHERE operation_id=$1', bad.operation_id)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert (await financial.observation_cut(cfg.binding)).cut == cut
        repo = PgJointRepairRepository(importer.batch)
        result = await repo.resolve(command, cut=cut, owner=owner)
        assert result.state == 'REPAIRED' and not result.trading_permission
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert (await importer.lookup(command.operation_id)).replayed
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT to_jsonb(f) FROM life_financial.funding_observations f WHERE operation_id=$1', bad.operation_id) == raw
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert D((await funding.frame(cfg.binding))['gross_debits_usdt']) == D('.1')
        assert len((await funding.frame(cfg.binding))['pending']) == 1  # Raw diagnostic, never deleted.
        assert not (await repo.frame(cfg.binding))['pending']
        assert not await database[0].fetchval('SELECT life_financial.joint_financial_pending($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.funding_pending_unresolved($1,$2,$3,false)', cfg.account_id, bad.operation_id, cut.checkpoint.revision)
        assert not await database[0].fetchval('SELECT life_financial.funding_pending_unresolved($1,$2,NULL,false)', cfg.account_id, bad.operation_id)
        assert not (await values[5]._read('SELECT life_financial.spot_state($1)', (cfg.account_id,)))['paid_funding']['pending']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches') == 1  # No fabricated pending parent.
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_repaired_funding_allows_fresh_native_nav_terminal_and_claim_retirement(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
        PgSharedReleaseRepository,
        ReleaseSharedClaim,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
        ConfirmSwapTerminal,
        PgSwapTerminalRepository,
    )
    values, funding, bad, command, facts, cut, approval, importer, _ = await setup(database, portfolio=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        # Explicit new synthetic account/carry capture; repair grants neither
        # source readiness nor order terminal/release by itself.
        values[4][0] = command.replacement.funding.bundle.account.snapshot
        values[7][0] = command.replacement.funding.bundle
        await refresh(database, values, cash='9.9')
        await PgPortfolioRepository(funding.swap).measure(MeasurePortfolio('nav-after-repair'),
                                                          cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        prepared = await database[0].fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
        terminal = ConfirmSwapTerminal('terminal-after-repair', 'prepare1', '987654321', 'canceled', D('0'), (),
                                       values[4][0].observed_at_ms, prepared, 'okx_joint_reconciled')
        terminals = PgSwapTerminalRepository(funding.swap)
        assert (await terminals.confirm(terminal, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
        release = ReleaseSharedClaim('release-after-repair', 'swap1', terminal.operation_id)
        assert (await PgSharedReleaseRepository(values[5]).release(release, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).released
        assert await values[5].claims(cfg.binding) == ()
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert await database[0].fetchval('SELECT life_financial.joint_repair_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.swap_terminal_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['changed_amount', 'changed_time', 'removed_bill', 'unapproved', 'wrong_cash'])
async def test_direct_repair_cannot_change_original_bills_or_invent_cash(database, fault):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = command.replacement.funding.bundle
        if fault in ('changed_amount', 'changed_time'):
            payment = b.funding.payments[0]
            payment = replace(payment, signed_payment_quote=D('.1')) if fault == 'changed_amount' else replace(payment, settled_at_ms=payment.settled_at_ms - 1)
            b = replace(b, funding=replace(b.funding, payments=(payment,)))
        elif fault == 'removed_bill':
            b = replace(b, account=replace(b.account, included_funding_bill_ids=()), funding=replace(b.funding, payments=()))
            facts = replace(facts, funding_bill_ids=())
        elif fault == 'wrong_cash':
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('11'))))
            facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('11')))
        command = replace(command, replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        if fault in ('changed_amount', 'changed_time', 'removed_bill'):
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID'):
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
        else:
            if fault != 'unapproved':
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
            reason = 'PG_JOINT_REPAIR_APPROVAL_REQUIRED' if fault == 'unapproved' else 'PG_JOINT_REPAIR_REPLACEMENT_REJECTED'
            with pytest.raises(StorageError, match=reason):
                await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_staging') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('alias_approval', [False, True])
async def test_direct_repair_cannot_bypass_another_direct_pending_operation(database, alias_approval):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        if alias_approval:
            # An independently signed pending parent can share a replacement
            # body. It is still NOT the capability selected by this root.
            second = replace(command, operation_id='other-approval', pending_operation_id='another-pending')
            signed = keys.sign('operator', cfg.binding, 'RECOVER', second.json(), cut, facts,
                               issued_at=approval.issued_at, expires_at=approval.expires_at)
            await importer.approve(second, approval=signed, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT life_financial.joint_financial_pending($1)', cfg.account_id)
        assert (await funding.lookup('another-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_later_paid_funding_keeps_gross_debits_and_original_repair_receipts(database):
    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    values, funding, bad, command, facts, cut, approval, importer, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, included_funding_bill_ids=('777', '888'),
                                       snapshot=replace(b.account.snapshot, sequence=4, observed_at_ms=at, spot_cash_quote=D('10'), collateral_quote=D('10'))),
                    mark=replace(b.mark, sequence=4, observed_at_ms=at), index=replace(b.index, sequence=4, observed_at_ms=at),
                    funding=replace(b.funding, sequence=4, observed_at_ms=at, coverage_through_ms=at,
                                    payments=(*b.funding.payments, FundingPayment('888', at, D('.1')))),
                    terms=replace(b.terms, sequence=4, observed_at_ms=at))
        incoming = replace(command.replacement.funding, operation_id='later-funding', bundle=b)
        result = await funding.apply_funding(incoming, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED' and result.cash_delta_usdt == D('.1') and result.gross_debits_usdt == D('.1')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert len((await funding.frame(cfg.binding))['payments']) == 2
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_expiring_direct_repair_at_physical_commit_books_nothing(database, monkeypatch, stage):
    import asyncio
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    now = await database[0].fetchval('SELECT clock_timestamp()')
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=2))
    target = importer if stage == 'approval' else importer.runtime
    original = target._commit
    try:
        if stage == 'application':
            await importer.approve(command, approval=approval, cut=cut, facts=facts)

        async def expired(tx):
            at = await tx._connection.fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (approval.expires_at - at).total_seconds()) + .05)
            await original(tx)

        monkeypatch.setattr(target, '_commit', expired)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_EXPIRED_AT_COMMIT'):
            if stage == 'approval':
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
            else:
                await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_staging') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['original', 'resolution'])
async def test_corrupt_original_or_resolution_cannot_suppress_funding_pending(database, target):
    values, funding, bad, command, facts, cut, approval, importer, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        table = 'funding_observations' if target == 'original' else 'operations'
        c = database[0]
        # Synthetic privileged damage, outside the trusted-owner contract.
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'original':
            await c.execute("UPDATE life_financial.funding_observations SET bundle=jsonb_set(bundle,'{funding,payments,0,signed_payment_quote}','\"1\"') WHERE operation_id=$1", bad.operation_id)
        else:
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{pending_operation_id}','\"another\"') WHERE operation_id=$1", command.operation_id)
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('9.9')
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_direct_funding_process_crash_recovers_original_repair_identity(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, funding, bad, command, facts, cut, approval, importer, _ = await setup(database)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await exit_joint_repair(cfg, command, cut, owner, committed)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        recovered = PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)))
        found = await recovered.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        if committed:
            assert found.replayed and not found.trading_permission
            assert (await recovered.resolve(command, cut=cut, owner=owner)).replayed
        else:
            assert (await recovered.resolve(command, cut=cut, owner=owner)).state == 'REPAIRED'
        assert (await PgFundingRepository(recovered.batch.swap).lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        await financial.close()
        if cold is not None:
            await cold.close()
