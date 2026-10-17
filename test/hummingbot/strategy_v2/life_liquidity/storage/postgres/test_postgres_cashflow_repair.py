"""Signed recovery books an original pending external bill exactly once."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
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


async def setup(database, *, funding_pending=False, currency='USDT', credit_later=False, credit_amount='2'):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic key.
    values, funding, payment = await funding_setup(database, amount=credit_amount, carry_options=dict(refresh_initial_facts=True),
                                                   spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    c = database[0]
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    now = await c.fetchval('SELECT clock_timestamp()')
    ms = int(now.timestamp() * 1000)
    amount = D('-11') if currency == 'USDT' else D('1')
    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                    cfg.account_id, '101', currency, amount, ms, b'synthetic-preverified-import')
    blocker = None
    if currency == 'LIFE':
        await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                        cfg.account_id, '102', 'USDT', D('-11'), ms, b'synthetic-preverified-import')
        blocker = flow(now, ms, operation_id='cash-blocker', bill_id='102', signed_amount=D('-11'))
        assert (await financial.apply_cashflow(blocker, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    original = flow(now, ms, operation_id='cash-pending', currency=currency, signed_amount=amount,
                    source_kind='quote_unit' if currency == 'USDT' else 'independent_market')
    assert (await financial.apply_cashflow(original, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    await asyncio.sleep(.005)
    now = await c.fetchval('SELECT clock_timestamp()')
    through = int(now.timestamp() * 1000)
    cash = D('10') + D(credit_amount) - D('11')
    base = D('5') if currency == 'USDT' else D('6')
    b = payment.bundle
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=through,
                                                               spot_cash_quote=cash, collateral_quote=cash, spot_life_base=base)),
                mark=replace(b.mark, sequence=3, observed_at_ms=through), index=replace(b.index, sequence=3, observed_at_ms=through),
                funding=replace(b.funding, sequence=3, observed_at_ms=through, coverage_through_ms=through,
                                payments=(replace(b.funding.payments[0], settled_at_ms=ms + 1 if credit_later else ms - 1),)),
                terms=replace(b.terms, sequence=3, observed_at_ms=through))
    if funding_pending:
        assert (await funding.apply_funding(replace(payment, operation_id='funding-pending', bundle=b),
                                            cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    command = ResolveJointSources('repair-cash', original.operation_id,
                                  ApplyJointBatch('batch-repaired', (), (), (replace(original, operation_id='cash-repaired'),) + ((replace(blocker, operation_id='blocker-repaired'),) if blocker else ()),
                                                  replace(payment, operation_id='funding-repaired', bundle=b)),
                                  (('funding-pending',) if funding_pending else ()) + (('cash-blocker',) if blocker else ()))
    account = SpotFinancialRecoveryFacts(cfg.binding, now, base, cash, D('0'), D('0'), (), ('101', '102') if blocker else ('101',), ('SwapWire1',), (), True, True, True)
    opened = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
    facts = JointRepairFacts(account, (), ('777',), True, opened, through)
    cut = (await financial.observation_cut(cfg.binding)).cut
    signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                       issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgJointRepairApprovalImporter(c, PgJointBatchRepository(funding.swap), verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, funding, original, command, facts, cut, signed, importer, keys


@pytest.mark.asyncio
@pytest.mark.parametrize('currency,funding_pending', [('USDT', False), ('USDT', True), ('LIFE', False)])
async def test_signed_cashflow_pending_books_bill_once_without_overwriting_original(database, currency, funding_pending):
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database, currency=currency, funding_pending=funding_pending)
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        raw = await c.fetchval('SELECT jsonb_agg(to_jsonb(f) ORDER BY bill_id) FROM life_financial.spot_cashflow_facts f')
        budget = (await financial.snapshot(cfg.binding)).budget
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        assert (await repo.resolve(command, cut=cut, owner=owner)).state == 'REPAIRED'
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert await c.fetchval('SELECT jsonb_agg(to_jsonb(f) ORDER BY bill_id) FROM life_financial.spot_cashflow_facts f') == raw
        assert (await financial.lookup_cashflow(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (facts.account.account_base_life, facts.account.account_quote_usdt)
        duplicate = replace(original, operation_id='duplicate-cash')
        assert (await financial.apply_cashflow(duplicate, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert await financial.balance(cfg.binding) == balance
        assert (await financial.snapshot(cfg.binding)).budget == budget
        assert await c.fetchval('SELECT count(*) FROM life_financial.cashflows') == (2 if currency == 'LIFE' else 1)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
        context = await PgSpotObservationReader(financial, max_rows=128, max_bytes=1048576).observation_context(cfg.binding)
        assert len(context.cashflows) == (2 if currency == 'LIFE' else 1)
        assert not (await repo.frame(cfg.binding))['pending']
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'currency', 'bill', 'time', 'price', 'observed', 'omitted', 'extra', 'signature', 'cash'])
async def test_signed_cashflow_repair_cannot_rewrite_original_money_or_scope(database, fault):
    values, _, _, command, facts, cut, signed, importer, keys = await setup(database, currency='LIFE' if fault == 'price' else 'USDT')
    financial, cfg, owner = values[:3]
    try:
        row = command.replacement.cashflows[0]
        changes = dict(amount=dict(signed_amount=D('-10')), currency=dict(currency='LIFE', source_kind='independent_market'), bill=dict(bill_id='102'),
                       time=dict(transfer_at_ms=row.transfer_at_ms - 1), price=dict(independent_price_usdt=D('2')),
                       observed=dict(observed_at=row.observed_at + timedelta(microseconds=1)))
        if fault in changes:
            row = replace(row, **changes[fault])
        replacement = replace(command.replacement, cashflows=(row, *command.replacement.cashflows[1:]))
        if fault == 'omitted':
            replacement = replace(replacement, cashflows=())
        elif fault == 'extra':
            replacement = replace(replacement, cashflows=(row, replace(row, operation_id='extra', bill_id='102')))
        elif fault == 'cash':
            b = replacement.funding.bundle
            replacement = replace(replacement, funding=replace(replacement.funding,
                                                               bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('2'))))))
            facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('2')))
        command = replace(command, replacement=replacement)
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        if fault == 'signature':
            signed = replace(signed, signature='0' * 128)
        if fault == 'cash':
            await importer.approve(command, approval=signed, cut=cut, facts=facts)
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        else:
            with pytest.raises(StorageError, match='(PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID|OPERATOR_APPROVAL)'):
                await importer.approve(command, approval=signed, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('spot_cashflow_applications', 'cashflows', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_future_funding_cannot_finance_original_earlier_withdrawal(database):
    values, _, _, command, facts, cut, signed, importer, _ = await setup(database, credit_later=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_cashflow_applications') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_cashflow_repair_process_exit_and_cold_original_id_retry(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database, funding_pending=True)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        await exit_joint_repair(cfg, command, cut, owner, committed)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        repo = PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)))
        assert (await repo.lookup(command.operation_id, cfg.binding) is not None) is committed
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed is committed
        assert (await cold.lookup_cashflow(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 1
        for table in ('spot_cashflow_applications', 'cashflows', 'funding_cash_payments', 'joint_repair_facts'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['original', 'source_hash', 'receipt', 'application', 'orphan', 'resolution'])
async def test_cashflow_damage_is_unverified_after_signed_resolution(database, target):
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        c = database[0]
        table = ('spot_cashflow_facts' if target in ('original', 'source_hash') else
                 'spot_cashflow_applications' if target in ('application', 'orphan') else 'operations')
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'original':
            await c.execute("UPDATE life_financial.spot_cashflow_facts SET command=jsonb_set(command,'{signed_amount}','\"-10\"')")
        elif target == 'source_hash':
            await c.execute("UPDATE life_financial.spot_cashflow_facts SET source_hash=repeat('0',64)")
        elif target == 'receipt':
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{state}','\"APPLIED\"') WHERE operation_id=$1", original.operation_id)
        elif target == 'application':
            await c.execute("UPDATE life_financial.spot_cashflow_applications SET command=jsonb_set(command,'{independent_price_usdt}','\"2\"')")
        elif target == 'orphan':
            await c.execute('DELETE FROM life_financial.spot_cashflow_applications')
        else:
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{pending_operation_id}','\"other\"') WHERE operation_id=$1", command.operation_id)
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == 1
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [False, True])
async def test_cashflow_repair_needs_all_selected_pending_bills_in_one_transaction(database, selected):
    values, _, _, command, facts, _, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        now = await c.fetchval('SELECT clock_timestamp()')
        ms = int(now.timestamp() * 1000)
        await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                        cfg.account_id, '102', 'USDT', D('2'), ms, b'synthetic-preverified-import')
        other = flow(now, ms, operation_id='another-pending', bill_id='102')
        assert (await financial.apply_cashflow(other, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        if selected:
            b = command.replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('3'), collateral_quote=D('3'))))
            command = replace(command, additional_pending_operation_ids=(other.operation_id,), replacement=replace(command.replacement,
                                                                                                                   cashflows=(*command.replacement.cashflows, replace(other, operation_id='other-repaired')),
                                                                                                                   funding=replace(command.replacement.funding, bundle=b)))
            facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('3'), account_cashflow_ids=('101', '102')))
        cut = (await financial.observation_cut(cfg.binding)).cut
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        if selected:
            await repo.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == 3
            assert not (await repo.frame(cfg.binding))['pending']
            assert await c.fetchval('SELECT count(*) FROM life_financial.spot_cashflow_applications') == 2
        else:
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await repo.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
            assert await c.fetchval('SELECT count(*) FROM life_financial.spot_cashflow_applications') == 0
        assert (await financial.lookup_cashflow(other.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_cashflow_spot_swap_and_funding_repair_share_one_exact_application(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_repair import (
        setup as spot_setup,
    )
    values, _, _, command, facts, _, signed, importer, keys = await spot_setup(database, mixed=True, swap_pending=True, credit_amount='3')
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        now = await c.fetchval('SELECT clock_timestamp()')
        ms = int(now.timestamp() * 1000)
        await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                        cfg.account_id, '102', 'USDT', D('-1'), ms, b'synthetic-preverified-import')
        cash = flow(now, ms, operation_id='cash-pending', bill_id='102', signed_amount=D('-1'))
        assert (await financial.apply_cashflow(cash, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        through = ms
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, observed_at_ms=through, spot_cash_quote=D('.989'), collateral_quote=D('.989'))),
                    mark=replace(b.mark, observed_at_ms=through), index=replace(b.index, observed_at_ms=through),
                    funding=replace(b.funding, observed_at_ms=through, coverage_through_ms=through), terms=replace(b.terms, observed_at_ms=through))
        command = replace(command, additional_pending_operation_ids=(*command.additional_pending_operation_ids, cash.operation_id),
                          replacement=replace(command.replacement, cashflows=(replace(cash, operation_id='cash-repaired'),),
                                              funding=replace(command.replacement.funding, bundle=b)))
        facts = replace(facts, coverage_through_ms=through,
                        account=replace(facts.account, observed_at=now, account_quote_usdt=D('.989'), account_cashflow_ids=('101', '102')))
        cut = (await financial.observation_cut(cfg.binding)).cut
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (6, D('.989'))
        assert (await financial.snapshot(cfg.binding)).budget.session_execution_loss_usdt == D('.111')
        assert len(await values[5].claims(cfg.binding)) == 2
        for table in ('spot_cashflow_applications', 'spot_fill_facts', 'swap_fill_facts', 'funding_cash_payments', 'joint_repair_facts'):
            assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert not (await repo.frame(cfg.binding))['pending']
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_cashflow_repair_approval_expiry_is_rechecked_at_physical_commit(database, monkeypatch, stage):
    values, _, _, command, facts, cut, _, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=2))
        target = importer if stage == 'approval' else importer.runtime
        commit = target._commit
        if stage == 'application':
            await importer.approve(command, approval=signed, cut=cut, facts=facts)

        async def delayed(tx):
            at = await tx._connection.fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (signed.expires_at - at).total_seconds()) + .05)
            await commit(tx)
        monkeypatch.setattr(target, '_commit', delayed)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_EXPIRED_AT_COMMIT'):
            if stage == 'approval':
                await importer.approve(command, approval=signed, cut=cut, facts=facts)
            else:
                await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('spot_cashflow_applications', 'cashflows', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_unused_approval_cannot_replace_the_original_cashflow_application_root(database):
    values, _, _, command, facts, cut, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        alternate = replace(command, operation_id='unused-approval')
        auth = keys.sign('operator', cfg.binding, 'RECOVER', alternate.json(), cut, facts,
                         issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(alternate, approval=auth, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        c = database[0]
        await c.execute('ALTER TABLE life_financial.spot_cashflow_applications DISABLE TRIGGER USER')
        await c.execute('UPDATE life_financial.spot_cashflow_applications SET repair_operation_id=$1', alternate.operation_id)
        await c.execute('ALTER TABLE life_financial.spot_cashflow_applications ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['funding', 'swap'])
async def test_ordinary_money_observations_cannot_bypass_pending_cashflow(database, kind):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
        FillSwap,
        PgSwapFillRepository,
        SwapFillObservation,
    )
    values, funding, _, command, _, _, _, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(now.timestamp() * 1000)
        if kind == 'funding':
            b = command.replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('12'), collateral_quote=D('12'))))
            result = await funding.apply_funding(replace(command.replacement.funding, operation_id='ordinary-funding', bundle=b),
                                                 cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        else:
            fill = SwapFillObservation('ordinary-trade', D('1'), D('1.01'), 'USDT', D('-.01'), ms, D('1.01'), ms, ms,
                                       'independent_market', D('0'), None)
            swap = FillSwap('ordinary-swap', 'prepare1', 'swap1', 'SwapWire1', 'LIFE-USDT', 'SELL', str(owner.session_id),
                            owner.epoch, '987654321', D('1'), (fill,), now)
            result = await PgSwapFillRepository(funding.swap).apply_fills(swap, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outside', ['none', 'before_terminal', 'before_release'])
async def test_cashflow_repair_file_nav_parity_terminal_and_shared_release(database, outside):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh
    from types import SimpleNamespace

    from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery, RecoveryPayment
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
        PgSharedReleaseRepository,
        ReleaseSharedClaim,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
        ConfirmSwapTerminal,
        PgSwapTerminalRepository,
    )
    values, funding, _, command, facts, _, signed, importer, keys = await setup(database, credit_amount='20')
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        for name, parameters in (('portfolio', '1,1000,5000,64,1048576'), ('swap_terminal', '64,1048576'), ('shared_release', '64,1048576')):
            await c.execute('INSERT INTO life_financial.' + name + '_policies VALUES($1,$2,' + parameters + ')', cfg.account_id, cfg.binding.policy_hash)
        cut = (await financial.observation_cut(cfg.binding)).cut
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        values[4][0] = command.replacement.funding.bundle.account.snapshot
        values[7][0] = command.replacement.funding.bundle
        await refresh(database, values, cash='19')
        nav = await PgPortfolioRepository(funding.swap).measure(MeasurePortfolio('nav-after-cash-repair'),
                                                                cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        replay = object.__new__(JointRecovery)
        replay._binding = dict(opening_long_entry='0', opening_short_entry='0')
        replay._repository_application = None
        replay.controller = SimpleNamespace(_carry_monitor=None, _fill_attributor=SimpleNamespace(
            cashflow_approvals=SimpleNamespace(approved={'101': ('USDT', D('-11'))})))
        replay.reservations = SimpleNamespace(cashflow_events={'101': ('USDT', D('-11'))}, joint_cash_events={})
        opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        state = dict(anchor=dict(spot_life_base='5', spot_cash_quote='10', long_base='0', short_base='0', observed_at_ms=opening),
                     opening_cashflows={}, opening_joint={}, bundle=None, highwater='15')
        payments = tuple(RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms) for p in command.replacement.funding.bundle.funding.payments)
        _, expected = replay._financial_replay(SimpleNamespace(payments=payments, observed_at_ms=values[4][0].observed_at_ms), state, values[4][0], {})
        assert nav.measurement == {key: D(value) for key, value in expected.items()}

        async def other_pending():
            now = await c.fetchval('SELECT clock_timestamp()')
            ms = int(now.timestamp() * 1000)
            await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                            cfg.account_id, '102', 'USDT', D('-99'), ms, b'synthetic-preverified-import')
            pending = flow(now, ms, operation_id='outside-cash', bill_id='102', signed_amount=D('-99'))
            assert (await financial.apply_cashflow(pending, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        if outside == 'before_terminal':
            await other_pending()
        prepared = await c.fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
        terminal = ConfirmSwapTerminal('terminal-after-cash', 'prepare1', '987654321', 'canceled', D('0'), (),
                                       values[4][0].observed_at_ms, prepared, 'okx_joint_reconciled')
        terminals = PgSwapTerminalRepository(funding.swap)
        receipt = await terminals.confirm(terminal, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        if outside == 'before_terminal':
            assert receipt.state == 'RECOVERY_REQUIRED'
            assert len(await values[5].claims(cfg.binding)) == 1
        else:
            assert receipt.state == 'TERMINAL'
            if outside == 'before_release':
                await other_pending()
                assert (await terminals.lookup(terminal.operation_id, cfg.binding)).state == 'TERMINAL'
            release = ReleaseSharedClaim('release-after-cash', 'swap1', terminal.operation_id)
            releases = PgSharedReleaseRepository(values[5])
            if outside == 'before_release':
                with pytest.raises(StorageError, match='PG_SHARED_RELEASE_PENDING'):
                    await releases.release(release, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
                assert len(await values[5].claims(cfg.binding)) == 1
            else:
                assert (await releases.release(release, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).released
                assert await values[5].claims(cfg.binding) == ()
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 19
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['event', 'cashbook'])
async def test_cashflow_publication_rechecks_financial_event_and_book_at_physical_commit(database, monkeypatch, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import capital_store
    financial, cfg, owner, at, ms = await capital_store(database)
    try:
        # Privileged test-only fault, with no runtime table write grant. The
        # original raw fact/fingerprint/receipt remain internally consistent.
        statement = ("DELETE FROM life_financial.financial_events WHERE account_id=p_account AND kind='CASHFLOW'" if fault == 'event' else
                     "UPDATE life_financial.cashflows SET value_usdt=3 WHERE account_id=p_account")
        table = 'financial_events' if fault == 'event' else 'cashflows'
        await database[0].execute('''CREATE FUNCTION life_financial.test_damage_cash_publication(p_account uuid) RETURNS void
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
                ALTER TABLE life_financial.''' + table + ''' DISABLE TRIGGER USER;
                ''' + statement + ''';
                ALTER TABLE life_financial.''' + table + ''' ENABLE TRIGGER USER;
            END; $$;
            REVOKE ALL ON FUNCTION life_financial.test_damage_cash_publication(uuid) FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION life_financial.test_damage_cash_publication(uuid) TO life_pg2_runtime''')
        commit = financial._runtime._commit

        async def damaged(tx):
            await tx._connection.execute('SELECT life_financial.test_damage_cash_publication($1)', cfg.account_id)
            await commit(tx)
        monkeypatch.setattr(financial._runtime, '_commit', damaged)
        with pytest.raises(StorageError, match='PG_CASHFLOW_HISTORY_UNVERIFIED_AT_COMMIT'):
            await financial.apply_cashflow(flow(at, ms), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for name in ('cashflows', 'spot_cashflow_facts', 'spot_cashflow_applications'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + name) == 0
    finally:
        await financial.close()
