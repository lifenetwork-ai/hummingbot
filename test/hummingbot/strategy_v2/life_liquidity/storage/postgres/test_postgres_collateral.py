"""Cash collateral is bounded by native USDT equity, never LIFE NAV or a loan.

All account values are synthetic; no provider authentication is asserted here.
"""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_batch import setup as batch_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_repair import setup as repair_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import setup as portfolio_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import setup as swap_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import PgJointRepairRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository


@pytest.mark.asyncio
async def test_joint_overstated_collateral_retains_whole_batch_halt_and_original_claim(database):
    values, repo, command = await batch_setup(database, carry_options=dict(refresh_initial_facts=True))
    financial, cfg, owner = values[:3]
    b = command.funding.bundle
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, collateral_quote=D('100'))))
    command = replace(command, funding=replace(command.funding, bundle=b))
    try:
        before = await financial.balance(cfg.binding)
        result = await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert (await repo.frame(cfg.binding))['batches'][0]['command'] == command.json()
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_signed_repair_cannot_turn_overstated_collateral_into_valid_finance(database):
    values, batch, bad, command, facts, cut, approval, importer = await repair_setup(database)
    financial, cfg, owner = values[:3]
    b = command.replacement.funding.bundle
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, collateral_quote=D('100'))))
    command = replace(command, replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=approval.issued_at, expires_at=approval.expires_at)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        assert await financial.balance(cfg.binding) == before
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await batch.lookup(command.replacement.operation_id, cfg.binding) is None
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_staging') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_portfolio_cannot_qualify_reported_collateral_with_life_nav(database):
    values, swap, _, _ = await swap_setup(database)
    repo = PgPortfolioRepository(swap)
    financial, cfg, owner = values[:3]
    # The portfolio owns 5 LIFE + 10 USDT. LIFE value is NOT USDT collateral in
    # the supported single-currency, non-borrowing profile.
    now = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    feed = values[4]
    feed[0] = replace(feed[0], collateral_quote=D('15'), sequence=2, observed_at_ms=at)
    b = values[7][0]
    values[7][0] = replace(b, account=replace(b.account, snapshot=feed[0]),
                           mark=replace(b.mark, sequence=2, observed_at_ms=at),
                           index=replace(b.index, sequence=2, observed_at_ms=at),
                           funding=replace(b.funding, sequence=2, observed_at_ms=at, coverage_through_ms=at),
                           terms=replace(b.terms, sequence=2, observed_at_ms=at))
    try:
        await values[5].check_capital(cfg.binding)
        await values[8].check_carry(cfg.binding)
        # Explicit privileged installation of the stricter portfolio profile
        # cannot qualify an older storage-only collateral observation.
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)',
                                  cfg.account_id, cfg.binding.policy_hash)
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError, match='PG_PORTFOLIO_COLLATERAL_UNQUALIFIED'):
            await repo.measure(MeasurePortfolio('overstated'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 0
        assert financial._runtime._healthy is False
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('collateral,accepted', [('0', True), ('9.89', True), ('9.90', True), ('9.900000000000000001', False)])
async def test_joint_cash_and_actual_upl_bound_collateral_without_adding_it_to_wallet(database, collateral, accepted):
    values, repo, command = await batch_setup(database, carry_options=dict(refresh_initial_facts=True))
    financial, cfg, owner = values[:3]
    b = command.funding.bundle
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, collateral_quote=D(collateral))))
    command = replace(command, funding=replace(command.funding, bundle=b))
    try:
        # SELL 1 @ 1.01, mark 1 -> actual UPL .01. Settled funding/fee leave
        # cash 9.89, so equity 9.90; opening 5 LIFE cannot raise this ceiling.
        result = await repo.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == ('APPLIED' if accepted else 'RECOVERY_REQUIRED')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == (D('9.89') if accepted else D('10'))
        assert len(await values[5].claims(cfg.binding)) == 1
        if not accepted:
            frame = await repo.frame(cfg.binding)
            assert frame['batches'][0]['reason'] == 'LIFE_JOINT_COLLATERAL_UNQUALIFIED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cash,long,short,long_entry,short_entry,mark,collateral,accepted', [
    ('10', '0', '0', '0', '0', '1', '10', True),
    ('10', '0', '0', '0', '0', '1', '10.0001', False),
    ('10', '2', '0', '1', '0', '1.5', '11', True),
    ('10', '0', '2', '0', '1.5', '1', '11', True),
    ('10', '2', '0', '2', '0', '1', '8', True),
    ('10', '2', '0', '2', '0', '1', '8.0001', False),
    ('1', '2', '0', '2', '0', '1', '0', True),
    ('1', '2', '0', '2', '0', '1', '.0001', False),
    ('10', '1', '1', '2', '2', '1', '10', True),
    ('10000000000000000000000000009.1', '0', '0', '0', '0', '1', '10000000000000000000000000010', False),
    ('10', '.333333333333333333', '0', '1.333333333333333333333333333', '0', '2', '10.222222222222222222', True),
    ('10', '.333333333333333333', '0', '1.333333333333333333333333333', '0', '2', '10.222222222222222223', False),
])
async def test_collateral_kernel_matches_file_decimal28_unrealized_arithmetic(database, cash, long, short, long_entry, short_entry, mark, collateral, accepted):
    import json
    from dataclasses import asdict
    from decimal import ROUND_HALF_EVEN, Context, localcontext
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import snapshot

    from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
    money = dict(quote=cash, base='5')
    legs = dict(long_base=long, short_base=short, long_entry=long_entry, short_entry=short_entry)
    s = snapshot(spot_life_base=D('5'), spot_cash_quote=D(cash), long_base=D(long), short_base=D(short),
                 collateral_quote=D(collateral), mark_price_usdt=D(mark))
    source = _json(asdict(s))
    # Independent Python expression, using the declared File replay precision.
    # This verifies arithmetic equivalence, not an installed File source gate.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        upl = D(long) * (D(mark) - D(long_entry)) + D(short) * (D(short_entry) - D(mark))
        rounded_equity = D(cash) + upl
    with localcontext(Context(prec=200, rounding=ROUND_HALF_EVEN)):
        exact_equity = D(cash) + D(long) * (D(mark) - D(long_entry)) + D(short) * (D(short_entry) - D(mark))
        expected = D(collateral) <= max(D('0'), min(exact_equity, rounded_equity))
    assert expected is accepted
    assert await database[0].fetchval('SELECT life_financial.joint_collateral_qualified($1::jsonb,$2::jsonb,$3::jsonb)',
                                      json.dumps(money), json.dumps(legs), json.dumps(source)) is accepted


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('account_mode', '3'), ('collateral_currency', 'USD'), ('margin_mode', 'isolated'),
                                         ('scope_complete', False), ('collateral_quote', None), ('collateral_quote', 10),
                                         ('collateral_quote', 'NaN'), ('collateral_quote', '-1'), ('long_base', '1'),
                                         ('spot_cash_quote', '11'), ('mark_price_usdt', '0')])
