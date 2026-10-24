"""Standalone SPOT settlement over real PostgreSQL with synthetic scoped facts."""
import os
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import PgInventorySettlementRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


async def cycle(database, *, sell_price=D('1'), fee_currency='USDT', sell_quantity=D('1'), sell_reference=D('.9'), buy_reference=D('.9'), usdt_fee=D('-.02'), max_age_ms=1000, close_last=True, financial_values=None):
    repo, cfg, owner = await spot(database) if financial_values is None else financial_values
    c = database[0]
    await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,$3)', cfg.account_id, cfg.binding.policy_hash, max_age_ms)
    fills = []
    for index, side in enumerate(('BUY', 'SELL'), 1):
        suffix = str(index)
        price = D('1') if index == 1 else sell_price
        prepared = order(owner, operation_id='operation' + suffix, intent_id='intent' + suffix, client_order_id='wire' + suffix,
                         side=side, limit_price_usdt=price, subsidy_quote_usdt=D('.5'), fee_buffer_quote_usdt=D('.1'), fee_buffer_base_life=D('.1'))
        await repo.prepare(prepared, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm(prepared.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        observed = await repo.observation_cut(cfg.binding)
        at = observed.observed_at
        ms = int(at.timestamp() * 1000)
        fee = usdt_fee if fee_currency == 'USDT' else D('-.01') if index == 1 else D('0')
        row = SpotFillObservation('trade' + suffix, D('1') if index == 1 else sell_quantity, price, fee_currency, fee, ms,
                                  buy_reference if index == 1 else sell_reference, ms, ms, 'independent_market')
        fills.append(row)
        command = FillSpot('fill' + suffix, prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market,
                           side, prepared.session_id, prepared.epoch, str(123456788 + index), row.quantity_base_life, (row,), at)
        await repo.apply_fills(command, cut=observed.cut, owner=owner)
        if index == 2 and not close_last:
            continue
        observed = await repo.observation_cut(cfg.binding)
        view = await repo.snapshot(cfg.binding)
        facts = SpotTerminalFacts(cfg.binding, observed.observed_at, 'CANCELED', row.quantity_base_life, prepared.quantity_base,
                                  prepared.limit_price_usdt, (row,), view.balance.total_base_life, view.balance.total_quote_usdt,
                                  D('0'), D('0'), tuple(fill.trade_id for fill in fills), (), (), (), (), True, True, True)
        terminal = TerminalSpot('terminal' + suffix, prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                                prepared.market, side, prepared.session_id, prepared.epoch, str(123456788 + index), facts)
        await repo.confirm_terminal(terminal, cut=view.cut, owner=owner)
    return repo, cfg, owner, PgInventorySettlementRepository(repo)


async def facts(repo):
    view = await repo.snapshot(repo.binding)
    at = (await repo.observation_cut(repo.binding)).observed_at
    return SpotFinancialRecoveryFacts(repo.binding, at, view.balance.total_base_life, view.balance.total_quote_usdt,
                                      D('0'), D('0'), ('trade1', 'trade2'), (), (), (), True, True, True)


def command(**changes):
    return replace(SettleSpotInventory('settle1', 'cycle1', ('intent1', 'intent2')), **changes)


@pytest.mark.asyncio
async def test_exact_flat_cycle_spends_actual_subsidy_once_without_refunding_execution_loss(database):
    repo, cfg, owner, settlement = await cycle(database)
    try:
        before = await repo.snapshot(cfg.binding)
        result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert result.state == 'SETTLED' and not result.replayed
        assert dict(result.actuals_usdt) == {'intent1': D('.12'), 'intent2': D('0')}
        assert after.balance == before.balance and after.budget.session_subsidy_committed_usdt == D('.12')
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt == D('.12')
        assert (await settlement.settle_inventory(command(), facts=await facts(repo), cut=after.cut, owner=owner)).replayed
        assert (await settlement.lookup_inventory_settlement('settle1', cfg.binding)).proof_digest == result.proof_digest
        assert (await repo.snapshot(cfg.binding)).cut == after.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('asset,quantity,allowed', [('USDT', D('.9'), False), ('LIFE', D('1'), False), ('LIFE', D('.99'), True)])
async def test_inventory_cycle_must_be_physically_flat_including_life_fees(database, asset, quantity, allowed):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database, fee_currency=asset, sell_quantity=quantity)
    try:
        before = await repo.snapshot(cfg.binding)
        if allowed:
            result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
            assert result.actuals_usdt['intent1'] == D('.109') and result.actuals_usdt['intent2'] == 0
            assert (await repo.balance(cfg.binding)) == before.balance
        else:
            with pytest.raises(StorageError, match='PG_SETTLEMENT_INVENTORY_NOT_FLAT'):
                await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(order_scope_complete=False), dict(bill_scope_complete=False), dict(runner_scope_complete=False),
                                     dict(account_trade_ids=()), dict(account_cashflow_ids=('foreign',)), dict(accepted_wire_ids=('foreign',)),
                                     dict(runner_wire_ids=('foreign',)), dict(account_base_life=D('99')), dict(account_quote_usdt=D('99')),
                                     dict(liability_base_life=D('.01')), dict(liability_quote_usdt=D('.01'))])
