"""Native portfolio NAV from permanent economics; all inputs are synthetic."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import setup as swap_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository


async def refresh(database, values, *, short='0', cash='10', mark='1', index='1'):
    _, _, _, _, account_feed, capital, _, carry_feed, carry = values
    at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
    s = account_feed[0]
    # Explicit synthetic cash-only collateral haircut. This fixture does not
    # carry forward collateral already spent through fees/funding/withdrawals.
    account_feed[0] = replace(s, sequence=s.sequence + 1, observed_at_ms=at, short_base=D(short), spot_cash_quote=D(cash), collateral_quote=D(cash), mark_price_usdt=D(mark), index_price_usdt=D(index))
    await capital.check_capital(capital.binding)
    b = carry_feed[0]
    carry_feed[0] = replace(b, account=replace(b.account, snapshot=account_feed[0]),
                            mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=at, price_usdt=D(mark)),
                            index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=at, price_usdt=D(index)),
                            funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=at, coverage_through_ms=at),
                            terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=at))
    await carry.check_carry(capital.binding)


async def setup(database, *, drawdown='1000', max_observations=64, spot_options=None):
    values, swap, fills, fill = await swap_setup(database, spot_options=spot_options)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,$3,5000,$4,1048576)',
                              cfg.account_id, cfg.binding.policy_hash, D(drawdown), max_observations)
    return values, PgPortfolioRepository(swap), fills, fill


@pytest.mark.asyncio
async def test_portfolio_observes_replayed_cash_basis_and_drawdown_once_without_collateral_or_release(database):
    values, repo, fills, fill = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        proof = await database[0].fetchrow('SELECT life_financial.carry_verified($1) AS carry,life_financial.shared_capital_verified($1) AS shared,life_financial.spot_capital_verified($1) AS money,life_financial.portfolio_opening_highwater($1) AS opening,life_financial.portfolio_verified($1) AS portfolio', cfg.account_id)
        assert proof['portfolio'], dict(proof)
        cut = (await financial.snapshot(cfg.binding)).cut
        cmd = MeasurePortfolio('nav1')
        first = await repo.measure(cmd, cut=cut, owner=owner)
        assert first.measurement['nav_quote'] == D('15') and not first.trading_permission
        assert (await repo.measure(cmd, cut=cut, owner=owner)).replayed
        assert (await repo.frame(cfg.binding))['current'] is True
        await fills.apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.frame(cfg.binding))['current'] is False
        await refresh(database, values, short='1', cash='9.99', mark='.9', index='.9')
        second = await repo.measure(MeasurePortfolio('nav2'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert second.measurement['nav_quote'] == D('14.60')
        assert second.measurement['unrealized_quote'] == D('.11')
        assert second.measurement['highwater_quote'] == 15
        # Actual File joint financial projection, over the identical synthetic
        # anchor/trade/account cut. This tests valuation, not coupled settlement.
        from types import SimpleNamespace

        from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery, RecoveryTrade
        file_replay = object.__new__(JointRecovery)
        file_replay._binding = dict(opening_long_entry='0', opening_short_entry='0')
        file_replay._repository_application = None
        file_replay.controller = SimpleNamespace(_carry_monitor=None, _fill_attributor=None)
        file_replay.reservations = SimpleNamespace(cashflow_events={}, joint_cash_events={})
        opening_ms = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        state = dict(anchor=dict(spot_life_base='5', spot_cash_quote='10', long_base='0', short_base='0', observed_at_ms=opening_ms),
                     opening_cashflows={}, opening_joint={}, bundle=None, highwater='15')
        trade = RecoveryTrade('trade1', D('1'), D('1.01'), 'USDT', D('-.01'), D('0'), fill.fills[0].fill_at_ms)
        claim = SimpleNamespace(product='SWAP', side='SELL', position_action='OPEN')
        _, expected = file_replay._financial_replay(SimpleNamespace(payments=(), observed_at_ms=values[4][0].observed_at_ms),
                                                    state, values[4][0], {'trade1': (claim, trade)})
        assert second.measurement == {key: D(value) for key, value in expected.items()}
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await repo.lookup('nav1', cfg.binding)).measurement['nav_quote'] == 15
        assert (await repo.frame(cfg.binding))['current'] is True
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_drawdown_records_truth_and_halts_without_creating_cash_or_refunding_claim(database):
    values, repo, fills, fill = await setup(database, drawdown='1')
    financial, cfg, owner = values[:3]
    try:
        await fills.apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await refresh(database, values, short='1', cash='9.99', mark='.9', index='.9')
        before = await financial.balance(cfg.binding)
        result = await repo.measure(MeasurePortfolio('loss'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.halted and result.measurement['drawdown_bps'] > 1
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_stale_native_positions_cannot_publish_portfolio_nav(database):
    values, repo, fills, fill = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await fills.apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError, match='PG_PORTFOLIO_SOURCE_UNQUALIFIED'):
            await repo.measure(MeasurePortfolio('stale'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['basis', 'measurement', 'receipt', 'source'])
async def test_portfolio_corruption_blocks_cold_read_and_marks_snapshot_unverified(database, fault):
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        query = dict(basis="UPDATE life_financial.portfolio_observations SET basis=jsonb_set(basis,'{money,quote}','\"20\"')",
                     measurement="UPDATE life_financial.portfolio_observations SET measurement=jsonb_set(measurement,'{nav_quote}','\"25\"')",
                     receipt="UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{halted}','true') WHERE operation_id='nav1'",
                     source="UPDATE life_financial.carry_checks SET bundle=jsonb_set(bundle,'{mark,price_usdt}','\"2\"')")
        # Trusted test owner deliberately violates append-only storage. Runtime
        # cannot disable these triggers or edit source/financial history.
        table = dict(basis='portfolio_observations', measurement='portfolio_observations', receipt='operations', source='carry_checks')[fault]
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(query[fault])
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_PORTFOLIO_UNVERIFIED'):
            await repo.frame(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_portfolio_commit_restores_original_highwater_once_without_money_or_release(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio_process import exit_portfolio

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        command = MeasurePortfolio('nav1')
        if fault == 'process':
            await exit_portfolio(cfg, command, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                raise ConnectionError('synthetic portfolio COMMIT reply loss')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.measure(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgPortfolioRepository(PgSwapRepository(capital))
        found = await restored.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        if not committed:
            await restored.measure(command, cut=cut, owner=owner)
        assert (await restored.measure(command, cut=cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        assert len(await capital.claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_generation_change_at_physical_nav_commit_rolls_back_the_measurement(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup('nav1', cfg.binding) is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_original_receipt_readback_after_context_mutation_at_confirmed_commit(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await original(tx)
            repo.owner = replace(owner, epoch=owner.epoch + 1)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        repo.owner = owner
        restored = await repo.lookup('nav1', cfg.binding)
        assert restored.measurement['nav_quote'] == 15 and not restored.trading_permission
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_paid_funding_changes_nav_once_and_preserves_earlier_economic_cut(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup
    values, funding, command = await funding_setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    repo = PgPortfolioRepository(funding.swap)
    try:
        await repo.measure(MeasurePortfolio('before'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await funding.apply_funding(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not (await repo.frame(cfg.binding))['current']
        values[4][0] = command.bundle.account.snapshot
        await values[5].check_capital(cfg.binding)
        values[7][0] = command.bundle
        await values[8].check_carry(cfg.binding)
        result = await repo.measure(MeasurePortfolio('after'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.measurement['nav_quote'] == D('14.9')
        assert result.measurement['highwater_quote'] == 15
        assert (await repo.lookup('before', cfg.binding)).measurement['nav_quote'] == 15
        assert (await funding.frame(cfg.binding))['gross_debits_usdt'] == '0.1'
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_observation_bounds_and_roles_never_become_money_or_release_permissions(database):
    import asyncpg
    values, repo, _, _ = await setup(database, max_observations=1)
    financial, cfg, owner = values[:3]
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        await repo.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError, match='PG_PORTFOLIO_OBSERVATION_BOUND'):
            await repo.measure(MeasurePortfolio('nav2'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        for c in (runtime, reporter):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.portfolio_observations')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetchval('SELECT life_financial.portfolio_verified($1)', cfg.account_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.portfolio_command($1,$2,$3,$4,$5,$6,$7)',
                                    cfg.account_id, '{}', '{}', 0, '0' * 64, '0' * 64, '{}')
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await runtime.close()
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('asset,amount', [('USDT', '2'), ('USDT', '-2'), ('LIFE', '2'), ('LIFE', '-2')])
async def test_external_flows_change_nav_without_resetting_cash_flow_adjusted_highwater(database, asset, amount):
    from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.measure(MeasurePortfolio('before'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        ms = int(at.timestamp() * 1000)
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,5000,4096) ON CONFLICT DO NOTHING', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                                  cfg.account_id, '101', asset, D(amount), ms, b'synthetic-preverified-import')
        flow = CashflowSpot('cash1', '101', asset, D(amount), ms, D('1'), ms, ms,
                            'quote_unit' if asset == 'USDT' else 'independent_market', at)
        await financial.apply_cashflow(flow, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        if asset == 'LIFE':
            values[4][0] = replace(values[4][0], spot_life_base=D('5') + D(amount))
        await refresh(database, values, cash=str(D('10') + (D(amount) if asset == 'USDT' else D('0'))))
        result = await repo.measure(MeasurePortfolio('after'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.measurement['nav_quote'] == D('15') + D(amount)
        assert result.measurement['adjusted_nav_quote'] == 15 and result.measurement['highwater_quote'] == 15
        assert result.measurement['drawdown_bps'] == 0 and result.measurement['external_flows_quote'] == D(amount)
        assert (await repo.lookup('before', cfg.binding)).measurement['nav_quote'] == 15
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_source_expiry_during_physical_commit_does_not_publish_a_current_valuation(database, monkeypatch):
    import asyncio
    values, repo, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    # An explicit synthetic NAV source age, independently of carry/account ages.
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies DISABLE TRIGGER USER')
    await database[0].execute('UPDATE life_financial.portfolio_policies SET max_age_ms=1000')
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies ENABLE TRIGGER USER')
    await refresh(database, values)
    original = financial._runtime._commit
    try:
        async def delayed(tx):
            await asyncio.sleep(1.1)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', delayed)
        with pytest.raises(StorageError, match='PG_PORTFOLIO_SOURCE_STALE_AT_COMMIT'):
            await repo.measure(MeasurePortfolio('late'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup('late', cfg.binding) is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 0
    finally:
        await financial.close()
