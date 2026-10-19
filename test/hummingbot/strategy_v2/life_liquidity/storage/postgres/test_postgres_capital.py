"""Approved synthetic bills, causal capital and cold accounting over real PG."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot, ObserveSpotNAV
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def capital_store(database, flows=(('101', 'USDT', '2'),), *, max_events=4096, financial_values=None):
    repo, cfg, owner = await spot(database) if financial_values is None else financial_values
    c = database[0]
    at = await c.fetchval('SELECT clock_timestamp()')
    ms = int(at.timestamp() * 1000)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    # Privileged test import only; signature bytes and numeric bounds are synthetic.
    await c.execute('''INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',$4)''',
                    cfg.account_id, cfg.binding.policy_hash, at - timedelta(seconds=1), max_events)
    for bill, asset, amount in flows:
        await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                        cfg.account_id, bill, asset, D(amount), ms, b'synthetic-preverified-import')
    return repo, cfg, owner, at, ms


def flow(at, ms, **changes):
    return replace(CashflowSpot('cash1', '101', 'USDT', D('2'), ms, D('1'), ms, ms, 'quote_unit', at), **changes)


def nav(at, ms, **changes):
    return replace(ObserveSpotNAV('nav1', D('1'), ms, ms, 'independent_market', at), **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize('relative_ms', [-1, 0])
async def test_transfer_opening_boundary_uses_the_same_millisecond_unit_as_file_causal_replay(database, relative_ms):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        c = database[0]
        # An isolated pre-application opening fixture with a fractional-ms UTC
        # timestamp. The exchange bill itself is an exact integer millisecond.
        opening = datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(microseconds=1)
        await c.execute('ALTER TABLE life_financial.spot_capital_policies DISABLE TRIGGER USER')
        await c.execute('UPDATE life_financial.spot_capital_policies SET opened_at=$1 WHERE account_id=$2', opening, cfg.account_id)
        await c.execute('ALTER TABLE life_financial.spot_capital_policies ENABLE TRIGGER USER')
        await c.execute('ALTER TABLE life_financial.bill_approvals DISABLE TRIGGER USER')
        await c.execute('UPDATE life_financial.bill_approvals SET transfer_at=to_timestamp($1::double precision/1000) WHERE account_id=$2', ms + relative_ms, cfg.account_id)
        await c.execute('ALTER TABLE life_financial.bill_approvals ENABLE TRIGGER USER')
        now = await c.fetchval('SELECT clock_timestamp()')
        command = flow(now, ms + relative_ms)
        cut = (await repo.snapshot(cfg.binding)).cut
        if relative_ms < 0:
            with pytest.raises(StorageError, match='PG_CASHFLOW_UNAPPROVED'):
                await repo.apply_cashflow(command, cut=cut, owner=owner)
            assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
            assert await c.fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        else:
            assert (await repo.apply_cashflow(command, cut=cut, owner=owner)).state == 'APPLIED'
            assert (await repo.balance(cfg.binding)).total_quote_usdt == 12
            assert (await repo.apply_cashflow(command, cut=cut, owner=owner)).replayed
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_deposit_adjusts_nav_without_profit_budget_reset_or_double_counting(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        receipt = await repo.apply_cashflow(flow(at, ms), cut=before.cut, owner=owner)
        assert receipt.state == 'APPLIED' and not receipt.replayed
        assert (await repo.apply_cashflow(flow(at, ms), cut=before.cut, owner=owner)).replayed
        await repo.apply_cashflow(flow(at, ms, operation_id='cash2'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert after.balance.total_quote_usdt == 12 and after.budget == before.budget
        assert not after.recovery_required
        measured = await repo.observe_nav(nav(at, ms), cut=after.cut, owner=owner)
        assert measured.capital.nav_usdt == 17 and measured.capital.adjusted_nav_usdt == 15
        assert measured.capital.highwater_usdt == 15 and measured.capital.net_cashflows_usdt == 2
        assert measured.capital.current and measured.capital.drawdown_bps == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 1
        assert (await repo.lookup_cashflow('cash1', cfg.binding)).state == 'APPLIED'
        assert (await repo.observe_nav(nav(at, ms), cut=after.cut, owner=owner)).replayed
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(bill_id='999'), dict(signed_amount=D('3')), dict(signed_amount=D('2.0000000000000000001')), dict(transfer_at_ms=1),
                                     dict(value_at_ms=1), dict(observed_at=datetime(2099, 1, 1, tzinfo=timezone.utc))])
async def test_unapproved_stale_or_future_bill_changes_nothing(database, changes):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.apply_cashflow(flow(at, ms, **changes), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('currency,amount,price', [('LIFE', '1', '2'), ('LIFE', '-1', '2'), ('USDT', '-1', '1')])
async def test_transfers_preserve_highwater_and_starting_inventory(database, currency, amount, price):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', currency, amount),))
    try:
        first = await repo.observe_nav(nav(at, ms, independent_price_usdt=D('2')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert first.capital.highwater_usdt == 20
        await repo.apply_cashflow(flow(at, ms, currency=currency, signed_amount=D(amount), independent_price_usdt=D(price),
                                       source_kind='quote_unit' if currency == 'USDT' else 'independent_market'),
                                  cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not (await repo.capital(cfg.binding)).current
        later = await repo.observe_nav(nav(at, ms, operation_id='nav2'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        expected = D('15') - D(amount) if currency == 'LIFE' else D('15')
        assert later.capital.adjusted_nav_usdt == expected and later.capital.highwater_usdt == 20
        assert later.capital.starting_inventory_pnl_usdt == (1 if currency == 'LIFE' and D(amount) < 0 else 0)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_and_deposit_keep_execution_loss_and_stale_nav_state(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        fill_at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, fill_at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not (await repo.capital(cfg.binding)).current
        spent = (await repo.snapshot(cfg.binding)).budget
        await repo.apply_cashflow(flow(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.observe_nav(nav(at, ms, operation_id='nav2', independent_price_usdt=D('.9')),
                                        cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.capital.nav_usdt == D('16.38') and result.capital.adjusted_nav_usdt == D('14.38')
        assert result.capital.execution_loss_usdt == D('.12') and result.capital.highwater_usdt == 15
        assert (await repo.snapshot(cfg.binding)).budget == spent
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_withdrawal_keeps_holds_and_books_funding_deficit(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '-8'),))
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        result = await repo.apply_cashflow(flow(at, ms, signed_amount=D('-8')), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert result.state == 'APPLIED' and after.balance.total_quote_usdt == 2
        assert after.balance.reserved_quote_usdt == before.balance.reserved_quote_usdt and after.budget == before.budget
        assert after.recovery_required and await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['cashflow', 'nav'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_commit_reply_cold_exact_retry_does_not_double_account(database, monkeypatch, kind, committed):
    import asyncpg
    repo, cfg, owner, at, ms = await capital_store(database)
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost capital reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    command = flow(at, ms) if kind == 'cashflow' else nav(at, ms)
    try:
        with pytest.raises(CommitUnknown):
            await getattr(repo, 'apply_cashflow' if kind == 'cashflow' else 'observe_nav')(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        result = await getattr(cold, 'apply_cashflow' if kind == 'cashflow' else 'observe_nav')(command, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert result.replayed is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (12 if kind == 'cashflow' else 10)
        if kind == 'nav':
            assert (await cold.capital(cfg.binding)).highwater_usdt == 15
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_impossible_cashflow_is_durable_pending_and_later_deposit_cannot_clear_it(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '-11'), ('102', 'USDT', '2')))
    try:
        before = await repo.snapshot(cfg.binding)
        pending = await repo.apply_cashflow(flow(at, ms, signed_amount=D('-11')), cut=before.cut, owner=owner)
        assert pending.state == 'RECOVERY_REQUIRED'
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.snapshot(cfg.binding)).recovery_required
        next_fact = await repo.apply_cashflow(flow(at, ms, operation_id='cash2', bill_id='102'),
                                              cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert next_fact.state == 'RECOVERY_REQUIRED' and (await repo.balance(cfg.binding)).total_quote_usdt == 10
        with pytest.raises(StorageError):
            await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_cashflow_facts') == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert (await cold.lookup_cashflow('cash1', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(cfg.binding)).recovery_required
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_cashflows_are_replayed_by_economic_time_not_arrival_order(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '-9'),))
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        fill_at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, fill_at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        deposit_at = await database[0].fetchval('SELECT clock_timestamp()')
        deposit_ms = int(deposit_at.timestamp() * 1000)
        await database[0].execute("INSERT INTO life_financial.bill_approvals VALUES($1,'102','USDT',5,to_timestamp($2::double precision/1000),$3)",
                                  cfg.account_id, deposit_ms, b'synthetic-preverified-import')
        await repo.apply_cashflow(flow(deposit_at, deposit_ms, operation_id='cash2', bill_id='102', signed_amount=D('5')),
                                  cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.apply_cashflow(flow(at, ms, signed_amount=D('-9')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        # Balance would be nonnegative if only arrival totals were considered;
        # withdrawal leaves 1 USDT at the earlier cut, but the fill needs 1.02.
        assert result.state == 'RECOVERY_REQUIRED'
        assert (await repo.balance(cfg.binding)).total_quote_usdt == D('13.98')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('currency,amount,price', [('USDT', '2', '1'), ('USDT', '-1', '1'), ('LIFE', '1', '2'), ('LIFE', '-1', '2')])
async def test_actual_file_and_postgres_cashflow_nav_parity(database, tmp_path, currency, amount, price):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import (
        nav as file_nav,
        transfer as file_transfer,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
        AT,
        BUDGET,
        CAPACITY,
        NOW,
        POLICY,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, SpotAccountingPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    repo, cfg, owner, at, ms = await capital_store(database, (('101', currency, amount),))
    files, binding, file_owner = create_fixture(tmp_path)
    policy = SpotAccountingPolicy(D('5'), D('10'), D('1'), NOW - timedelta(seconds=1), '100',
                                  (ApprovedSpotCashflow('101', currency, D(amount), AT),))
    native = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                    subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                    account_lock_root=tmp_path / 'locks', owner=file_owner, create_binding=True,
                                    clock=lambda: NOW, fill_policy=POLICY, accounting_policy=policy)
    try:
        source = 'quote_unit' if currency == 'USDT' else 'independent_market'
        await native.apply_cashflow(file_transfer(currency=currency, signed_amount=D(amount), independent_price_usdt=D(price), source_kind=source),
                                    cut=(await native.snapshot(binding)).cut, owner=file_owner)
        await repo.apply_cashflow(flow(at, ms, currency=currency, signed_amount=D(amount), independent_price_usdt=D(price), source_kind=source),
                                  cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        left = (await native.observe_nav(file_nav(independent_price_usdt=D('.9')), cut=(await native.snapshot(binding)).cut, owner=file_owner)).capital
        right = (await repo.observe_nav(nav(at, ms, independent_price_usdt=D('.9')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)).capital
        for field in ('nav_usdt', 'adjusted_nav_usdt', 'highwater_usdt', 'drawdown_bps', 'execution_loss_usdt',
                      'starting_inventory_pnl_usdt', 'net_cashflows_usdt', 'current'):
            assert getattr(left, field) == getattr(right, field), field
    finally:
        await repo.close()
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['cashflow', 'nav'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_at_cashflow_and_nav_commit(database, kind, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields
    repo, cfg, owner, at, ms = await capital_store(database)
    config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), cut=(await repo.snapshot(cfg.binding)).cut.json(), at=at.isoformat(), ms=ms, kind=kind, committed=committed))
    child = r'''
import asyncio,json,os,sys
from datetime import datetime
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow,nav
async def main():
    d=json.load(sys.stdin); c=d['config']; c['binding']=StateBinding.from_json(c['binding'])
    for key in ('account_id','database_lineage'): c[key]=UUID(c[key])
    runtime=await PgRuntime.open(PgConfig(**c)); repo=PgSpotRepository(runtime); original=runtime._commit
    async def die(tx):
        if d['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    if d['kind']=='nav':
        async def now(connection): return await connection.fetchval('SELECT clock_timestamp()')
        at=await runtime._admit(now,safety=True)
        d['at']=at.isoformat(); d['ms']=int(at.timestamp()*1000)
    print(json.dumps({'at':d['at'],'ms':d['ms']}),flush=True)
    command=(flow if d['kind']=='cashflow' else nav)(datetime.fromisoformat(d['at']),d['ms'])
    await getattr(repo,'apply_cashflow' if d['kind']=='cashflow' else 'observe_nav')(command,cut=FinancialCut.from_json(d['cut']),owner=OwnerToken.from_json(d['owner']))
asyncio.run(main())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, error = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        assert process.returncode == 73, error.decode()
        # Value NAV after interpreter/driver startup, retaining the exact command
        # for cold retry without widening the 1,000 ms synthetic freshness limit.
        source = json.loads(output)
        at, ms = datetime.fromisoformat(source['at']), source['ms']
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        command = flow(at, ms) if kind == 'cashflow' else nav(at, ms)
        result = await getattr(cold, 'apply_cashflow' if kind == 'cashflow' else 'observe_nav')(command, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert result.replayed is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (12 if kind == 'cashflow' else 10)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['cashflow', 'nav'])
async def test_generation_change_at_physical_commit_rolls_back_capital(database, monkeypatch, kind):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, at, ms = await capital_store(database)
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await getattr(repo, 'apply_cashflow' if kind == 'cashflow' else 'observe_nav')(
                flow(at, ms) if kind == 'cashflow' else nav(at, ms), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_stale_nav_cut_owner_price_and_regression_are_denied(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.observe_nav(nav(at, ms, value_at_ms=ms - 2000), cut=before.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.observe_nav(nav(at, ms), cut=before.cut, owner=replace(owner, epoch=2))
        await repo.apply_cashflow(flow(at, ms), cut=before.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.observe_nav(nav(at, ms), cut=before.cut, owner=owner)
        await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.observe_nav(nav(at, ms, operation_id='nav2', value_at_ms=ms - 1), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_late_accounting_after_halt_and_expiry_never_reopens_risk(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        owner = replace(owner, expires_at=owner.valid_from + timedelta(milliseconds=100))
        await database[0].execute("UPDATE life_financial.accounts SET mode='DRAINING',halted=true,owner_until=$1", owner.expires_at)
        await repo.apply_cashflow(flow(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        measured = await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert measured.capital.adjusted_nav_usdt == 15
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('value,expected', [
    ('1.2345678901234567890123456785', '1.234567890123456789012345678'),
    ('1.2345678901234567890123456795', '1.234567890123456789012345680'),
])
async def test_drawdown_keeps_existing_half_even_decimal_contract(database, value, expected):
    assert await database[0].fetchval('SELECT life_financial.spot_drawdown_round($1)', D(value)) == D(expected)


@pytest.mark.asyncio
async def test_declared_replay_limit_keeps_raw_fact_without_unbounded_application(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '2'), ('102', 'USDT', '2')), max_events=1)
    try:
        await repo.apply_cashflow(flow(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        result = await repo.apply_cashflow(flow(at, ms, operation_id='cash2', bill_id='102'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED' and (await repo.balance(cfg.binding)).total_quote_usdt == 12
        assert await database[0].fetchval("SELECT reason FROM life_financial.spot_cashflow_facts WHERE bill_id='102'") == 'LIFE_CAPITAL_REPLAY_BOUND'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_null_sql_command_kind_cannot_publish_an_unclassified_observation(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        args = (cfg.account_id, cfg.binding.json(), owner.json(), before.cut.checkpoint.revision,
                before.cut.checkpoint.state_digest, 'c' * 64, None, nav(at, ms).json())
        with pytest.raises(StorageError):
            await repo._runtime._transaction('SELECT life_financial.spot_capital_command($1,$2,$3,$4,$5,$6,$7,$8)',
                                             args, owner, lambda value: value, observation=True)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
    finally:
        await repo.close()