async def test_incomplete_or_conflicting_financial_facts_release_nothing(database, changes):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await settlement.settle_inventory(command(), facts=replace(await facts(repo), **changes), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_settlements') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_reused_member_or_cycle_cannot_refund_subsidy_again(database):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    try:
        await settlement.settle_inventory(command(), facts=await facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        for candidate in (command(operation_id='settle2'), command(operation_id='settle2', cycle_id='cycle2')):
            with pytest.raises(StorageError, match='PG_SETTLEMENT_MEMBER_OR_CYCLE_REUSED'):
                await settlement.settle_inventory(candidate, facts=await facts(repo), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_stale_cut_and_unqualified_member_subset_release_nothing(database):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_SETTLEMENT_INVENTORY_NOT_FLAT'):
            await settlement.settle_inventory(command(intent_ids=('intent1',)), facts=await facts(repo), cut=before.cut, owner=owner)
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        with pytest.raises(StorageError, match='PG_SPOT_CUT_CONFLICT'):
            await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        assert (await repo.budget(cfg.binding)) == before.budget
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('fault', ['process', 'backend'])
async def test_real_commit_fault_recovers_original_cycle_without_budget_refund_or_rearm(database, monkeypatch, committed, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement_process import exit_settlement

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, settlement = await cycle(database, max_age_ms=10000)  # Explicit synthetic process-start allowance.
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        before = await repo.snapshot(cfg.binding)
        evidence = await facts(repo)
        if fault == 'process':
            await exit_settlement(cfg, command(), evidence, owner, committed)
        else:
            original = repo._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic settlement reply lost')
            monkeypatch.setattr(repo._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await settlement.settle_inventory(command(), facts=evidence, cut=before.cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgInventorySettlementRepository(cold)
        found = await restored.lookup_inventory_settlement('settle1', cfg.binding)
        assert (found is not None) is committed
        if not committed:
            with pytest.raises(StorageError, match='PG_SETTLEMENT_RECOVERY_NOT_STARTED'):
                await restored.resume_inventory_settlement(command(), facts=await facts(cold), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
            # Definite original absence precedes fresh facts; economics stay original.
            fresh = await facts(cold)
            assert replace(fresh, observed_at=evidence.observed_at) == evidence
            await restored.settle_inventory(command(), facts=fresh, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        result = await restored.resume_inventory_settlement(command(), facts=evidence, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        after = await cold.snapshot(cfg.binding)
        assert result.replayed and after.balance == before.balance
        assert after.budget.session_subsidy_committed_usdt == D('.12')
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_settlements') == 1
        assert (await cold.attempt('intent1', cfg.binding)).state.value == 'TERMINAL'
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
async def test_generation_change_at_physical_commit_rolls_back_cycle_and_budget_publication(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_members') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_facts_expiring_at_physical_commit_release_nothing(database, monkeypatch):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    if os.environ.get('LIFE_PG_TEST_SETTLEMENT_DIAGNOSTIC') != '1':
        await database[0].execute(Path(__file__).with_name('settlement_commit_diagnostics.sql').read_text())
    original = repo._runtime._commit

    async def expired(tx):
        await asyncio.sleep(1.05)  # Deliberately exceed the unchanged 1,000 ms policy.
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', expired)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT'):
            await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        diagnostic = repo._runtime.diagnostics[-1]
        assert diagnostic.error_phase == 'commit' and diagnostic.reason_code == 'SETTLEMENT_FACTS_EXPIRED'
        assert diagnostic.fact_age_ms > diagnostic.fact_max_age_ms == 1000
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_realized_cycle_loss_cannot_hide_behind_favorable_independent_references(database):
    repo, cfg, owner, settlement = await cycle(database, buy_reference=D('2'), sell_reference=D('.1'), sell_price=D('.5'))
    try:
        before = await repo.snapshot(cfg.binding)
        assert before.budget.session_execution_loss_usdt == 0
        result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        assert dict(result.actuals_usdt) == {'intent1': D('0'), 'intent2': D('.54')}
        after = await repo.snapshot(cfg.binding)
        assert after.budget.session_subsidy_committed_usdt == D('.54')
        assert after.budget.session_execution_loss_usdt == 0 and after.balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_positive_rebates_and_cycle_profit_never_refund_gross_execution_loss(database):
    repo, cfg, owner, settlement = await cycle(database, usdt_fee=D('.02'))
    try:
        before = await repo.snapshot(cfg.binding)
        assert before.balance.total_quote_usdt == D('10.04')
        assert before.budget.session_execution_loss_usdt == D('.08')
        result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        assert dict(result.actuals_usdt) == {'intent1': D('.08'), 'intent2': D('0')}
        after = await repo.snapshot(cfg.binding)
        assert after.budget.session_execution_loss_usdt == D('.08') and after.balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_same_cycle_has_one_application_and_one_original_readback(database):
    import asyncio
    repo, cfg, owner, settlement = await cycle(database)
    try:
        evidence = await facts(repo)
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(*(settlement.settle_inventory(command(), facts=evidence, cut=cut, owner=owner) for _ in range(2)))
        assert sorted(row.replayed for row in results) == [False, True]
        assert (await repo.budget(cfg.binding)).session_subsidy_committed_usdt == D('.12')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_settlements') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['member', 'receipt', 'missing_settlement', 'missing_member', 'budget_spend'])
async def test_corrupt_or_missing_settlement_proof_blocks_further_financial_permission(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    try:
        await settlement.settle_inventory(command(), facts=await facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        table = {'member': 'spot_inventory_members', 'receipt': 'operations', 'missing_settlement': 'spot_inventory_settlements',
                 'missing_member': 'spot_inventory_members', 'budget_spend': 'budget_events'}[fault]
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if fault == 'member':
            await database[0].execute("UPDATE life_financial.spot_inventory_members SET actual_usdt=0 WHERE intent_id='intent1'")
        elif fault == 'receipt':
            await database[0].execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{actuals_usdt,intent1}','\"0\"') WHERE operation_id='settle1'")
        elif fault == 'budget_spend':
            await database[0].execute("DELETE FROM life_financial.budget_events WHERE family='SUBSIDY' AND kind='SPEND'")
        elif fault == 'missing_settlement':
            await database[0].execute('ALTER TABLE life_financial.spot_inventory_members DISABLE TRIGGER USER')
            await database[0].execute('DELETE FROM life_financial.spot_inventory_members')
            await database[0].execute('DELETE FROM life_financial.spot_inventory_settlements')
            await database[0].execute('ALTER TABLE life_financial.spot_inventory_members ENABLE TRIGGER USER')
        else:
            await database[0].execute("DELETE FROM life_financial.spot_inventory_members WHERE intent_id='intent1'")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_SETTLEMENT_UNVERIFIED'):
            await settlement.lookup_inventory_settlement('settle1', cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('asset,quantity', [('USDT', D('1')), ('LIFE', D('.99'))])
async def test_actual_file_and_postgres_cycle_economics_match(database, tmp_path, asset, quantity):
    from test.hummingbot.strategy_v2.life_liquidity.storage import test_spot_settlement as file_cases
    directory = tmp_path / 'file'
    directory.mkdir()
    files, binding, file_owner = file_cases.create_fixture(directory)
    file_store = await file_cases.open_it(files, binding, directory, owner=file_owner, create=True)
    try:
        await file_cases.closed_cycle(file_store, binding, file_owner, fee_currency=asset, sell_quantity=quantity)
        result = await file_store.settle_inventory(file_cases.command(), facts=await file_cases.evidence(file_store, binding),
                                                   cut=(await file_store.snapshot(binding)).cut, owner=file_owner)
        file_view = await file_store.snapshot(binding)
    finally:
        await file_store.close()
    repo, cfg, owner, settlement = await cycle(database, fee_currency=asset, sell_quantity=quantity)
    try:
        pg_result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        pg_view = await repo.snapshot(cfg.binding)
        assert pg_result.actuals_usdt == result.actuals_usdt and pg_result.operation_id == result.operation_id and pg_result.cycle_id == result.cycle_id
        assert pg_view.balance == file_view.balance and pg_view.budget == file_view.budget
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_readonly_scoped_receipt_and_private_helper_dml_denials(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, settlement = await cycle(database)
    reader = None
    c = None
    try:
        result = await settlement.settle_inventory(command(), facts=await facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        rcfg = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reader = PgSpotRepository(await PgRuntime.open(rcfg))
        view = PgInventorySettlementRepository(reader)
        assert (await view.lookup_inventory_settlement('settle1', cfg.binding)).actuals_usdt == result.actuals_usdt
        with pytest.raises(StorageError):
            await view.settle_inventory(command(operation_id='readonly'), facts=await facts(reader), cut=(await reader.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError, match='PG_SPOT_BINDING_MISMATCH'):
            await view.lookup_inventory_settlement('settle1', replace(cfg.binding, account_uid='999'))
        c = await asyncpg.connect(cfg.dsn)
        for query, args in [('SELECT life_financial.spot_inventory_evidence($1,$2::jsonb)', (cfg.account_id, '["intent1","intent2"]')),
                            ('SELECT life_financial.spot_inventory_settlements_verified($1)', (cfg.account_id,)),
                            ('UPDATE life_financial.spot_inventory_members SET actual_usdt=0 WHERE account_id=$1', (cfg.account_id,))]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(query, *args)
        assert (await repo.budget(cfg.binding)).session_subsidy_committed_usdt == D('.12')
    finally:
        if c is not None:
            await c.close()
        if reader is not None:
            await reader.close()
        await repo.close()


@pytest.mark.asyncio
async def test_qualified_late_settlement_after_halt_does_not_activate_or_change_wallet(database):
    repo, cfg, owner, settlement = await cycle(database)
    try:
        await database[0].execute("UPDATE life_financial.accounts SET mode='DRAINING',halted=true WHERE account_id=$1", cfg.account_id)
        before = await repo.snapshot(cfg.binding)
        await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        row = await database[0].fetchrow('SELECT mode,halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert row['mode'] == 'DRAINING' and row['halted']
        assert (await repo.balance(cfg.binding)) == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_flat_wallet_still_cannot_settle_an_order_without_qualified_terminal_receipt(database):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database, close_last=False)
    try:
        before = await repo.snapshot(cfg.binding)
        assert before.balance.total_base_life == 5 and before.balance.reserved_base_life > 0
        with pytest.raises(StorageError, match='PG_SETTLEMENT_TERMINAL_REQUIRED'):
            await settlement.settle_inventory(command(), facts=await facts(repo), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_stale_entry_facts_preserve_original_commitments(database):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    repo, cfg, owner, settlement = await cycle(database)
    try:
        before = await repo.snapshot(cfg.binding)
        stale = await facts(repo)
        stale = replace(stale, observed_at=stale.observed_at - timedelta(seconds=2))
        with pytest.raises(StorageError, match='PG_SETTLEMENT_FACTS_UNQUALIFIED'):
            await settlement.settle_inventory(command(), facts=stale, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()
