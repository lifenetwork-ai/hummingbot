"""One account transaction over synthetic original financial commands and source."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository


async def setup(database, *, carry_options=None, funding_budget='10', spot_options=None):
    values, funding, payment = await funding_setup(database, carry_options=carry_options, budget=funding_budget, spot_options=spot_options)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await database[0].execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)',
                              cfg.account_id, cfg.binding.policy_hash)
    # Original source covers BOTH paid funding and the original derivative fee.
    # Do not fabricate intermediate wallet or positions for standalone writers.
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
    received = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(received.timestamp() * 1000)
    fill = SwapFillObservation('trade1', D('1'), D('1.01'), 'USDT', D('-.01'), at, D('1.01'), at, at,
                               'independent_market', D('0'), None)
    trade = FillSwap('fill1', 'prepare1', 'swap1', 'SwapWire1', 'LIFE-USDT', 'SELL', str(owner.session_id),
                     owner.epoch, '987654321', D('1'), (fill,), received)
    b = payment.bundle
    snapshot = replace(b.account.snapshot, observed_at_ms=at, spot_cash_quote=D('9.89'), short_base=D('1'))
    b = replace(b, account=replace(b.account, snapshot=snapshot),
                mark=replace(b.mark, observed_at_ms=at), index=replace(b.index, observed_at_ms=at),
                funding=replace(b.funding, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, observed_at_ms=at))
    return values, PgJointBatchRepository(funding.swap), ApplyJointBatch('batch1', (), (trade,), (), replace(payment, bundle=b))


@pytest.mark.asyncio
async def test_joint_fill_and_funding_apply_once_from_one_unchanged_final_source(database):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        assert ApplyJointBatch.from_json(command.json()) == command
        receipt = await repo.apply(command, cut=cut, owner=owner)
        assert receipt.state == 'APPLIED' and receipt.atomic_scope is AtomicScope.ACCOUNT and not receipt.trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert receipt.cash_delta_usdt == D('-.11') and receipt.base_delta_life == 0
        assert (await repo.apply(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        frame = await repo.frame(cfg.binding)
        assert len(frame['batches']) == 1 and frame['batches'][0]['command'] == command.json()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await financial.snapshot(cfg.binding)).recovery_required  # Explicit source refresh, never auto-activation.
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['cash', 'position', 'funding_scope', 'pnl'])
async def test_joint_failure_retains_all_originals_and_halt_without_partial_money(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = command.funding.bundle
        if fault == 'cash':
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('11'))))
        elif fault == 'position':
            b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, short_base=D('0'))))
        elif fault == 'funding_scope':
            b = replace(b, funding=replace(b.funding, scope_complete=False))
        else:
            trade = command.swap_fills[0]
            command = replace(command, swap_fills=(replace(trade, fills=(replace(trade.fills[0], realized_pnl_usdt=D('10')),)),))
        command = replace(command, funding=replace(command.funding, bundle=b))
        before = await financial.balance(cfg.binding)
        receipt = await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'RECOVERY_REQUIRED' and receipt.cash_delta_usdt == 0
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.budget_events') == 0
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await repo.frame(cfg.binding))['batches'][0]['command'] == command.json()
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_all_four_financial_families_share_one_cut_and_keep_gross_costs_and_claims(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import ReserveCapital
    values, repo, cmd = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        candidate = replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')
        await values[5].reserve(ReserveCapital('spotclaim', candidate), await values[5].cut(cfg.binding), owner)
        await values[8].check_carry(cfg.binding)
        await financial.prepare(order(owner, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002')),
                                cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        spot = filled(owner, received, operation_id='spotfill')
        spot = replace(spot, fills=(replace(spot.fills[0], signed_fee=D('-.001'), independent_price_usdt=D('1')),))
        cashflow = flow(received, at)
        await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                                  cfg.account_id, cashflow.bill_id, cashflow.currency, cashflow.signed_amount, at, b'synthetic-preverified-import')
        b = cmd.funding.bundle
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, observed_at_ms=at, spot_cash_quote=D('10.889'), spot_life_base=D('6'))),
                    mark=replace(b.mark, observed_at_ms=at), index=replace(b.index, observed_at_ms=at),
                    funding=replace(b.funding, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, observed_at_ms=at))
        cmd = replace(cmd, spot_fills=(spot,), cashflows=(cashflow,), funding=replace(cmd.funding, bundle=b))
        result = await repo.apply(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED', await database[0].fetchval('SELECT reason FROM life_financial.joint_financial_batches')
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (D('6'), D('10.889'))
        assert result.base_delta_life == 1 and result.cash_delta_usdt == D('.889')
        assert len(await values[5].claims(cfg.binding)) == 2
        budget = (await financial.snapshot(cfg.binding)).budget
        assert budget.session_execution_loss_usdt == D('.011') and budget.session_subsidy_committed_usdt == D('.1')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
        assert await database[0].fetchval('SELECT life_financial.joint_batch_verified($1)', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('funding_first', [True, False])
async def test_original_economic_time_not_member_invocation_order_decides_cash_financing(database, funding_first):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
    values, repo, cmd = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = cmd.funding.bundle
        early = b.funding.payments[0].settled_at_ms
        late = int(cmd.swap_fills[0].observed_at.timestamp() * 1000)
        # The two events need distinct economic milliseconds, all before receipt.
        import asyncio
        if late <= early + 1:
            await asyncio.sleep(.005)
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        cash_at = early + 1
        payment = replace(b.funding.payments[0], signed_payment_quote=D('.2'), settled_at_ms=early if funding_first else at)
        cashflow = flow(received, cash_at, signed_amount=D('-10.05'))
        await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                                  cfg.account_id, cashflow.bill_id, cashflow.currency, cashflow.signed_amount, cash_at, b'synthetic-preverified-import')
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, observed_at_ms=at, spot_cash_quote=D('.14'))),
                    mark=replace(b.mark, observed_at_ms=at), index=replace(b.index, observed_at_ms=at),
                    funding=replace(b.funding, observed_at_ms=at, coverage_through_ms=at, payments=(payment,)), terms=replace(b.terms, observed_at_ms=at))
        cmd = replace(cmd, cashflows=(cashflow,), funding=replace(cmd.funding, bundle=b))
        result = await repo.apply(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == ('APPLIED' if funding_first else 'RECOVERY_REQUIRED')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == (D('.14') if funding_first else D('10'))
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_previously_applied_member_is_linked_without_recredit_or_replacing_its_original_receipt(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import PgSwapFillRepository
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        trade = command.swap_fills[0]
        fills = PgSwapFillRepository(repo.swap)
        original = await fills.apply_fills(trade, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED' and result.cash_delta_usdt == D('-.1')
        assert await fills.lookup(trade.operation_id, cfg.binding) == replace(original, replayed=True)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert len((await repo.frame(cfg.binding))['batches'][0]['members']) == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_batch_commit_loss_cold_readback_has_all_money_and_members_or_none(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch_process import exit_joint_batch

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
            await exit_joint_batch(cfg, command, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic joint batch COMMIT loss')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.apply(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgJointBatchRepository(PgSwapRepository(capital))
        assert ((await restored.lookup(command.operation_id, cfg.binding)) is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (D('9.89') if committed else D('10'))
        assert await admin.fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
        if not committed:
            assert (await restored.apply(command, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await restored.apply(command, cut=cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert await admin.fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await admin.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_pending_originals_cannot_be_bypassed_by_a_new_member_id_or_new_batch(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import PgSwapFillRepository
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        bad = replace(command, funding=replace(command.funding, bundle=replace(command.funding.bundle,
                      account=replace(command.funding.bundle.account, snapshot=replace(command.funding.bundle.account.snapshot, spot_cash_quote=D('11'))))))
        cut = (await financial.snapshot(cfg.binding)).cut
        assert (await repo.apply(bad, cut=cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError, match='PG_JOINT_PRIOR_PENDING'):
            await PgSwapFillRepository(repo.swap).apply_fills(replace(command.swap_fills[0], operation_id='bypass'),
                                                              cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.apply(replace(command, operation_id='newbatch'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        assert (await repo.apply(bad, cut=cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert len((await repo.frame(cfg.binding))['batches']) == 2
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source', 'member_receipt', 'money', 'missing_member'])
async def test_corrupt_batch_source_or_members_denies_cold_readback_and_account_readiness(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        assert (await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        table = 'joint_financial_batches'
        queries = dict(source="UPDATE life_financial.joint_financial_batches SET command=jsonb_set(command,'{funding,bundle,account,snapshot,spot_cash_quote}','\"11\"')",
                       member_receipt="UPDATE life_financial.joint_financial_batches SET members=jsonb_set(members,'{0,receipt,state}','\"PENDING\"')",
                       money="UPDATE life_financial.joint_financial_batches SET money_after=jsonb_set(money_after,'{quote}','\"11\"')",
                       missing_member="UPDATE life_financial.joint_financial_batches SET members=members-0")
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(queries[fault])
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_private_stage_kernels_and_context_cannot_bypass_public_transaction_or_bounds(database):
    import asyncpg
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        for connection in (runtime, reporter):
            for query in ('SELECT * FROM life_financial.joint_batch_staging', 'DELETE FROM life_financial.joint_financial_batches',
                          'SELECT life_financial.joint_stage_guard(NULL,NULL,NULL)', 'SELECT life_financial.joint_batch_verified(NULL)'):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(query)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('SELECT life_financial.joint_batch_command(NULL,NULL,NULL,NULL,NULL,NULL,NULL)')
        with pytest.raises(asyncpg.RaiseError, match='LIFE_JOINT_STAGE_CAPABILITY_INVALID'):
            await database[0].execute("SELECT life_financial.joint_stage_guard($1,'SWAP_FILL','{}')", cfg.account_id)
        await database[0].execute('ALTER TABLE life_financial.joint_batch_policies DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.joint_batch_policies SET max_total_bytes=1')
        await database[0].execute('ALTER TABLE life_financial.joint_batch_policies ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_BOUND'):
            await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await runtime.close()
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
async def test_owner_change_at_physical_commit_rolls_back_every_member_and_original_identity(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    original = repo.runtime._commit
    try:
        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_changed_receipt_context_after_commit_requires_original_batch_readback(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    original = repo.runtime._commit
    try:
        async def changed(tx):
            await original(tx)
            repo.binding = replace(cfg.binding, config_hash='f' * 64)

        monkeypatch.setattr(repo.runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        restored = PgJointBatchRepository(repo.swap)
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_final_original_source_must_still_be_fresh_at_physical_batch_commit(database, monkeypatch):
    import asyncio
    values, repo, command = await setup(database, carry_options=dict(max_age_ms=2000))
    financial, cfg, owner = values[:3]
    original = repo.runtime._commit
    source_ms = min(command.funding.bundle.account.snapshot.observed_at_ms, command.funding.bundle.mark.observed_at_ms,
                    command.funding.bundle.index.observed_at_ms, command.funding.bundle.funding.observed_at_ms, command.funding.bundle.terms.observed_at_ms)
    try:
        async def expired(tx):
            staged = await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='batch1'")
            assert staged == 'APPLIED', 'source must be accepted before injecting COMMIT expiry'
            now = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            await asyncio.sleep(max(0, (source_ms + 2000 - now) / 1000) + .1)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', expired)
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_SOURCE_EXPIRED_AT_COMMIT'):
            await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_staging_capability_cannot_survive_a_transaction_without_final_publication(database):
    import asyncpg
    values, repo, _ = await setup(database)
    financial, cfg, _ = values[:3]
    try:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_JOINT_STAGE_STILL_APPLYING'):
            async with database[0].transaction():
                await database[0].execute("INSERT INTO life_financial.joint_batch_staging VALUES($1,pg_current_xact_id(),'[]')", cfg.account_id)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
        assert await repo.lookup('batch1', cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fee,funding', [('-.01', '-.1'), ('.01', '.1'), ('0', '0')])
async def test_coupled_native_cash_matches_actual_file_cash_writer_without_reapplying_ids(database, tmp_path, fee, funding):
    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        original_trade = command.swap_fills[0]
        trade = replace(original_trade.fills[0], signed_fee=D(fee))
        original_bundle = command.funding.bundle
        payment = replace(original_bundle.funding.payments[0], signed_payment_quote=D(funding))
        expected = D('10') + D(fee) + D(funding)
        source = replace(original_bundle, account=replace(original_bundle.account,
                         snapshot=replace(original_bundle.account.snapshot, spot_cash_quote=expected)),
                         funding=replace(original_bundle.funding, payments=(payment,)))
        command = replace(command, swap_fills=(replace(original_trade, fills=(trade,)),), funding=replace(command.funding, bundle=source))
        ledger = ReservationLedger(
            life_balance=D('5'), usdt_balance=D('10'),
            limits=RiskLimits(D('0'), D('100'), D('1000'), D('100')), path=tmp_path / 'cash.json')
        events = {f'JOINT:{trade.fill_at_ms}:TRADE:{trade.trade_id}': trade.signed_fee,
                  f'JOINT:{payment.settled_at_ms}:FUNDING:{payment.bill_id}': payment.signed_payment_quote}
        for key in sorted(events):
            assert ledger.record_joint_cash(key, events[key])
            assert not ledger.record_joint_cash(key, events[key])
        cut = (await financial.snapshot(cfg.binding)).cut
        assert (await repo.apply(command, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await repo.apply(command, cut=cut, owner=owner)).replayed
        balance = await financial.balance(cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (ledger.life_balance, ledger.usdt_balance)
        assert ledger.joint_cash_events == events and ledger.cashflow_events == {}
        assert (await financial.snapshot(cfg.binding)).budget.session_execution_loss_usdt == max(D('0'), -D(fee))
        assert await database[0].fetchval('SELECT gross_debits_usdt FROM life_financial.funding_observations') == max(D('0'), -D(funding))
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_batch_refresh_nav_terminal_release_and_cold_readback_preserve_one_financial_history(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
        PgSharedReleaseRepository,
        ReleaseSharedClaim,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
        ConfirmSwapTerminal,
        PgSwapTerminalRepository,
    )
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        original_cut = (await financial.snapshot(cfg.binding)).cut
        assert (await repo.apply(command, cut=original_cut, owner=owner)).state == 'APPLIED'
        values[7][0] = command.funding.bundle
        await refresh(database, values, short='1', cash='9.89')
        portfolio = PgPortfolioRepository(repo.swap)
        await portfolio.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        prepared = await database[0].fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
        terminal = ConfirmSwapTerminal('terminal1', 'prepare1', '987654321', 'filled', D('1'), ('trade1',),
                                       values[4][0].observed_at_ms, prepared, 'okx_joint_reconciled')
        assert (await PgSwapTerminalRepository(repo.swap).confirm(terminal, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
        release = ReleaseSharedClaim('release1', 'swap1', terminal.operation_id)
        before = await financial.snapshot(cfg.binding)
        release_cut = before.cut
        assert (await PgSharedReleaseRepository(repo.swap.capital).release(release, cut=release_cut, owner=owner)).released
        assert await values[5].claims(cfg.binding) == ()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgJointBatchRepository(PgSwapRepository(capital))
        assert (await restored.apply(command, cut=original_cut, owner=owner)).replayed
        assert (await PgSharedReleaseRepository(capital).release(release, cut=release_cut, owner=owner)).replayed
        assert await cold.balance(cfg.binding) == before.balance
        assert (await cold.snapshot(cfg.binding)).budget == before.budget
        assert await capital.claims(cfg.binding) == ()
        assert await database[0].fetchval("SELECT (life_financial.swap_fill_replay($1)->>'short_base')::numeric", cfg.account_id) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_batch_staging') == 0
    finally:
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_retained_envelope_bound_counts_original_member_requests_and_receipts_and_rolls_back_money(database):
    import json
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        # A raw command fits, while its immutable requests/receipts/both cuts do
        # not. This must reject the WHOLE transaction, including staged members.
        command_bytes = await database[0].fetchval('SELECT octet_length(($1::jsonb)::text)', json.dumps(command.json()))
        await database[0].execute('ALTER TABLE life_financial.joint_batch_policies DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.joint_batch_policies SET max_total_bytes=$1', command_bytes + 1)
        await database[0].execute('ALTER TABLE life_financial.joint_batch_policies ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_BOUND'):
            await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('swap_fill_facts', 'funding_cash_payments', 'joint_financial_batches', 'joint_batch_staging', 'budget_events'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_financial.{table}') == 0
    finally:
        await financial.close()
