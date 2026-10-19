"""Native mode execution uses original financial facts and separate budgets."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_execution_modes import (
    choose_mode,
    execution_store,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk_markout import refresh

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def mode_store(database, mode='INVENTORY', *, inventory_side='BUY', deadline_seconds=60):
    now = await database[0].fetchval('SELECT clock_timestamp()')
    terms = dict(inventory=dict(campaign_id='execution1', started_at=(now - timedelta(seconds=1)).isoformat(), max_age_ms=1000,
                                policy=dict(side=inventory_side, target_base='1', benchmark_price_usdt='1', deadline_utc=(now + timedelta(seconds=deadline_seconds)).isoformat(),
                                            max_child_base='.2', max_total_notional_quote='10', max_slippage_bps='100', max_impact_quote='.1',
                                            max_exit_loss_quote='.5', participation_fraction='.1', min_independent_volume_base='1', tick_size='.01', lot_size='.1', min_size_base='.1')),
                 exit=dict(target_base='2', max_child_base='.2', max_slippage_bps='100', max_age_ms=1000),
                 rules=dict(tick_size='.01', lot_size='.1', min_size_base='.1'))
    values = await execution_store(database, execution_terms=terms)
    repo, cfg, owner, risk, ready, keys, body = values
    await choose_mode(database, repo, cfg, owner, keys, body, mode=mode)
    await refresh(database, repo, cfg, owner, risk, 'mode-nav')
    return values


async def market(database, repo, cfg, owner, *, operation='market1', **changes):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import (
        NativeExecutionMarket,
        PgExecutionRepository,
    )
    ms = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
    command = replace(NativeExecutionMarket(operation, 'intent1', ms, ms, 'independent_market', 'execution1',
                                            D('1'), D('1.01'), D('100'), D('0'), D('5'), D('0'), D('.01'), D('1')), **changes)
    cut = (await repo.snapshot(cfg.binding)).cut
    execution = PgExecutionRepository(repo)
    receipt = await execution.observe(command, cut=cut, owner=owner)
    return execution, command, cut, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['INVENTORY', 'EXIT'])
async def test_native_mode_market_is_observation_only_until_guarded_child_commit(database, mode):
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, mode)
    try:
        before = await repo.snapshot(cfg.binding)
        execution, command, cut, receipt = await market(database, repo, cfg, owner)
        assert not receipt['trading_permission']
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        decision = (await execution.frame())['decision']
        assert decision['allowed'] and not decision['trading_permission']
        child = order(owner, quantity_base=D(decision['quantity_base']), limit_price_usdt=D(decision['price_usdt']),
                      side=decision['side'], subsidy_quote_usdt=D(decision['estimated_loss_quote']),
                      budget_scope='EXIT' if mode == 'EXIT' else 'QUOTE', risk_epoch=ready.risk_epoch)
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT mode FROM life_financial.execution_children') == mode
        await refresh(database, repo, cfg, owner, risk, 'arm-mode-nav')
        await market(database, repo, cfg, owner, operation='market2')
        balance = (await repo.snapshot(cfg.binding)).balance
        await repo.arm(child.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == balance
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,changes', [
    ('INVENTORY', dict(quantity_base=D('.3'))), ('INVENTORY', dict(limit_price_usdt=D('1.01'))),
    ('INVENTORY', dict(fee_buffer_quote_usdt=D('0'))), ('INVENTORY', dict(fee_buffer_base_life=D('0'))),
    ('INVENTORY', dict(subsidy_quote_usdt=D('.001'))), ('INVENTORY', dict(side='SELL')), ('INVENTORY', dict(quantity_base=D('.15'))),
    ('EXIT', dict(budget_scope='QUOTE')), ('EXIT', dict(quantity_base=D('4'))),
    ('EXIT', dict(fee_buffer_base_life=D('3'))), ('EXIT', dict(subsidy_quote_usdt=D('0')))])
async def test_native_mode_invalid_child_rolls_back_all_reservations_and_membership(database, mode, changes):
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, mode)
    try:
        execution, _, _, _ = await market(database, repo, cfg, owner)
        decision = (await execution.frame())['decision']
        child = order(owner, quantity_base=D('.2'), side=decision['side'], limit_price_usdt=D(decision['price_usdt']),
                      subsidy_quote_usdt=D(decision['estimated_loss_quote']), risk_epoch=ready.risk_epoch, budget_scope='EXIT' if mode == 'EXIT' else 'QUOTE')
        child = replace(child, **changes)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.prepare(child, cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_children') == 0
        assert await repo.attempt(child.intent_id, cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_native_inventory_planner_matches_file_with_exact_original_policy(database, side):
    from datetime import datetime, timezone
    from decimal import localcontext

    from hummingbot.strategy_v2.life_liquidity.inventory_execution import (
        InventoryExecutionPolicy,
        InventoryExecutionSnapshot,
        plan_inventory_child,
    )
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, inventory_side=side)
    try:
        execution, command, _, _ = await market(database, repo, cfg, owner)
        native = (await execution.frame())['decision']
        with localcontext() as ctx:
            ctx.prec = 160
            p = {k: datetime.fromisoformat(v) if k == 'deadline_utc' else v if k == 'side' else D(v) for k, v in body['inventory']['policy'].items()}
            wallet = await repo.balance(cfg.binding)
            inputs = InventoryExecutionSnapshot(datetime.fromtimestamp(command.observed_at_ms / 1000, timezone.utc), command.best_bid_usdt, command.best_ask_usdt,
                                                command.public_volume_base, command.known_own_volume_base, command.independent_depth_base, command.expected_impact_quote,
                                                wallet.available_base_life, wallet.available_quote_usdt, D('0'), D('0'), D('0'), D('0'), D('0'), True, True)
            expected = plan_inventory_child(InventoryExecutionPolicy(**p), inputs, fee_rate=command.fee_rate)
        assert expected.allowed and native['allowed']
        assert (D(native['quantity_base']), D(native['price_usdt']), D(native['estimated_loss_quote'])) == (expected.quantity_base, expected.price_usdt, expected.estimated_exit_loss_quote)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,expected', [
    (dict(public_volume_base=D('100'), known_own_volume_base=D('100')), 'INDEPENDENT_VOLUME_INSUFFICIENT'),
    (dict(independent_depth_base=D('0')), 'INDEPENDENT_DEPTH_UNAVAILABLE'),
    (dict(expected_impact_quote=D('.2')), 'IMPACT_LIMIT_BREACHED'),
    (dict(independent_value_usdt=D('2')), 'EXECUTION_REFERENCE_MISMATCH'),
    (dict(volume_anchor='foreign'), 'INVENTORY_SNAPSHOT_INVALID')])
async def test_native_inventory_independent_market_denials_cannot_reserve(database, changes, expected):
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database)
    try:
        execution, _, _, _ = await market(database, repo, cfg, owner, **changes)
        decision = (await execution.frame())['decision']
        assert not decision['allowed'] and decision['reason_code'] == expected
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_' + expected):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_execution_market_unknown_commit_replays_original_without_healing(database, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database)
    try:
        original = repo._runtime._commit
        once = True

        async def lost(tx):
            nonlocal once
            if not once:
                return await original(tx)
            once = False
            if committed:
                await original(tx)
            raise ConnectionError('synthetic execution market COMMIT reply lost')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        # Build once, preserving the exact original source/cut for retry.
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import (
            NativeExecutionMarket,
            PgExecutionRepository,
        )
        ms = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
        command = NativeExecutionMarket('lost', 'intent1', ms, ms, 'independent_market', 'execution1', D('1'), D('1.01'), D('100'), D('0'), D('5'), D('0'), D('.01'), D('1'))
        cut = (await repo.snapshot(cfg.binding)).cut
        execution = PgExecutionRepository(repo)
        with pytest.raises(CommitUnknown):
            await execution.observe(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_market_facts') == int(committed)
        replay = await execution.observe(command, cut=cut, owner=owner)
        assert replay['replayed'] is committed and not replay['trading_permission']
        assert not repo._runtime.healthy
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_market_expiring_during_physical_commit_is_not_published(database, monkeypatch):
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database)
    try:
        original = repo._runtime._commit

        async def late(tx):
            await tx._connection.execute('SELECT pg_sleep(.55)')
            await tx._connection.execute('SELECT pg_sleep(.55)')
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', late)
        with pytest.raises(StorageError, match='PG_EXECUTION_MARKET_STALE_AT_COMMIT'):
            await market(database, repo, cfg, owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_market_facts') == 0
    finally:
        await repo.close()


async def terminal_exit(database, *, filled=True, mode='EXIT', subsidy=D('.1')):
    from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, mode)
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import PgExecutionRepository
    execution = PgExecutionRepository(repo)
    if mode != 'MM':
        execution, _, _, _ = await market(database, repo, cfg, owner)
    child = order(owner, quantity_base=D('.2'), limit_price_usdt=D('1') if mode == 'INVENTORY' else D('1.01'), side='BUY' if mode == 'INVENTORY' else 'SELL', budget_scope='EXIT' if mode == 'EXIT' else 'QUOTE', subsidy_quote_usdt=subsidy, risk_epoch=ready.risk_epoch)
    await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    await repo.arm(child.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    obs = await repo.observation_cut(cfg.binding); at = obs.observed_at; ms = int(at.timestamp() * 1000)
    fill = SpotFillObservation('trade1', D('.1'), child.limit_price_usdt, 'USDT', D('-.003'), ms, D('1'), ms, ms, 'independent_market')
    fills = (fill,) if filled else ()
    if filled:
        await repo.apply_fills(FillSpot('fill1', child.operation_id, child.intent_id, child.client_order_id, child.market, child.side, child.session_id, child.epoch,
                                        '123456789', D('.1'), fills, at), cut=obs.cut, owner=owner)
    else:
        from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
        await repo.acknowledge(AckSpot('ack1', child.operation_id, child.intent_id, child.client_order_id, child.market, child.side, child.session_id, child.epoch, '123456789', at), cut=obs.cut, owner=owner)
    obs = await repo.observation_cut(cfg.binding); view = await repo.snapshot(cfg.binding)
    facts = SpotTerminalFacts(cfg.binding, obs.observed_at, 'CANCELED', D('.1') if filled else D('0'), child.quantity_base, child.limit_price_usdt, fills,
                              view.balance.total_base_life, view.balance.total_quote_usdt, D('0'), D('0'), ('trade1',) if filled else (), (), (), (), (), True, True, True)
    terminal = TerminalSpot('terminal1', child.operation_id, child.intent_id, child.client_order_id, child.market, child.side, child.session_id, child.epoch, '123456789', facts)
    await repo.confirm_terminal(terminal, cut=view.cut, owner=owner)
    return repo, cfg, owner, execution, fill


async def exit_facts(repo, *, filled=True):
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
    view = await repo.snapshot(repo.binding); obs = await repo.observation_cut(repo.binding)
    return SpotFinancialRecoveryFacts(repo.binding, obs.observed_at, view.balance.total_base_life, view.balance.total_quote_usdt, D('0'), D('0'),
                                      ('trade1',) if filled else (), (), (), (), True, True, True)


@pytest.mark.asyncio
@pytest.mark.parametrize('filled', [False, True])
async def test_native_exit_settles_original_actual_once_and_never_refunds_gross_loss(database, filled):
    repo, cfg, owner, execution, fill = await terminal_exit(database, filled=filled)
    try:
        before = await repo.snapshot(cfg.binding)
        frame = await execution.frame()
        assert before.budget.session_subsidy_committed_usdt == 0
        assert D(frame['exit_budget']['windows'][2]['committed']) == (D('.1') if filled else 0)
        facts = await exit_facts(repo, filled=filled)
        result = await execution.settle_exit('settle-exit', 'intent1', facts=facts, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        actual = fill.loss('SELL') if filled else D('0')
        assert D(result['actual_usdt']) == actual == (D('.002') if filled else 0)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        status = (await execution.frame())['exit_budget']['windows'][2]
        assert D(status['committed']) == 0 and D(status['incurred']) == actual
        replay = await execution.settle_exit('settle-exit', 'intent1', facts=facts, cut=before.cut, owner=owner)
        assert replay['replayed'] and (await repo.snapshot(cfg.binding)).cut == after.cut
        with pytest.raises(StorageError, match='PG_EXIT_SETTLEMENT_ALREADY_SETTLED'):
            await execution.settle_exit('settle-again', 'intent1', facts=await exit_facts(repo, filled=filled), cut=after.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_exit_unknown_commit_survives_cold_and_preserves_halt_and_budgets(database, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import PgExecutionRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    try:
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        facts = await exit_facts(repo); before = await repo.snapshot(cfg.binding)
        original = repo._runtime._commit

        async def lost(tx):
            if committed:
                await original(tx)
            raise ConnectionError('synthetic EXIT settlement COMMIT lost')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await execution.settle_exit('settle-exit', 'intent1', facts=facts, cut=before.cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_exit_settlements') == int(committed)
        await repo.close()
        repo = PgSpotRepository(await PgRuntime.open(cfg)); execution = PgExecutionRepository(repo)
        result = await execution.settle_exit('settle-exit', 'intent1', facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result['replayed'] is committed and not result['trading_permission']
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await repo.budget(cfg.binding)).session_execution_loss_usdt == D('.002')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=2), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(account_trade_ids=()), dict(runner_scope_complete=False), dict(account_quote_usdt=D('99')), dict(liability_base_life=D('.1'))])
async def test_native_exit_incomplete_facts_release_no_hold(database, changes):
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    try:
        before = await repo.snapshot(cfg.binding); frame = await execution.frame()
        with pytest.raises(StorageError):
            await execution.settle_exit('settle-exit', 'intent1', facts=replace(await exit_facts(repo), **changes), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await execution.frame())['exit_budget'] == frame['exit_budget']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_exit_settlements') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_exit_matches_file_money_fees_and_actual_settlement(database, tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_exit_budget_repository import exit_repository
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
        command as file_command,
        fill as file_fill,
        prepare_arm,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
        facts as file_facts,
        terminal as file_terminal,
    )
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    store, files, binding, file_owner = await exit_repository(tmp_path)
    try:
        await prepare_arm(store, binding, file_owner, acknowledge=False, side='SELL', budget_scope='EXIT', quantity_base=D('.2'), limit_price_usdt=D('1.01'), subsidy_quote_usdt=D('.1'))
        event = file_fill(quantity_base_life=D('.1'), price_usdt=D('1.01'), signed_fee=D('-.003'), independent_price_usdt=D('1'))
        await store.apply_fills(file_command(file_owner, side='SELL', cumulative_base_life=D('.1'), fills=(event,)), cut=(await store.snapshot(binding)).cut, owner=file_owner)
        view = await store.snapshot(binding)
        source = file_facts(binding, cumulative_base_life=D('.1'), quantity_base_life=D('.2'), limit_price_usdt=D('1.01'), fills=(event,),
                            account_base_life=view.balance.total_base_life, account_quote_usdt=view.balance.total_quote_usdt, account_trade_ids=('trade1',))
        await store.confirm_terminal(file_terminal(binding, file_owner, source, side='SELL'), cut=view.cut, owner=file_owner)
        result = await execution.settle_exit('settle-exit', 'intent1', facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        native = await repo.snapshot(cfg.binding); file = await store.snapshot(binding)
        assert (native.balance, native.budget.session_execution_loss_usdt, native.budget.session_subsidy_committed_usdt) == (file.balance, file.budget.session_execution_loss_usdt, file.budget.session_subsidy_committed_usdt)
        assert await store._call(lambda: store._subsidy.exit.matches_settled('intent1', D(result['actual_usdt']), session_id=str(file_owner.session_id)))
    finally:
        await store.close(); await repo.close()


@pytest.mark.asyncio
async def test_native_quote_exhaustion_does_not_consume_the_separate_exit_allowance(database):
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, execution, fill = await terminal_exit(database, mode='MM', subsidy=D('.24'))
    try:
        before = await repo.snapshot(cfg.binding)
        assert before.budget.subsidy_available_usdt == 0 and before.budget.session_subsidy_committed_usdt == D('.24')
        keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'REVOKE', 'RECOVER', 'OBSERVE_SESSION', 'INVENTORY'))})
        body = await database[0].fetchval('SELECT body FROM life_financial.execution_policies')
        import json
        body = json.loads(body)
        await choose_mode(database, repo, cfg, owner, keys, body, mode='EXIT', operation='exit-mode')
        ready = await refresh(database, repo, cfg, owner, PgRiskRepository(repo), 'exit-after-quote')
        execution, _, _, _ = await market(database, repo, cfg, owner, intent_id='exit2')
        decision = (await execution.frame())['decision']
        assert decision['allowed']
        child = order(owner, operation_id='exit2', intent_id='exit2', client_order_id='ExitWire2', side='SELL', budget_scope='EXIT', quantity_base=D('.2'),
                      limit_price_usdt=D('1.01'), subsidy_quote_usdt=D('.1'), risk_epoch=ready.risk_epoch)
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        frame = await execution.frame()
        assert (await repo.budget(cfg.binding)).session_subsidy_committed_usdt == D('.24')
        assert D(frame['exit_budget']['windows'][2]['committed']) == D('.1')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_inventory_mode_restart_does_not_reset_filled_target_or_fixed_benchmark(database):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, execution, fill = await terminal_exit(database, mode='INVENTORY')
    try:
        original = json.loads(await database[0].fetchval('SELECT body FROM life_financial.execution_policies'))
        keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'REVOKE', 'RECOVER', 'OBSERVE_SESSION', 'INVENTORY'))})
        await choose_mode(database, repo, cfg, owner, keys, original, mode='INVENTORY', operation='inventory-again')
        await refresh(database, repo, cfg, owner, PgRiskRepository(repo), 'inventory-again-nav')
        await market(database, repo, cfg, owner, intent_id='next', operation='market-next')
        decision = (await execution.frame())['decision']
        assert decision['allowed'] and D(decision['remaining_base']) == D('.9')
        assert json.loads(await database[0].fetchval('SELECT body FROM life_financial.execution_policies')) == original
        assert (await repo.budget(cfg.binding)).session_subsidy_committed_usdt == D('.1')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['INVENTORY', 'EXIT'])
async def test_native_bootstrap_does_not_authorize_inventory_but_allows_only_bounded_exit(database, mode):
    import json
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, mode)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_markout_policies VALUES($1,$2,ARRAY[1]::bigint[],1,1,60000,1000,1000,0,$3::jsonb)', cfg.account_id, cfg.binding.policy_hash, json.dumps([['BUY' if mode == 'INVENTORY' else 'SELL', 'small', 1]]))
        await database[0].execute('INSERT INTO life_financial.risk_markout_probe_policies VALUES($1,$2,.25,1)', cfg.account_id, cfg.binding.policy_hash)
        await refresh(database, repo, cfg, owner, risk, 'markout-mode-nav')
        execution, _, _, _ = await market(database, repo, cfg, owner)
        decision = (await execution.frame())['decision']
        child = order(owner, quantity_base=D('.2'), side=decision['side'], limit_price_usdt=D(decision['price_usdt']),
                      budget_scope='EXIT' if mode == 'EXIT' else 'QUOTE', subsidy_quote_usdt=D('.1'), risk_epoch=ready.risk_epoch)
        before = await repo.snapshot(cfg.binding)
        if mode == 'INVENTORY':
            with pytest.raises(StorageError, match='PG_EXECUTION_NORMAL_READY_REQUIRED'):
                await repo.prepare(child, cut=before.cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).balance == before.balance
        else:
            await repo.prepare(child, cut=before.cut, owner=owner)
            assert await database[0].fetchval('SELECT mode FROM life_financial.execution_children') == 'EXIT'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['market', 'child', 'exit'])
async def test_native_cold_corruption_is_not_repaired_by_readback(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import PgExecutionRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    try:
        await execution.settle_exit('settle-exit', 'intent1', facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        money = await database[0].fetch('SELECT * FROM life_financial.balances ORDER BY asset')
        table = {'market': 'execution_market_facts', 'child': 'execution_children', 'exit': 'execution_exit_settlements'}[fault]
        await repo.close()
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.' + table + " SET source_hash=repeat('0',64)")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        repo = PgSpotRepository(await PgRuntime.open(cfg)); execution = PgExecutionRepository(repo)
        with pytest.raises(StorageError, match='PG_EXECUTION_UNVERIFIED'):
            await execution.frame()
        assert money == await database[0].fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_exit_actual_backend_loss_retains_original_settlement_identity(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    admin = await asyncpg.connect(database[1])
    try:
        facts = await exit_facts(repo); cut = (await repo.snapshot(cfg.binding)).cut
        original = repo._runtime._commit
        once = True

        async def lost(tx):
            nonlocal once
            if not once:
                return await original(tx)
            once = False
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic lost EXIT settlement backend')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await execution.settle_exit('settle-exit', 'intent1', facts=facts, cut=cut, owner=owner)
        result = await execution.settle_exit('settle-exit', 'intent1', facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result['replayed'] is committed and not result['trading_permission']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_exit_settlements') == 1
        assert not repo._runtime.healthy
    finally:
        await admin.close(); await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_exit_process_death_keeps_original_economic_effect(database, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields

    repo, cfg, owner, execution, fill = await terminal_exit(database)
    config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    child = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.execution import PgExecutionRepository
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_exit import exit_facts
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)));owner=OwnerToken.from_json(data['owner'])
    facts=await exit_facts(repo);cut=(await repo.snapshot(repo.binding)).cut;original=repo._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await PgExecutionRepository(repo).settle_exit('process-exit','intent1',facts=facts,cut=cut,owner=owner)
asyncio.run(run())
'''
    try:
        before = await repo.snapshot(cfg.binding)
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(json.dumps(dict(config=config, owner=owner.json(), committed=committed)).encode()), 15)
        except BaseException:
            proc.kill(); await proc.wait(); raise
        assert proc.returncode == 73, stderr.decode()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_exit_settlements') == int(committed)
        result = await execution.settle_exit('process-exit', 'intent1', facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result['replayed'] is committed and not result['trading_permission']
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_exit_cancelled_caller_retains_owned_commit_and_no_new_permission(database, monkeypatch):
    import asyncio
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    entered, finish = asyncio.Event(), asyncio.Event()
    try:
        original = repo._runtime._commit

        async def held(tx):
            entered.set()
            await finish.wait()
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', held)
        args = dict(facts=await exit_facts(repo), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        task = asyncio.create_task(execution.settle_exit('cancel-exit', 'intent1', **args))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo._runtime._counts[True] == 1 and not repo._runtime.healthy
        finish.set()
        await repo.close()
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.execution_exit_settlements WHERE operation_id='cancel-exit'") == 1
        assert not repo._runtime.healthy
    finally:
        finish.set(); await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('privilege', ['market', 'settlement', 'read'])
async def test_enabled_native_execution_checks_grants_without_privilege_cache(database, privilege):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import (
        NATIVE_EXECUTION_ENTRYPOINT,
        NATIVE_EXIT_ENTRYPOINT,
    )
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database)
    try:
        await repo.snapshot(cfg.binding)
        signature = {'market': NATIVE_EXECUTION_ENTRYPOINT, 'settlement': NATIVE_EXIT_ENTRYPOINT, 'read': 'life_financial.execution_market_frame(uuid)'}[privilege]
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + signature + ' FROM life_pg2_runtime')
        with pytest.raises(StorageError, match='PG_EXECUTION_ROLE_PROFILE_INVALID'):
            await repo.snapshot(cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_exit_actual_commit_rechecks_original_complete_source_freshness(database, monkeypatch):
    repo, cfg, owner, execution, fill = await terminal_exit(database)
    try:
        facts = await exit_facts(repo); before = await repo.snapshot(cfg.binding)
        original = repo._runtime._commit

        async def late(tx):
            await tx._connection.execute('SELECT pg_sleep(.55)')
            await tx._connection.execute('SELECT pg_sleep(.55)')
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', late)
        with pytest.raises(StorageError, match='PG_EXIT_SETTLEMENT_FACTS_OR_PROJECTION_INVALID'):
            await execution.settle_exit('settle-exit', 'intent1', facts=facts, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_exit_settlements') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['INVENTORY', 'EXIT'])
async def test_native_degraded_permission_allows_only_target_bounded_exit(database, mode):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk import observe
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, mode)
    try:
        paused = await observe(database, repo, cfg, owner, risk, 'pause-mode', market_data_fresh=False)
        assert paused.state == 'PAUSED'
        ready = await observe(database, repo, cfg, owner, risk, 'recover-mode')
        assert ready.state == 'DEGRADED'
        execution, _, _, _ = await market(database, repo, cfg, owner)
        decision = (await execution.frame())['decision']
        child = order(owner, quantity_base=D('.2'), side=decision['side'], limit_price_usdt=D(decision['price_usdt']),
                      budget_scope='EXIT' if mode == 'EXIT' else 'QUOTE', subsidy_quote_usdt=D('.1'), risk_epoch=ready.risk_epoch)
        before = await repo.snapshot(cfg.binding)
        if mode == 'INVENTORY':
            with pytest.raises(StorageError, match='PG_EXECUTION_NORMAL_READY_REQUIRED'):
                await repo.prepare(child, cut=before.cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).balance == before.balance
        else:
            await repo.prepare(child, cut=before.cut, owner=owner)
            assert await database[0].fetchval('SELECT mode FROM life_financial.execution_children') == 'EXIT'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_inventory_deadline_expiring_at_actual_commit_refunds_nothing(database, monkeypatch):
    import asyncio
    from datetime import datetime
    repo, cfg, owner, risk, ready, keys, body = await mode_store(database, deadline_seconds=3)
    try:
        deadline = datetime.fromisoformat(body['inventory']['policy']['deadline_utc'])
        while await database[0].fetchval("SELECT clock_timestamp()<$1::timestamptz-interval '.7 seconds'", deadline):
            await asyncio.sleep(.05)
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk import observe
        await refresh(database, repo, cfg, owner, risk, 'deadline-fresh-nav')
        ready = await observe(database, repo, cfg, owner, risk, 'deadline-normal')
        assert ready.state == 'NORMAL'
        await market(database, repo, cfg, owner)
        original = repo._runtime._commit

        async def late(tx):
            # Each statement stays within the unchanged 1s SQL timeout.
            while await tx._connection.fetchval('SELECT clock_timestamp()<$1', deadline):
                await tx._connection.execute('SELECT pg_sleep(.1)')
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', late)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_INVENTORY_DEADLINE_EXPIRED'):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.execution_children') == 0
    finally:
        await repo.close()
