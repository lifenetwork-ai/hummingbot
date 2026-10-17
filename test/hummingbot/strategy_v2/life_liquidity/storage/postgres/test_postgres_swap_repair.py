"""Retained native SWAP observations recover only with exact signed facts."""
import asyncio
import json
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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
    FillSwap,
    PgSwapFillRepository,
    SwapFillObservation,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, mixed=False, credit_later=False):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic test key.
    values, funding, payment = await funding_setup(
        database, amount='.1', carry_options=dict(refresh_initial_facts=True),
        spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    c = database[0]
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    now = await c.fetchval('SELECT clock_timestamp()')
    ms = int(now.timestamp() * 1000)
    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                    cfg.account_id, '101', 'USDT', D('-10'), ms, b'synthetic-preverified-import')
    assert (await financial.apply_cashflow(flow(now, ms, signed_amount=D('-10')),
                                           cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    await asyncio.sleep(.005)  # Distinct economic times, not a source freshness workaround.
    now = await c.fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    observation = SwapFillObservation('trade1', D('1'), D('1.01'), 'USDT', D('-.01'), at, D('1.01'), at, at,
                                      'independent_market', D('0'), None)
    original = FillSwap('fill-pending', 'prepare1', 'swap1', 'SwapWire1', 'LIFE-USDT', 'SELL', str(owner.session_id),
                        owner.epoch, '987654321', D('1'), (observation,), now)
    fills = PgSwapFillRepository(funding.swap)
    pending = await fills.apply_fills(original, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
    assert pending.state == 'RECOVERY_REQUIRED'
    assert await c.fetchval('SELECT reason FROM life_financial.swap_pending_fills WHERE operation_id=$1', original.operation_id) == 'SWAP_NEGATIVE_WALLET_UNSUPPORTED'
    assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
    now = await c.fetchval('SELECT clock_timestamp()')
    through = int(now.timestamp() * 1000)
    b = payment.bundle
    credit = replace(b.funding.payments[0], settled_at_ms=at + 1 if credit_later else at - 1)
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=through,
                                                               spot_cash_quote=D('.09'), collateral_quote=D('.09'), short_base=D('1'))),
                mark=replace(b.mark, sequence=3, observed_at_ms=through), index=replace(b.index, sequence=3, observed_at_ms=through),
                funding=replace(b.funding, sequence=3, observed_at_ms=through, coverage_through_ms=through, payments=(credit,)),
                terms=replace(b.terms, sequence=3, observed_at_ms=through))
    if mixed:
        pending = await funding.apply_funding(replace(payment, bundle=b), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert pending.state == 'RECOVERY_REQUIRED'
    replacement = ApplyJointBatch('batch-repaired', (), (replace(original, operation_id='fill-repaired'),), (),
                                  replace(payment, operation_id='funding-repaired', bundle=b))
    command = ResolveJointSources('repair-swap', original.operation_id, replacement, ('funding1',) if mixed else ())
    account = SpotFinancialRecoveryFacts(cfg.binding, now, D('5'), D('.09'), D('0'), D('0'), ('trade1',), ('101',),
                                         ('SwapWire1',), (), True, True, True)
    opening = await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
    facts = JointRepairFacts(account, (('SWAP', 'trade1'),), ('777',), True, opening, through)
    cut = (await financial.observation_cut(cfg.binding)).cut
    signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                       issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgJointRepairApprovalImporter(c, PgJointBatchRepository(funding.swap), verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, funding, fills, original, command, facts, cut, signed, importer, keys


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed', [False, True])
async def test_signed_swap_recovery_keeps_original_trade_fee_and_pending_receipt(database, mixed):
    values, funding, fills, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=mixed)
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        raw = await c.fetchval('SELECT to_jsonb(f) FROM life_financial.swap_pending_fills f WHERE operation_id=$1', original.operation_id)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repair = PgJointRepairRepository(importer.batch)
        receipt = await repair.resolve(command, cut=cut, owner=owner)
        assert receipt.state == 'REPAIRED' and not receipt.trading_permission
        assert (await repair.resolve(command, cut=cut, owner=owner)).replayed
        assert (await fills.lookup(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await c.fetchval('SELECT to_jsonb(f) FROM life_financial.swap_pending_fills f WHERE operation_id=$1', original.operation_id) == raw
        assert json.loads(await c.fetchval('SELECT observation FROM life_financial.swap_fill_facts')) == original.fills[0].json()
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('.09')
        assert await c.fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.joint_financial_batches') == 1
        assert not (await repair.frame(cfg.binding))['pending']
        assert not await c.fetchval('SELECT life_financial.joint_financial_pending($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.swap_accounting_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.joint_repair_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
        state = await values[5]._read('SELECT life_financial.spot_state($1)', (cfg.account_id,))
        assert not state['swap_accounting']['pending']
        assert len(state['swap_accounting']['pending_facts']) == 1
        duplicate = replace(original, operation_id='duplicate-after-repair')
        assert (await fills.apply_fills(duplicate, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('.09')
        assert await c.fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'pnl', 'price', 'trade', 'time', 'wire', 'omitted', 'extra', 'signature', 'cash'])
async def test_signed_repair_cannot_change_original_swap_financial_facts_or_invent_cash(database, fault):
    values, _, _, _, command, facts, cut, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        trade = command.replacement.swap_fills[0]
        fill = trade.fills[0]
        if fault in ('fee', 'pnl', 'price', 'trade', 'time'):
            changes = dict(fee=dict(signed_fee=D('0')), pnl=dict(realized_pnl_usdt=D('1')), price=dict(price_usdt=D('1')),
                           trade=dict(trade_id='another'), time=dict(fill_at_ms=fill.fill_at_ms - 1))[fault]
            trade = replace(trade, fills=(replace(fill, **changes),))
        elif fault == 'wire':
            trade = replace(trade, client_order_id='AnotherWire')
        replacement = replace(command.replacement, swap_fills=(trade,))
        if fault == 'omitted':
            replacement = replace(replacement, swap_fills=())
        elif fault == 'extra':
            extra = replace(trade, operation_id='extra-fill', fills=(replace(fill, trade_id='extra'),))
            replacement = replace(replacement, swap_fills=(trade, extra))
        elif fault == 'cash':
            b = replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('1'))))
            replacement = replace(replacement, funding=replace(replacement.funding, bundle=b))
            facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('1')))
        command = replace(command, replacement=replacement)
        if fault != 'signature':
            signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                               issued_at=signed.issued_at, expires_at=signed.expires_at)
        if fault == 'cash':
            await importer.approve(command, approval=signed, cut=cut, facts=facts)
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        else:
            # For the unchanged payload a RECOVER signature is required too.
            if fault == 'signature':
                signed = replace(signed, signature='0' * 128)
            with pytest.raises(StorageError, match='(PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID|OPERATOR_APPROVAL)'):
                await importer.approve(command, approval=signed, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        for table in ('swap_fill_facts', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_staging', 'joint_batch_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_future_funding_cannot_pay_an_earlier_swap_fee_despite_positive_final_cash(database):
    values, _, _, _, command, facts, cut, signed, importer, _ = await setup(database, credit_later=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [False, True])
async def test_same_trade_in_another_pending_requires_explicit_cohort_membership(database, selected):
    values, _, fills, original, command, facts, _, signed, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        assert (await fills.apply_fills(replace(original, operation_id='other-pending'),
                                        cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        cut = (await financial.observation_cut(cfg.binding)).cut
        if selected:
            command = replace(command, additional_pending_operation_ids=('other-pending',))
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repair = PgJointRepairRepository(importer.batch)
        if selected:
            await repair.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == D('.09')
            assert not (await repair.frame(cfg.binding))['pending']
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        else:
            # Even a separate signed approval sharing the same replacement is
            # not the exact capability selected by the active transaction.
            other = replace(command, operation_id='other-repair', pending_operation_id='other-pending')
            auth = keys.sign('operator', cfg.binding, 'RECOVER', other.json(), cut, facts,
                             issued_at=signed.issued_at, expires_at=signed.expires_at)
            await importer.approve(other, approval=auth, cut=cut, facts=facts)
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await repair.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == 0
        assert (await fills.lookup('other-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_funding_primary_and_swap_secondary_use_the_same_exact_scope(database):
    values, _, fills, original, command, facts, cut, signed, importer, keys = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    try:
        command = replace(command, pending_operation_id='funding1', additional_pending_operation_ids=(original.operation_id,))
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        assert not (await repo.frame(cfg.binding))['pending']
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('.09')
        assert (await fills.lookup(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_process_exit_around_swap_repair_commit_has_one_original_trade_and_fee(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        await exit_joint_repair(cfg, command, cut, owner, committed)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        repo = PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)))
        found = await repo.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('.09')
        assert (await PgSwapFillRepository(repo.batch.swap).lookup(original.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outside_joint', ['none', 'before', 'after'])
async def test_repaired_swap_reaches_fresh_nav_terminal_and_original_claim_release(database, outside_joint):
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
    values, funding, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    try:
        c = database[0]
        await c.execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await c.execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await c.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        # Installing explicit immutable policies changes the cut; sign that cut.
        cut = (await financial.observation_cut(cfg.binding)).cut
        keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                           issued_at=signed.issued_at, expires_at=signed.expires_at)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        assert await c.fetchval('SELECT life_financial.joint_source_unresolved($1,$2,$3,false)', cfg.account_id, original.operation_id, cut.checkpoint.revision)
        assert not await c.fetchval('SELECT life_financial.joint_source_unresolved($1,$2,NULL,false)', cfg.account_id, original.operation_id)
        values[4][0] = command.replacement.funding.bundle.account.snapshot
        values[7][0] = command.replacement.funding.bundle
        await refresh(database, values, cash='.09', short='1', mark='1.01', index='1.01')
        nav = await PgPortfolioRepository(funding.swap).measure(MeasurePortfolio('nav-after-repair'),
                                                                cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert nav.measurement['nav_quote'] == D('5.14')
        # Actual File valuation over the same opening anchor, original trade,
        # approved withdrawal and funding. This is financial parity, not a
        # claim that the configured native controller/service is assembled.
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
        trade = RecoveryTrade(fill.trade_id, fill.quantity_base_life, fill.price_usdt, fill.fee_currency, fill.signed_fee,
                              fill.realized_pnl_usdt, fill.fill_at_ms)
        payments = tuple(RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms)
                         for p in command.replacement.funding.bundle.funding.payments)
        _, expected = replay._financial_replay(SimpleNamespace(payments=payments, observed_at_ms=values[4][0].observed_at_ms),
                                               state, values[4][0], {trade.trade_id: (SimpleNamespace(product='SWAP', side='SELL', position_action='OPEN'), trade)})
        assert nav.measurement == {key: D(value) for key, value in expected.items()}
        if outside_joint == 'before':
            b = command.replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('1'))))
            pending = replace(command.replacement, operation_id='outside-batch', swap_fills=(),
                              funding=replace(command.replacement.funding, operation_id='outside-funding', bundle=b))
            assert (await importer.batch.apply(pending, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        prepared = await c.fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
        command_terminal = ConfirmSwapTerminal('terminal-after-repair', 'prepare1', original.exchange_order_id, 'filled', D('1'), ('trade1',),
                                               values[4][0].observed_at_ms, prepared, 'okx_joint_reconciled')
        terminals = PgSwapTerminalRepository(funding.swap)
        terminal = await terminals.confirm(command_terminal, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        if outside_joint == 'before':
            assert terminal.state == 'RECOVERY_REQUIRED'
            assert len(await values[5].claims(cfg.binding)) == 1
            assert await c.fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='swap1'") == 'ACKED'
            return
        assert terminal.state == 'TERMINAL'
        if outside_joint == 'after':
            b = command.replacement.funding.bundle
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('1'))))
            pending = replace(command.replacement, operation_id='outside-batch', swap_fills=(),
                              funding=replace(command.replacement.funding, operation_id='outside-funding', bundle=b))
            assert (await importer.batch.apply(pending, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
            assert await c.fetchval('SELECT life_financial.swap_terminal_verified($1)', cfg.account_id)
            with pytest.raises(StorageError, match='PG_SHARED_RELEASE_PENDING'):
                await PgSharedReleaseRepository(values[5]).release(ReleaseSharedClaim('blocked-release', 'swap1', command_terminal.operation_id),
                                                                   cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
            assert len(await values[5].claims(cfg.binding)) == 1
            return
        release = ReleaseSharedClaim('release-after-repair', 'swap1', command_terminal.operation_id)
        assert (await PgSharedReleaseRepository(values[5]).release(release, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).released
        assert await values[5].claims(cfg.binding) == ()
        assert await c.fetchval('SELECT life_financial.swap_terminal_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['pending', 'resolution', 'membership'])
async def test_damaged_swap_recovery_is_never_accepted_as_verified_history(database, target):
    values, _, _, original, command, facts, cut, signed, importer, _ = await setup(database, mixed=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        c = database[0]
        table = dict(pending='swap_pending_fills', resolution='operations', membership='joint_repair_members')[target]
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'pending':
            await c.execute("UPDATE life_financial.swap_pending_fills SET command=jsonb_set(command,'{fills,0,signed_fee}','\"0\"') WHERE operation_id=$1", original.operation_id)
        elif target == 'resolution':
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{pending_operation_id}','\"other\"') WHERE operation_id=$1", command.operation_id)
        else:
            await c.execute('DELETE FROM life_financial.joint_repair_members')
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('.09')
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_swap_repair_approval_expiring_at_physical_commit_books_no_trade(database, monkeypatch, stage):
    values, _, _, _, command, facts, cut, _, importer, keys = await setup(database, mixed=True)
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
        for table in ('swap_fill_facts', 'funding_cash_payments', 'joint_repair_facts', 'joint_repair_members', 'joint_repair_staging', 'joint_batch_staging'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_pending_fills') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