async def test_collateral_kernel_rejects_unsupported_scope_or_missing_unbounded_values(database, field, value):
    import json
    from dataclasses import asdict
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import snapshot

    from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
    source = _json(asdict(snapshot(spot_life_base=D('5'))))
    source[field] = value
    assert not await database[0].fetchval('SELECT life_financial.joint_collateral_qualified($1::jsonb,$2::jsonb,$3::jsonb)',
                                          json.dumps(dict(base='5', quote='10')),
                                          json.dumps(dict(long_base='0', short_base='0', long_entry='0', short_entry='0')), json.dumps(source))


@pytest.mark.asyncio
@pytest.mark.parametrize('liability', [False, True])
async def test_installed_portfolio_profile_blocks_current_and_cold_same_invalid_source(database, liability):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    values, _, _, _ = await portfolio_setup(database)
    financial, cfg, owner = values[:3]
    feed = values[4]
    feed[0] = replace(feed[0], collateral_quote=D('15') if not liability else D('10'), sequence=2,
                      observed_at_ms=int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000))
    cold = None
    try:
        if liability:
            await database[0].execute("UPDATE life_financial.balances SET liability=1,version=version+1 WHERE asset='USDT'")
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError, match='PG_SWAP_ACCOUNT_SNAPSHOT_MISMATCH'):
            await values[5].check_capital(cfg.binding)
        assert financial._runtime._healthy is False
        assert await financial.balance(cfg.binding) == before
        await financial.close()
        cold = await PgRuntime.open(cfg)
        repo = PgSpotRepository(cold)
        capital = PgSharedCapitalRepository(repo, owner=owner, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
        feed[0] = replace(feed[0], sequence=3, observed_at_ms=int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000))
        with pytest.raises(StorageError, match='PG_SWAP_ACCOUNT_SNAPSHOT_MISMATCH'):
            await capital.reserve(ReserveCapital('cannot-credit-collateral', native_claim(owner, 'new-collateral')),
                                  await capital.cut(cfg.binding), owner)
        assert cold._healthy is False
        assert len(await capital.claims(cfg.binding)) == 1
        assert await repo.balance(cfg.binding) == before
    finally:
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_liability_change_at_physical_nav_commit_rolls_back_complete_publication(database, monkeypatch):
    values, repo, _, _ = await portfolio_setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute("""CREATE FUNCTION life_financial.test_collateral_liability(p_account uuid) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS
        'UPDATE life_financial.balances SET liability=1,version=version+1 WHERE account_id=p_account AND asset=''USDT''';
        REVOKE ALL ON FUNCTION life_financial.test_collateral_liability(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION life_financial.test_collateral_liability(uuid) TO life_pg2_runtime""")
    original = financial._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_collateral_liability($1)', cfg.account_id)
        await original(tx)
    try:
        before = await financial.balance(cfg.binding)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='(PG_PORTFOLIO|PG_SWAP|PG_CAPITAL|PG_CARRY)'):
            await repo.measure(MeasurePortfolio('changed-liability'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval("SELECT liability FROM life_financial.balances WHERE asset='USDT'") == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.portfolio_observations') == 0
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='changed-liability'") == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_portfolio_profile_allows_actual_funding_cash_before_final_source_matches_wallet(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup
    values, funding, command = await funding_setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)',
                              cfg.account_id, cfg.binding.policy_hash)
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await funding.apply_funding(command, cut=cut, owner=owner)
        assert result.state == 'APPLIED' and result.cash_delta_usdt == D('-.1')
        assert (await funding.apply_funding(command, cut=cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        values[4][0] = command.bundle.account.snapshot
        await values[5].check_capital(cfg.binding)
        values[7][0] = command.bundle
        await values[8].check_carry(cfg.binding)
        nav = await PgPortfolioRepository(funding.swap).measure(MeasurePortfolio('after-cash'),
                                                                cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert nav.measurement['nav_quote'] == D('14.9')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()
