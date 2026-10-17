"""Settled funding cash is distinct from forecast, transfer or execution loss."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import setup as swap_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding, PgFundingRepository


async def setup(database, *, amount='-.1', budget='10'):
    values, swap, fills, fill_command = await swap_setup(database, carry_options=dict(funding_budget_quote=D(budget)))
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.funding_accounting_policies VALUES($1,$2,64,128,1048576)',
                              cfg.account_id, cfg.binding.policy_hash)
    received = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(received.timestamp() * 1000)
    b = values[7][0]
    payment = FundingPayment('777', at, D(amount))
    snapshot = replace(b.account.snapshot, sequence=2, observed_at_ms=at,
                       spot_cash_quote=D('10') + D(amount), collateral_quote=b.account.snapshot.collateral_quote + D(amount))
    bundle = replace(b, account=replace(b.account, snapshot=snapshot, included_funding_bill_ids=('777',)),
                     mark=replace(b.mark, sequence=2, observed_at_ms=at), index=replace(b.index, sequence=2, observed_at_ms=at),
                     funding=replace(b.funding, sequence=2, observed_at_ms=at, coverage_through_ms=at, payments=(payment,)),
                     terms=replace(b.terms, sequence=2, observed_at_ms=at))
    return values, PgFundingRepository(swap), ApplyFunding('funding1', bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['-.1', '.2', '0'])
async def test_settled_funding_adjusts_only_actual_cash_once_and_never_spot_transfer_or_execution_loss(database, amount):
    values, repo, command = await setup(database, amount=amount)
    financial, cfg, owner = values[:3]
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        assert ApplyFunding.from_json(command.json()) == command
        receipt = await repo.apply_funding(command, cut=cut, owner=owner)
        assert receipt.state == 'APPLIED' and receipt.atomic_scope is AtomicScope.ACCOUNT and not receipt.trading_permission
        assert receipt.cash_delta_usdt == D(amount)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('10') + D(amount)
        assert (await repo.apply_funding(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        frame = await repo.frame(cfg.binding)
        assert len(frame['payments']) == 1 and D(frame['gross_debits_usdt']) == max(D('0'), -D(amount))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.budget_events') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await financial.snapshot(cfg.binding)).recovery_required  # New complete carry/account facts must be installed.
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['money', 'incomplete_bills', 'changed_bill', 'history_missing'])
async def test_unqualified_or_conflicting_funding_retains_pending_halt_without_cash_credit(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        if fault in ('changed_bill', 'history_missing'):
            await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        b = command.bundle
        if fault == 'money':
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('20'))))
        elif fault == 'incomplete_bills':
            b = replace(b, funding=replace(b.funding, scope_complete=False))
        elif fault == 'changed_bill':
            b = replace(b, funding=replace(b.funding, payments=(replace(b.funding.payments[0], signed_payment_quote=D('1')),)))
        else:
            b = replace(b, account=replace(b.account, included_funding_bill_ids=()), funding=replace(b.funding, payments=()))
        before = await financial.balance(cfg.binding)
        bad = replace(command, operation_id='bad', bundle=b)
        receipt = await repo.apply_funding(bad, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'RECOVERY_REQUIRED' and receipt.cash_delta_usdt == 0
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required
        pending = (await repo.frame(cfg.binding))['pending']
        assert len(pending) == 1 and pending[0]['command'] == bad.json()
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_actual_funding_budget_exhaustion_halts_and_later_receipts_do_not_net_or_reset_cost(database):
    values, repo, command = await setup(database, budget='.1')
    financial, cfg, owner = values[:3]
    try:
        assert (await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        b = command.bundle
        incoming = FundingPayment('888', at, D('.1'))
        snapshot = replace(b.account.snapshot, sequence=3, observed_at_ms=at, spot_cash_quote=D('10'),
                           collateral_quote=b.account.snapshot.collateral_quote + D('.1'))
        second_bundle = replace(b, account=replace(b.account, snapshot=snapshot, included_funding_bill_ids=('777', '888')),
                                mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                                funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at, payments=(*b.funding.payments, incoming)),
                                terms=replace(b.terms, sequence=3, observed_at_ms=at))
        second = replace(command, operation_id='incoming', bundle=second_bundle)
        result = await repo.apply_funding(second, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED' and result.gross_debits_usdt == D('.1') and result.cash_delta_usdt == D('.1')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_funding_already_inside_opening_wallet_is_retained_as_cost_evidence_and_not_debited_again(database):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        opened = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        past = FundingPayment('777', opened - 1, D('-.1'))
        b = command.bundle
        snapshot = replace(b.account.snapshot, spot_cash_quote=D('10'), collateral_quote=values[4][0].collateral_quote)
        b = replace(b, account=replace(b.account, snapshot=snapshot), funding=replace(b.funding, payments=(past,)))
        receipt = await repo.apply_funding(replace(command, bundle=b), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'APPLIED' and receipt.cash_delta_usdt == 0 and receipt.gross_debits_usdt == D('.1')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.frame(cfg.binding))['payments'] == []
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_funding_commit_cold_readback_never_applies_a_settled_bill_twice(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.funding_process import exit_funding

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        if fault == 'process':
            await exit_funding(cfg, command, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic paid funding COMMIT reply loss')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.apply_funding(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgFundingRepository(PgSwapRepository(capital))
        found = await restored.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (D('9.9') if committed else D('10'))
        if not committed:
            assert (await restored.apply_funding(command, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await restored.apply_funding(command, cut=cut, owner=owner)).replayed
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert len((await restored.frame(cfg.binding))['payments']) == 1
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'event', 'receipt', 'deleted_payment'])
async def test_funding_projection_corruption_denies_common_account_readiness_and_cold_bill_readback(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        table = dict(amount='funding_cash_payments', event='financial_events', receipt='operations', deleted_payment='funding_cash_payments')[fault]
        query = dict(amount='UPDATE life_financial.funding_cash_payments SET signed_payment_usdt=1',
                     event="UPDATE life_financial.financial_events SET quote_delta=1 WHERE kind='FUNDING'",
                     receipt="UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{cash_delta_usdt}','\"1\"') WHERE operation_id='funding1'",
                     deleted_payment='DELETE FROM life_financial.funding_cash_payments')[fault]
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(query)
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='FUNDING_UNVERIFIED'):
            await repo.frame(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_paid_funding_commit_rechecks_original_owner_and_rolls_back_cash_and_bill_identity(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    original = financial._runtime._commit
    try:
        before = await financial.balance(cfg.binding)

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert await financial.balance(cfg.binding) == before
        assert (await repo.frame(cfg.binding))['payments'] == []
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_current_post_payment_account_and_carry_are_required_without_automatic_rearm(database):
    values, repo, command = await setup(database, amount='0')
    financial, cfg, owner = values[:3]
    try:
        await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        values[4][0] = command.bundle.account.snapshot
        assert (await values[5].check_capital(cfg.binding)).allowed
        values[7][0] = command.bundle
        assert (await values[8].check_carry(cfg.binding)).allowed
        assert not (await financial.snapshot(cfg.binding)).recovery_required
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await repo.lookup(command.operation_id, cfg.binding)).trading_permission is False
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_funding_work_and_read_bounds_are_explicit_and_runtime_cannot_edit_financial_facts(database):
    import asyncpg
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        assert await reporter.fetchval('SELECT life_financial.funding_read($1,64,1048576,NULL)', cfg.account_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.funding_command($1,NULL,NULL,0,NULL,NULL,NULL)', cfg.account_id)
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            for table in ('funding_accounting_policies', 'funding_observations', 'funding_cash_payments'):
                assert not await database[0].fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", role, 'life_financial.' + table)
            assert not await database[0].fetchval("SELECT has_function_privilege($1,'life_financial.funding_verified(uuid)','EXECUTE')", role)
        repo.swap.capital.max_bytes = 1
        with pytest.raises(StorageError, match='FUNDING_READ_BOUND'):
            await repo.frame(cfg.binding)
        repo.swap.capital.max_bytes = 1048576
        await database[0].execute('ALTER TABLE life_financial.funding_accounting_policies DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.funding_accounting_policies SET max_total_bytes=1')
        await database[0].execute('ALTER TABLE life_financial.funding_accounting_policies ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='FUNDING_OBSERVATION_BOUND'):
            await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not financial._runtime.healthy
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await repo.lookup(command.operation_id, cfg.binding) is None
    finally:
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['-.1', '.2'])
async def test_actual_file_joint_cash_writer_and_native_paid_bill_have_identical_balance_and_dedup(database, tmp_path, amount):
    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits
    values, repo, command = await setup(database, amount=amount)
    financial, cfg, owner = values[:3]
    try:
        file_ledger = ReservationLedger(life_balance=D('5'), usdt_balance=D('10'),
                                        limits=RiskLimits(D('0'), D('100'), D('1000'), D('100')),
                                        path=tmp_path / 'wallet.json')
        payment = command.bundle.funding.payments[0]
        key = f'JOINT:{payment.settled_at_ms}:FUNDING:{payment.bill_id}'
        assert file_ledger.record_joint_cash(key, payment.signed_payment_quote)
        assert not file_ledger.record_joint_cash(key, payment.signed_payment_quote)
        assert (await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        current = await financial.balance(cfg.binding)
        assert current.total_base_life == file_ledger.life_balance and current.total_quote_usdt == file_ledger.usdt_balance
        frame = await repo.frame(cfg.binding)
        assert {f"JOINT:{p['settled_at_ms']}:FUNDING:{p['bill_id']}": D(p['signed_payment_usdt']) for p in frame['payments']} == file_ledger.joint_cash_events
        assert file_ledger.cashflow_events == {}
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_changed_receipt_context_after_commit_requires_original_bill_readback(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await original(tx)
            repo.binding = replace(cfg.binding, config_hash='f' * 64)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not financial._runtime.healthy
        restored = PgFundingRepository(repo.swap)
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
    finally:
        await financial.close()
