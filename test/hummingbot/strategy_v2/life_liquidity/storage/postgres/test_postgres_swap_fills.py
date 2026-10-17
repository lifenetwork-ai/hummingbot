"""Native derivative financial application; every value here is synthetic."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap import setup as swap_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
    FillSwap,
    PgSwapFillRepository,
    SwapFillObservation,
)


async def setup(database, *, acknowledge=True, carry_options=None):
    values, swap, prepared = await swap_setup(database, carry_options=carry_options)
    financial, cfg, owner = values[:3]
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                              cfg.account_id, cfg.binding.policy_hash, owner.valid_from - timedelta(seconds=1))
    await database[0].execute('''INSERT INTO life_financial.swap_fill_policies
        VALUES($1,$2,0,0,0,0,64,128,500)''', cfg.account_id, cfg.binding.policy_hash)
    assert await database[0].fetchval('SELECT life_financial.swap_accounting_verified($1)', cfg.account_id)
    await swap.prepare(prepared, cut=await swap.cut(cfg.binding), owner=owner)
    await swap.arm(prepared.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
    if acknowledge:
        await swap.acknowledge(prepared.operation_id, exchange_order_id='987654321', owner=owner)
    received = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(received.timestamp() * 1000)
    fill = SwapFillObservation('trade1', D('1'), D('1.01'), 'USDT', D('-.01'), at, D('1.01'), at, at,
                               'independent_market', D('0'), None)
    command = FillSwap('fill1', prepared.operation_id, 'swap1', 'SwapWire1', 'LIFE-USDT', 'SELL', str(owner.session_id),
                       owner.epoch, '987654321', D('1'), (fill,), received)
    return values, swap, PgSwapFillRepository(swap), command


@pytest.mark.asyncio
async def test_swap_fill_records_fee_and_basis_once_without_spot_principal_or_claim_release(database):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        before = await financial.balance(cfg.binding)
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await repo.apply_fills(command, cut=cut, owner=owner)
        assert result.state == 'APPLIED' and result.atomic_scope is AtomicScope.ACCOUNT and not result.trading_permission
        after = await financial.balance(cfg.binding)
        assert after.total_base_life == before.total_base_life and after.total_quote_usdt == before.total_quote_usdt - D('.01')
        assert (await repo.apply_fills(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        frame = await repo.frame(cfg.binding)
        assert D(frame['replay']['short_base']) == 1 and D(frame['replay']['short_entry']) == D('1.01')
        assert D(frame['replay']['execution_loss_quote']) == D('.01')
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await swap.lookup('prepare1', cfg.binding)).state == 'PREPARED'  # Historical receipt never advances.
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.fills WHERE product='SWAP'") == 1
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
        view = await swap.capital._read('SELECT life_financial.spot_state($1)', (cfg.account_id,))
        assert view['unverified'] is False
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'quantity', 'receipt', 'financial_event', 'missing_loss'])
async def test_corrupt_derivative_money_projection_blocks_cold_read_and_new_risk(database, fault):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        queries = dict(
            fee="UPDATE life_financial.fees SET signed_fee=-.02 WHERE product='SWAP'",
            quantity="UPDATE life_financial.fills SET quantity=.5 WHERE product='SWAP'",
            receipt="UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{state}','\"BROKEN\"') WHERE operation_id='fill1'",
            financial_event="UPDATE life_financial.financial_events SET quote_delta=10 WHERE product='SWAP'",
            missing_loss="DELETE FROM life_financial.budget_events WHERE family='LOSS' AND scope='SESSION'",
        )
        # Disable only the migrator-owned immutability triggers in the selected table.
        table = dict(fee='fees', quantity='fills', receipt='operations', financial_event='financial_events', missing_loss='budget_events')[fault]
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(queries[fault])
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='SWAP_ACCOUNTING_UNVERIFIED'):
            await repo.frame(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_changed_permanent_trade_or_incomplete_cumulative_history_cannot_credit_money(database):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.balance(cfg.binding)
        changed = replace(command, operation_id='changed', fills=(replace(command.fills[0], signed_fee=D('.01')),))
        result = await repo.apply_fills(changed, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED' and not result.trading_permission
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert len((await repo.frame(cfg.binding))['pending']) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_late_fill_after_halt_is_accounted_and_does_not_restore_admission(database):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute("UPDATE life_financial.accounts SET halted=true,mode='RECOVERY_ONLY' WHERE account_id=$1", cfg.account_id)
        assert (await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_cumulative_partial_fills_change_only_new_money_and_preserve_original_history(database):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        first_fill = replace(command.fills[0], quantity_base_life=D('.5'), signed_fee=D('-.005'), sequence=1)
        first = replace(command, fills=(first_fill,), cumulative_base_life=D('.5'))
        assert (await repo.apply_fills(first, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        second_fill = replace(first_fill, trade_id='trade2', price_usdt=D('1.03'), independent_price_usdt=D('1.03'),
                              fill_at_ms=at, independent_value_at_ms=at, independent_observed_at_ms=at, sequence=2)
        second = replace(command, operation_id='fill2', fills=(first_fill, second_fill), observed_at=received)
        assert (await repo.apply_fills(second, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        before = await financial.balance(cfg.binding)
        duplicate = replace(second, operation_id='fill3')
        assert (await repo.apply_fills(duplicate, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert await financial.balance(cfg.binding) == before
        assert before.total_quote_usdt == D('9.99')
        frame = await repo.frame(cfg.binding)
        assert D(frame['replay']['short_base']) == 1 and D(frame['replay']['short_entry']) == D('1.02')
        assert len(frame['fills']) == 2
        assert (await repo.lookup('fill1', cfg.binding)).cumulative_base_life == D('.5')
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
        incomplete = replace(second, operation_id='incomplete', cumulative_base_life=D('.5'), fills=(second_fill,))
        assert (await repo.apply_fills(incomplete, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        assert await financial.balance(cfg.binding) == before
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_zero_fee_open_revokes_old_position_snapshot_until_current_facts_agree(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    values, swap, repo, command = await setup(database)
    financial, cfg, owner, _, feed, capital, _, carry_feed, carry = values
    try:
        command = replace(command, fills=(replace(command.fills[0], signed_fee=D('0')),))
        assert (await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await financial.snapshot(cfg.binding)).recovery_required
        candidate = native_claim(owner, 'next', product='SWAP', side='SELL', amount='1')
        with pytest.raises(StorageError, match='SWAP_ACCOUNT_SNAPSHOT_MISMATCH'):
            await capital.reserve(ReserveCapital('stale', candidate), await capital.cut(cfg.binding), owner)
        assert not financial._runtime.healthy
        assert len(await capital.claims(cfg.binding)) == 1
        now = await database[0].fetchval('SELECT clock_timestamp()')
        feed[0] = replace(feed[0], sequence=2, observed_at_ms=int(now.timestamp() * 1000), short_base=D('1'))
        assert (await capital.check_capital(cfg.binding)).allowed
        b, at_ms = carry_feed[0], feed[0].observed_at_ms
        carry_feed[0] = replace(b, account=replace(b.account, snapshot=feed[0]),
                                mark=replace(b.mark, sequence=2, observed_at_ms=at_ms),
                                index=replace(b.index, sequence=2, observed_at_ms=at_ms),
                                funding=replace(b.funding, sequence=2, observed_at_ms=at_ms, coverage_through_ms=at_ms),
                                terms=replace(b.terms, sequence=2, observed_at_ms=at_ms))
        assert (await carry.check_carry(cfg.binding)).allowed
        assert not (await financial.snapshot(cfg.binding)).recovery_required
        assert not financial._runtime.healthy  # Diagnostic refresh never heals the local source revocation.
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('epoch', 1.0), ('epoch', True), ('epoch', '1'), ('cumulative_base_life', 1)])
async def test_native_swap_fill_wire_types_cannot_be_coerced(database, field, value):
    import hashlib
    import json
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        version, digest = financial._cut(cut, owner)
        body = {**command.json(), field: value}
        request = dict(kind='SWAP_FILL', binding=cfg.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        identity = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with pytest.raises(StorageError, match='SWAP_FILL_COMMAND_INVALID'):
            await financial._runtime._transaction('SELECT life_financial.swap_fill_command($1,$2,$3,$4,$5,$6,$7)',
                                                  (cfg.account_id, cfg.binding.json(), owner.json(), version, digest, identity, body),
                                                  owner, repo._receipt, observation=True)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_swap_fill_commit_is_recovered_by_original_readback_without_fee_reapplication(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fill_process import exit_swap_fill

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        if fault == 'process':
            await exit_swap_fill(cfg, command, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic SWAP fill COMMIT reply loss')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.apply_fills(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgSwapFillRepository(PgSwapRepository(capital))
        found = await restored.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (D('9.99') if committed else D('10'))
        if not committed:
            assert (await restored.apply_fills(command, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await restored.apply_fills(command, cut=cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.99')
        assert len((await restored.frame(cfg.binding))['fills']) == 1
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_swap_fill_commit_rechecks_original_owner_after_financial_writes(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    original = financial._runtime._commit
    try:
        before = await financial.balance(cfg.binding)
        cut = (await financial.snapshot(cfg.binding)).cut

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.apply_fills(command, cut=cut, owner=owner)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert await financial.balance(cfg.binding) == before
        assert len((await repo.frame(cfg.binding))['fills']) == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,reason', [
    (dict(signed_fee=D('-11')), 'SWAP_NEGATIVE_WALLET_UNSUPPORTED'),
    (dict(realized_pnl_usdt=D('1')), 'JOINT_OPEN_PNL_INVALID'),
    (dict(quantity_base_life=D('.333')), 'SWAP_FILL_INVALID'),
])
async def test_unapplicable_swap_facts_are_durably_retained_without_partial_money_or_budget(database, changes, reason):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        bad_fill = replace(command.fills[0], **changes)
        command = replace(command, fills=(bad_fill,), cumulative_base_life=bad_fill.quantity_base_life)
        before = await financial.balance(cfg.binding)
        receipt = await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'RECOVERY_REQUIRED'
        frame = await repo.frame(cfg.binding)
        assert frame['fills'] == [] and frame['pending'][0]['reason'] == reason
        assert frame['pending'][0]['command'] == command.json()
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.budget_events') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_fill_before_ack_never_acks_or_rearms_and_late_ack_must_match_observed_order(database):
    values, swap, repo, command = await setup(database, acknowledge=False)
    financial, cfg, owner = values[:3]
    try:
        assert (await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        row = await database[0].fetchrow("SELECT state,exchange_order_id FROM life_financial.send_attempts WHERE intent_id='swap1'")
        assert row['state'] == 'SEND_UNKNOWN' and row['exchange_order_id'] is None
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError, match='SWAP_ACCOUNTING_UNVERIFIED_AT_COMMIT'):
            await swap.acknowledge('prepare1', exchange_order_id='999999', owner=owner)
        assert (await swap.acknowledge('prepare1', exchange_order_id=command.exchange_order_id, owner=owner)).state == 'ACKED'
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required  # Account/carry still require refresh.
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_close_credits_only_replayed_realized_pnl_and_actual_fee_without_releasing_claims(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.core.data_type.common import PositionAction, TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
    values, swap, repo, command = await setup(database)
    financial, cfg, owner, _, feed, capital, _, carry_feed, carry = values
    try:
        await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at_ms = int(now.timestamp() * 1000)
        feed[0] = replace(feed[0], sequence=2, observed_at_ms=at_ms, short_base=D('1'), spot_cash_quote=D('9.99'))
        assert (await capital.check_capital(cfg.binding)).allowed
        closing_claim = replace(native_claim(owner, 'closing', product='SWAP', side='BUY', action='CLOSE', amount='1'), wire_id='ClosingWire')
        assert (await capital.reserve(ReserveCapital('closing-claim', closing_claim), await capital.cut(cfg.binding), owner)).decision.allowed
        b = carry_feed[0]
        carry_feed[0] = replace(b, account=replace(b.account, snapshot=feed[0]), mark=replace(b.mark, sequence=2, observed_at_ms=at_ms),
                                index=replace(b.index, sequence=2, observed_at_ms=at_ms),
                                funding=replace(b.funding, sequence=2, observed_at_ms=at_ms, coverage_through_ms=at_ms),
                                terms=replace(b.terms, sequence=2, observed_at_ms=at_ms))
        assert (await carry.check_carry(cfg.binding)).allowed
        config = OrderExecutorConfig(id='closing', timestamp=at_ms / 1000, connector_name='okx_perpetual', trading_pair='LIFE-USDT',
                                     side=TradeType.BUY, position_action=PositionAction.CLOSE, amount=D('1'), price=D('1'), leverage=2,
                                     level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        prepared = PrepareSwap.from_config('closing-prepare', 'ClosingWire', config)
        await swap.prepare(prepared, cut=await swap.cut(cfg.binding), owner=owner)
        await swap.arm(prepared.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
        await swap.acknowledge(prepared.operation_id, exchange_order_id='12345678', owner=owner)
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        close_fill = replace(command.fills[0], trade_id='close', price_usdt=D('1'), independent_price_usdt=D('1'),
                             signed_fee=D('-.005'), realized_pnl_usdt=D('.01'), fill_at_ms=at,
                             independent_value_at_ms=at, independent_observed_at_ms=at)
        closing = replace(command, operation_id='closing-fill', prepare_operation_id=prepared.operation_id, intent_id='closing',
                          client_order_id='ClosingWire', side='BUY', exchange_order_id='12345678', fills=(close_fill,), observed_at=received)
        assert (await repo.apply_fills(closing, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        frame = await repo.frame(cfg.binding)
        assert D(frame['replay']['short_base']) == 0 and D(frame['replay']['long_base']) == 0
        assert D(frame['replay']['realized_pnl_quote']) == D('.01') and D(frame['replay']['execution_loss_quote']) == D('.015')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.995')
        assert len(await capital.claims(cfg.binding)) == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_swap_account_cannot_publish_spot_only_nav_as_complete_portfolio_value(database):
    from hummingbot.strategy_v2.life_liquidity.storage.capital import ObserveSpotNAV
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,64)', cfg.account_id, cfg.binding.policy_hash)
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        nav = ObserveSpotNAV('spot-nav', D('1'), at, at, 'independent_market', received)
        with pytest.raises(StorageError, match='PORTFOLIO_NAV_REQUIRED'):
            await financial.observe_nav(nav, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_nav_observations') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_swap_fill_read_bounds_and_role_scope_never_expose_a_private_financial_writer(database):
    import asyncpg
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        assert await reporter.fetchval('SELECT life_financial.swap_fill_read($1,64,1048576,NULL)', cfg.account_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('SELECT life_financial.swap_fill_command($1,NULL,NULL,0,NULL,NULL,NULL)', cfg.account_id)
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            for table in ('swap_fill_policies', 'swap_fill_facts', 'swap_fill_progress', 'swap_pending_fills'):
                assert not await database[0].fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", role, 'life_financial.' + table)
            for signature in ('swap_accounting_verified(uuid)', 'swap_fill_replay(uuid)', 'swap_snapshot_current(uuid,jsonb)',
                              'swap_position_replay(jsonb,jsonb,jsonb,text,bigint,integer)'):
                assert not await database[0].fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, 'life_financial.' + signature)
        repo.swap.capital.max_bytes = 1
        with pytest.raises(StorageError, match='SWAP_FILL_READ_BOUND'):
            await repo.frame(cfg.binding)
        repo.swap.capital.max_bytes = 1048576
        await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_SWAP_FILL_READ_BOUND_INVALID'):
            await reporter.fetchval('SELECT life_financial.swap_fill_read($1,0,1048576,NULL)', cfg.account_id)
    finally:
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
async def test_exhausted_explicit_observation_budget_revokes_new_risk_without_rewriting_original_receipts(database):
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute('ALTER TABLE life_financial.swap_fill_policies DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.swap_fill_policies SET max_observations=1')
        await database[0].execute('ALTER TABLE life_financial.swap_fill_policies ENABLE TRIGGER USER')
        cut = (await financial.snapshot(cfg.binding)).cut
        assert (await repo.apply_fills(command, cut=cut, owner=owner)).state == 'APPLIED'
        with pytest.raises(StorageError, match='SWAP_FILL_OBSERVATION_BOUND'):
            await repo.apply_fills(replace(command, operation_id='next'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not financial._runtime.healthy
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert (await repo.apply_fills(command, cut=cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.99')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_changed_fill_receipt_context_after_commit_preserves_original_fee_identity(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values, swap, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await original(tx)
            repo.binding = replace(cfg.binding, config_hash='f' * 64)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.apply_fills(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not financial._runtime.healthy
        restored = PgSwapFillRepository(swap)
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.99')
    finally:
        await financial.close()
