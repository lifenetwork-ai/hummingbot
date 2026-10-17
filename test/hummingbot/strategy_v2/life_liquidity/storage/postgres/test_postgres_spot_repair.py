"""Exact SPOT pending recovery retains original fees, holds and gross budgets."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import ReserveCapital
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, mixed=False, fee_currency='USDT', credit_later=False, swap_pending=False, credit_amount='2'):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic test key.
    values, funding, payment = await funding_setup(
        database, amount=credit_amount, carry_options=dict(refresh_initial_facts=True),
        spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    c = database[0]
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    claim = replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')
    await values[5].reserve(ReserveCapital('spotclaim', claim), await values[5].cut(cfg.binding), owner)
    await values[8].check_carry(cfg.binding)
    await financial.prepare(order(owner, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002')),
                            cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    now = await c.fetchval('SELECT clock_timestamp()')
    await financial.acknowledge(ack(owner, now), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
    ms = int(now.timestamp() * 1000)
    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                    cfg.account_id, '101', 'USDT', D('-10'), ms, b'synthetic-preverified-import')
    assert (await financial.apply_cashflow(flow(now, ms, signed_amount=D('-10')),
                                           cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    await asyncio.sleep(.005)  # Distinct synthetic economic times.
    now = await c.fetchval('SELECT clock_timestamp()')
    original = filled(owner, now, operation_id='spot-pending')
    original = replace(original, fills=(replace(original.fills[0], trade_id='spottrade1', fee_currency=fee_currency, signed_fee=D('-.001')),))
    assert (await financial.apply_fills(original, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    assert await c.fetchval('SELECT reason FROM life_financial.spot_pending_observations') == 'NEGATIVE_WALLET_UNSUPPORTED'
    at = original.fills[0].fill_at_ms
    swap_fills = ()
    if swap_pending:
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
            FillSwap,
            PgSwapFillRepository,
            SwapFillObservation,
        )
        swap_fill = SwapFillObservation('swaptrade1', D('1'), D('1.01'), 'USDT', D('-.01'), at, D('1.01'), at, at,
                                        'independent_market', D('0'), None)
        swap = FillSwap('swap-pending', 'prepare1', 'swap1', 'SwapWire1', 'LIFE-USDT', 'SELL', str(owner.session_id),
                        owner.epoch, '987654321', D('1'), (swap_fill,), now)
        assert (await PgSwapFillRepository(funding.swap).apply_fills(swap, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        swap_fills = (replace(swap, operation_id='swap-repaired'),)
    await asyncio.sleep(.005)
    now = await c.fetchval('SELECT clock_timestamp()')
    through = int(now.timestamp() * 1000)
    b = payment.bundle
    credit = replace(b.funding.payments[0], settled_at_ms=at + 1 if credit_later else at - 1)
    cash = D(credit_amount) - D('1') - (D('.001') if fee_currency == 'USDT' else D('0'))
    if swap_pending:
        cash -= D('.01')
    base = D('6') if fee_currency == 'USDT' else D('5.999')
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=through,
                                                               spot_cash_quote=cash, collateral_quote=cash, spot_life_base=base, short_base=D('1') if swap_pending else D('0'))),
                mark=replace(b.mark, sequence=3, observed_at_ms=through), index=replace(b.index, sequence=3, observed_at_ms=through),
                funding=replace(b.funding, sequence=3, observed_at_ms=through, coverage_through_ms=through, payments=(credit,)),
                terms=replace(b.terms, sequence=3, observed_at_ms=through))
    if mixed:
        assert (await funding.apply_funding(replace(payment, bundle=b), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    replacement = ApplyJointBatch('batch-repaired', (replace(original, operation_id='spot-repaired'),), swap_fills, (),
                                  replace(payment, operation_id='funding-repaired', bundle=b))
    command = ResolveJointSources('repair-spot', original.operation_id, replacement,
                                  (('funding1',) if mixed else ()) + (('swap-pending',) if swap_pending else ()))
    account = SpotFinancialRecoveryFacts(cfg.binding, now, base, cash, D('0'), D('0'), ('spottrade1', 'swaptrade1') if swap_pending else ('spottrade1',), ('101',),
                                         ('SwapWire1', 'wire1'), (), True, True, True)
    opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
    facts = JointRepairFacts(account, (('SPOT', 'spottrade1'),) + ((('SWAP', 'swaptrade1'),) if swap_pending else ()), ('777',), True, opening, through)
    cut = (await financial.observation_cut(cfg.binding)).cut
    signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                       issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgJointRepairApprovalImporter(c, PgJointBatchRepository(funding.swap), verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, funding, original, command, facts, cut, signed, importer, keys


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed,fee_currency', [(False, 'USDT'), (True, 'USDT'), (False, 'LIFE')])
async def test_exact_signed_spot_pending_applies_principal_fee_and_subsidy_once(database, mixed, fee_currency):
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=mixed, fee_currency=fee_currency)
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        raw = await c.fetchval('SELECT to_jsonb(p) FROM life_financial.spot_pending_observations p')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
        reader = PgSpotObservationReader(financial, max_rows=128, max_bytes=1048576)
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_PENDING_RESOLUTION_REQUIRED'):
            await reader.observation_context(cfg.binding)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        assert (await repo.resolve(command, cut=cut, owner=owner)).state == 'REPAIRED'
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert (await financial.lookup_fill(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await c.fetchval('SELECT to_jsonb(p) FROM life_financial.spot_pending_observations p') == raw
        assert json.loads(await c.fetchval('SELECT observation FROM life_financial.spot_fill_facts')) == original.fills[0].json()
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (facts.account.account_base_life, facts.account.account_quote_usdt)
        budget = (await financial.snapshot(cfg.binding)).budget
        expected_loss = D('.101') if fee_currency == 'USDT' else D('.1009')
        assert budget.session_execution_loss_usdt == budget.session_subsidy_committed_usdt == expected_loss
        assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 1
        assert not (await repo.frame(cfg.binding))['pending']
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 2
        context = await reader.observation_context(cfg.binding)
        assert context.pending == () and tuple(row.fill for row in context.fills) == original.fills
        duplicate = replace(original, operation_id='duplicate-after-repair')
        assert (await financial.apply_fills(duplicate, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert (await financial.balance(cfg.binding)) == balance
        assert (await financial.snapshot(cfg.binding)).budget == budget
        assert await c.fetchval('SELECT life_financial.spot_pending_verified($1)', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [False, True])
async def test_identical_spot_pending_snapshots_need_exact_selected_group(database, selected):
    values, _, original, command, facts, _, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        assert (await financial.apply_fills(replace(original, operation_id='other-pending'),
                                            cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        cut = (await financial.observation_cut(cfg.binding)).cut
        if selected:
            command = replace(command, additional_pending_operation_ids=('other-pending',))
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        if selected:
            await repo.resolve(command, cut=cut, owner=owner)
            assert not (await repo.frame(cfg.binding))['pending']
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 1
        else:
            other = replace(command, operation_id='another-repair', pending_operation_id='other-pending')
            auth = keys.sign('operator', cfg.binding, 'RECOVER', other.json(), cut, facts,
                             issued_at=signed.issued_at, expires_at=signed.expires_at)
            await importer.approve(other, approval=auth, cut=cut, facts=facts)
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await repo.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert (await financial.lookup_fill('other-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'currency', 'price', 'reference', 'time', 'trade', 'wire', 'omitted', 'extra', 'signature', 'cash'])
async def test_no_signed_repair_may_rewrite_original_spot_money_or_scope(database, fault):
    values, _, original, command, facts, cut, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        trade = command.replacement.spot_fills[0]
        row = trade.fills[0]
        if fault in ('fee', 'currency', 'price', 'reference', 'time', 'trade'):
            change = dict(fee=dict(signed_fee=D('0')), currency=dict(fee_currency='LIFE'), price=dict(price_usdt=D('.9')),
                          reference=dict(independent_price_usdt=D('1')), time=dict(fill_at_ms=row.fill_at_ms - 1), trade=dict(trade_id='another'))[fault]
            trade = replace(trade, fills=(replace(row, **change),))
        elif fault == 'wire':
            trade = replace(trade, client_order_id='AnotherWire')
        replacement = replace(command.replacement, spot_fills=(trade,))
        if fault == 'omitted':
            replacement = replace(replacement, spot_fills=())
        elif fault == 'extra':
            replacement = replace(replacement, spot_fills=(trade, replace(trade, operation_id='extra-fill', fills=(replace(row, trade_id='extra'),))))
        elif fault == 'cash':
            b = replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('2'))))
            replacement = replace(replacement, funding=replace(replacement.funding, bundle=b))
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
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 2
        assert (await financial.lookup_fill(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        for table in ('spot_fill_facts', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_positive_final_cash_cannot_finance_spot_principal_before_funding_arrived(database):
    values, _, _, command, facts, cut, signed, importer, _ = await setup(database, credit_later=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('primary', ['SPOT', 'SWAP', 'FUNDING'])
async def test_spot_swap_and_funding_originals_recover_in_one_account_transaction(database, primary):
    values, _, _, command, facts, cut, signed, importer, keys = await setup(database, mixed=True, swap_pending=True)
    financial, cfg, owner = values[:3]
    try:
        targets = {'SPOT': 'spot-pending', 'SWAP': 'swap-pending', 'FUNDING': 'funding1'}
        first = targets[primary]
        command = replace(command, pending_operation_id=first, additional_pending_operation_ids=tuple(v for v in targets.values() if v != first))
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        assert not (await repo.frame(cfg.binding))['pending']
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (D('6'), D('.989'))
        assert (await financial.snapshot(cfg.binding)).budget.session_execution_loss_usdt == D('.111')
        for table in ('spot_fill_facts', 'swap_fill_facts', 'funding_cash_payments', 'joint_repair_facts'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_members') == 2
        assert len(await values[5].claims(cfg.binding)) == 2
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outside', [False, True])
async def test_repaired_spot_reaches_native_terminal_and_original_shared_release(database, outside):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle, terminal
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
        PgSharedReleaseRepository,
        ReleaseSharedClaim,
    )
    values, funding, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True, credit_amount='2.1')
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        await c.execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await c.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        # Newly installed policies affect the signed financial cut.
        cut = (await financial.observation_cut(cfg.binding)).cut
        keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        values[4][0] = command.replacement.funding.bundle.account.snapshot
        values[7][0] = command.replacement.funding.bundle
        await refresh(database, values, cash='1.099')
        nav = await PgPortfolioRepository(funding.swap).measure(MeasurePortfolio('nav-after-repair'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        from types import SimpleNamespace

        from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery, RecoveryPayment, RecoveryTrade
        replay = object.__new__(JointRecovery)
        replay._binding = dict(opening_long_entry='0', opening_short_entry='0')
        replay._repository_application = None
        replay.controller = SimpleNamespace(_carry_monitor=None, _fill_attributor=SimpleNamespace(
            cashflow_approvals=SimpleNamespace(approved={'101': ('USDT', D('-10'))})))
        replay.reservations = SimpleNamespace(cashflow_events={'101': ('USDT', D('-10'))}, joint_cash_events={})
        opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        state = dict(anchor=dict(spot_life_base='5', spot_cash_quote='10', long_base='0', short_base='0', observed_at_ms=opening),
                     opening_cashflows={}, opening_joint={}, bundle=None, highwater='15')
        fill = original.fills[0]
        trade = RecoveryTrade(fill.trade_id, fill.quantity_base_life, fill.price_usdt, fill.fee_currency, fill.signed_fee, D('0'), fill.fill_at_ms)
        payments = tuple(RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms) for p in command.replacement.funding.bundle.funding.payments)
        _, expected = replay._financial_replay(SimpleNamespace(payments=payments, observed_at_ms=values[4][0].observed_at_ms), state,
                                               values[4][0], {trade.trade_id: (SimpleNamespace(product='SPOT', side='BUY'), trade)})
        assert nav.measurement == {key: D(value) for key, value in expected.items()}

        if outside:
            b = command.replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('2'))))
            pending = replace(command.replacement, operation_id='outside-batch', spot_fills=(),
                              funding=replace(command.replacement.funding, operation_id='outside-funding', bundle=b))
            assert (await importer.batch.apply(pending, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        confirmation = await terminal(financial, cfg, owner, fills=original.fills, account_cashflow_ids=('101',))
        if outside:
            with pytest.raises(StorageError, match='PG_TERMINAL_SCOPE_OR_ACCOUNT_INVALID'):
                await financial.confirm_terminal(confirmation, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
            assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 1
            assert len(await values[5].claims(cfg.binding)) == 2
            # The terminal barrier must not block safety cancellation.
            await financial.apply_lifecycle(lifecycle('REQUEST_CANCEL'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
            assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 1
            return
        assert (await financial.confirm_terminal(confirmation, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
        # Terminal releases order principal/fee holds, not filled inventory cost.
        budget = (await financial.snapshot(cfg.binding)).budget
        assert budget.session_execution_loss_usdt == budget.session_subsidy_committed_usdt == D('.101')
        release = ReleaseSharedClaim('release-spot', 'intent1', confirmation.operation_id)
        assert (await PgSharedReleaseRepository(values[5]).release(release, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).released
        assert [claim.intent_id for claim in await values[5].claims(cfg.binding)] == ['swap1']
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('1.099')
        assert (await financial.lookup_fill(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await c.fetchval('SELECT life_financial.spot_pending_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.spot_lifecycle_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_spot_process_exit_has_one_original_principal_fee_and_subsidy_effect(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True, swap_pending=True)
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
        assert (await cold.lookup_fill(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('.989')
        assert (await cold.snapshot(cfg.binding)).budget.session_execution_loss_usdt == D('.111')
        assert (await cold.reservation('intent1', cfg.binding)).remaining_base_life == 1
        for table in ('spot_fill_facts', 'swap_fill_facts', 'funding_cash_payments', 'joint_repair_facts'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert len(await capital.claims(cfg.binding)) == 2
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['pending', 'source_hash', 'receipt', 'orphan', 'resolution'])
async def test_spot_damage_remains_unverified_after_resolution(database, target):
    values, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        c = database[0]
        table = 'operations' if target in ('receipt', 'resolution') else 'spot_pending_observations'
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'pending':
            await c.execute("UPDATE life_financial.spot_pending_observations SET command=jsonb_set(command,'{fills,0,signed_fee}','\"0\"')")
        elif target == 'source_hash':
            await c.execute("UPDATE life_financial.spot_pending_observations SET source_hash=repeat('0',64)")
        elif target == 'receipt':
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{execution_loss_usdt}','\"1\"') WHERE operation_id=$1", original.operation_id)
        elif target == 'orphan':
            await c.execute('DELETE FROM life_financial.spot_pending_observations')
        else:
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{pending_operation_id}','\"other\"') WHERE operation_id=$1", command.operation_id)
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('.999')
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_expiring_spot_approval_rolls_back_all_money_and_holds_at_commit(database, monkeypatch, stage):
    values, _, _, command, facts, cut, _, importer, keys = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    now = await database[0].fetchval('SELECT clock_timestamp()')
    signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                       issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=2))
    target = importer if stage == 'approval' else importer.runtime
    commit = target._commit
    try:
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
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert (await financial.reservation('intent1', cfg.binding)).remaining_base_life == 2
        for table in ('spot_fill_facts', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
