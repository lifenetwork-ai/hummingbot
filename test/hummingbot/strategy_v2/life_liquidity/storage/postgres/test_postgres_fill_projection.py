"""Experimental indexed monetary prefixes, never a runtime integrity verdict."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

SCHEMA = Path(__file__).with_name('fill_projection_spike.sql')
SOURCES = Path(__file__).with_name('fill_projection_sources.sql')
ECONOMICS = Path(__file__).with_name('fill_projection_economics.sql')
SUBSIDY = Path(__file__).with_name('fill_projection_subsidy.sql')
WALLET = Path(__file__).with_name('fill_projection_wallet.sql')
BUDGET_STATE = Path(__file__).with_name('fill_projection_budget_state.sql')
COMPLETENESS = Path(__file__).with_name('fill_projection_completeness.sql')
PRODUCTS = Path(__file__).with_name('fill_projection_products.sql')


async def install(c, *, sources=True, economics=False, wallet=True, products=False):
    scripts = [SCHEMA]
    if sources:
        scripts.append(SOURCES)
    if products:
        assert sources
        scripts.append(PRODUCTS)
    if economics:
        assert sources
        scripts.extend((ECONOMICS, SUBSIDY, BUDGET_STATE))
        if wallet:
            scripts.append(WALLET)
        scripts.append(COMPLETENESS)
    # One failed extension must not leave a partially enrolled base schema.
    # Strip only each trusted file's transaction envelope; PL/pgSQL BEGIN/END
    # bodies remain unchanged. Runtime never invokes this test installer.
    async with c.transaction():
        for path in scripts:
            sql = path.read_text().strip()
            assert '\nBEGIN;\n' in sql and sql.endswith('COMMIT;')
            await c.execute(sql.replace('\nBEGIN;\n', '\n', 1)[:-len('COMMIT;')])


async def prefix(c, account):
    return await c.fetchrow('''SELECT ordinal,base_after,quote_after FROM fill_projection_spike.money
        WHERE account_id=$1 ORDER BY ordinal DESC LIMIT 1''', account)


async def budgets(c, account):
    return await c.fetch('''SELECT b.family,b.scope,b.window_key,b.committed,b.incurred,
        p.committed_after,p.incurred_after FROM life_financial.budget_buckets b
        LEFT JOIN LATERAL (SELECT committed_after,incurred_after FROM fill_projection_spike.budget p
            WHERE (p.account_id,p.family,p.scope,p.window_key)=(b.account_id,b.family,b.scope,b.window_key)
            ORDER BY ordinal DESC LIMIT 1) p ON true WHERE b.account_id=$1 ORDER BY b.family,b.scope,b.window_key''', account)


async def ready(database, *, sources=True, economics=False, wallet=True, side='BUY'):
    # Bootstrap the witness before any event. Existing balances/budget buckets
    # remain the sole production projections; the prototype adds no authority.
    await install(database[0], sources=sources, economics=economics, wallet=wallet)
    repo, cfg, owner = await spot(database)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        await repo.prepare(order(owner, side=side), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        return repo, cfg, owner
    except BaseException:
        await repo.close()
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize('asset', ['LIFE', 'USDT'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_fill_projection_real_fills_fees_rebates_and_replay_match_native(database, asset, side):
    repo, cfg, owner = await ready(database, side=side)
    c = database[0]
    try:
        opening = await repo.snapshot(cfg.binding)
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at, side=side), cut=opening.cut, owner=owner)
        cmd = filled(owner, at, side=side)
        reference = D('.9') if side == 'BUY' else D('1.1')
        cmd = replace(cmd, fills=(replace(cmd.fills[0], fee_currency=asset, independent_price_usdt=reference),))
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        first = await prefix(c, cfg.account_id)
        rebate = replace(cmd.fills[0], trade_id='trade2', independent_price_usdt=D('2') - reference, signed_fee=D('.01'))
        cmd2 = replace(cmd, operation_id='fill2', fills=(*cmd.fills, rebate), cumulative_base_life=D('2'))
        await repo.apply_fills(cmd2, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        current = await prefix(c, cfg.account_id)
        native = await repo.snapshot(cfg.binding)
        assert current['ordinal'] == first['ordinal'] + 1
        assert opening.balance.total_base_life + current['base_after'] == native.balance.total_base_life
        assert opening.balance.total_quote_usdt + current['quote_after'] == native.balance.total_quote_usdt
        loss = D('.1') + D('.02') * (reference if asset == 'LIFE' else D(1))
        assert native.budget.session_execution_loss_usdt == loss
        for b in await budgets(c, cfg.account_id):
            assert b['committed_after'] == b['committed']
            assert b['incurred_after'] == b['incurred']
        before = await c.fetchval('SELECT count(*) FROM fill_projection_spike.budget')
        assert (await repo.apply_fills(cmd2, cut=native.cut, owner=owner)).replayed
        assert await prefix(c, cfg.account_id) == current
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.budget') == before
        assert (await repo.snapshot(cfg.binding)).cut == native.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table,column', [('financial_events', 'quote_delta'), ('budget_events', 'amount')])
async def test_fill_projection_old_source_edit_is_rejected_with_user_triggers_disabled(database, table, column):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        original = await prefix(c, cfg.account_id)
        # Normal owner writes after DISABLE USER still execute PostgreSQL's
        # internal FK enforcement. No stronger DBA/storage guarantee is claimed.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(f'UPDATE life_financial.{table} SET {column}={column}+1 WHERE account_id=$1', cfg.account_id)
        assert await prefix(c, cfg.account_id) == original
        assert await c.fetchval('SELECT life_financial.spot_accounting_verified($1)', cfg.account_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('trigger,table', [('prototype_money', 'financial_events'),
                                           ('prototype_fill_source', 'financial_events'), ('prototype_budget', 'budget_events')])
async def test_fill_projection_missing_capture_aborts_native_money_and_budget_at_commit(database, trigger, table):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        before = await repo.snapshot(cfg.binding)
        await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER {trigger}')
        at = await c.fetchval('SELECT clock_timestamp()')
        with pytest.raises(StorageError):
            await repo.apply_fills(filled(owner, at), cut=before.cut, owner=owner)
        await c.execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER {trigger}')
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await prefix(c, cfg.account_id) is None
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
        for b in await budgets(c, cfg.account_id):
            assert b['committed_after'] == b['committed']
            assert b['incurred_after'] == b['incurred']
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_projection_actual_overbudget_loss_is_booked_without_refund(database):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        before = await repo.snapshot(cfg.binding)
        at = await c.fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        row = replace(cmd.fills[0], quantity_base_life=D(2), independent_price_usdt=D('.001'))
        await repo.apply_fills(replace(cmd, fills=(row,), cumulative_base_life=D(2)), cut=before.cut, owner=owner)
        native = await repo.snapshot(cfg.binding)
        assert native.budget.loss_exhausted and native.budget.session_execution_loss_usdt == D('2.018')
        assert before.balance.total_quote_usdt + (await prefix(c, cfg.account_id))['quote_after'] == native.balance.total_quote_usdt
        for b in await budgets(c, cfg.account_id):
            assert (b['committed_after'], b['incurred_after']) == (b['committed'], b['incurred'])
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['money_arithmetic', 'money_parent', 'money_tail', 'budget_arithmetic', 'budget_tail'])
async def test_fill_projection_broken_prefix_or_missing_tail_cannot_commit(database, target):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        old = await prefix(c, cfg.account_id)
        sql = {
            'money_arithmetic': 'UPDATE fill_projection_spike.money SET quote_after=quote_after+1',
            # NULL must not defeat the composite FK via CHECK three-valued logic.
            'money_parent': 'UPDATE fill_projection_spike.money SET previous=NULL WHERE ordinal>1',
            'money_tail': 'DELETE FROM fill_projection_spike.money WHERE ordinal=(SELECT max(ordinal) FROM fill_projection_spike.money)',
            'budget_arithmetic': 'UPDATE fill_projection_spike.budget SET incurred_after=incurred_after+1',
            'budget_tail': "DELETE FROM fill_projection_spike.budget WHERE family='LOSS'",
        }[target]
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            async with c.transaction():
                await c.execute('ALTER TABLE fill_projection_spike.money DISABLE TRIGGER USER')
                await c.execute('ALTER TABLE fill_projection_spike.budget DISABLE TRIGGER USER')
                assert await c.execute(sql) not in ('UPDATE 0', 'DELETE 0')
        assert await prefix(c, cfg.account_id) == old
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_projection_source_witness_is_not_complete_fill_provenance(database, request):
    repo, cfg, owner = await ready(database, sources=False)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        original = await prefix(c, cfg.account_id)
        async with c.transaction():
            await c.execute('ALTER TABLE life_financial.fees DISABLE TRIGGER USER')
            assert await c.execute('UPDATE life_financial.fees SET signed_fee=signed_fee-1 WHERE account_id=$1', cfg.account_id) != 'UPDATE 0'
            # An unchanged event/aggregate is NOT proof that its source fill or
            # fee remains intact. This counterexample blocks runtime promotion.
            assert await prefix(c, cfg.account_id) == original
            assert not await c.fetchval('SELECT life_financial.spot_accounting_verified($1)', cfg.account_id)
            request.node.user_properties.append(('prototype_promotion', 'BLOCKED_UNCOVERED_FEE_PROVENANCE'))
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table,change', [
    ('fees', 'signed_fee=signed_fee-1'),
    ('fills', 'price=price+1'),
    ('spot_fill_facts', "observation=jsonb_set(observation,'{signed_fee}','\"-.03\"'::jsonb)"),
    ('spot_fill_facts', 'execution_loss=execution_loss+1'),
])
async def test_fill_projection_bound_fill_sources_reject_history_edits(database, table, change):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        original = await prefix(c, cfg.account_id)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(f'UPDATE life_financial.{table} SET {change} WHERE account_id=$1', cfg.account_id)
        assert await prefix(c, cfg.account_id) == original
        assert await c.fetchval('SELECT life_financial.spot_accounting_verified($1)', cfg.account_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_fill_projection_unknown_commit_cold_retry_applies_once(database, monkeypatch, committed):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        at = await c.fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        cut = (await repo.snapshot(cfg.binding)).cut
        original_commit = repo._runtime._commit

        async def lost(tx):
            if committed:
                await original_commit(tx)
            else:
                await tx.rollback()
            raise ConnectionError('synthetic lost commit reply')

        monkeypatch.setattr(repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await repo.apply_fills(cmd, cut=cut, owner=owner)
    finally:
        await repo.close()
    store = await PgRuntime.open(cfg)
    cold = PgSpotRepository(store)
    try:
        before = await prefix(c, cfg.account_id)
        assert (before is not None) is committed
        result = await cold.apply_fills(cmd, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert result.replayed is committed
        assert (await prefix(c, cfg.account_id))['ordinal'] == 1
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.fill_source') == 1
        for b in await budgets(c, cfg.account_id):
            assert (b['committed_after'], b['incurred_after']) == (b['committed'], b['incurred'])
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_fill_projection_completed_native_pair_keeps_release_spend_and_recorder(database):
    await install(database[0])
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))), spot_quote_producer=MODEL)
    repo, cfg, owner, _, lease, *_ = values
    try:
        opening = await repo.snapshot(cfg.binding)
        await seed_completed(database, repo, cfg, owner, lease, 2, {})
        view = await repo.snapshot(cfg.binding)
        head = await prefix(database[0], cfg.account_id)
        assert opening.balance.total_base_life + head['base_after'] == view.balance.total_base_life
        assert opening.balance.total_quote_usdt + head['quote_after'] == view.balance.total_quote_usdt
        kinds = set(await database[0].fetchval('SELECT array_agg(DISTINCT kind) FROM fill_projection_spike.budget'))
        assert kinds == {'COMMIT', 'RELEASE', 'SPEND'}
        assert await database[0].fetchval('SELECT count(*) FROM fill_projection_spike.fill_source') == 2
        for b in await budgets(database[0], cfg.account_id):
            assert (b['committed_after'], b['incurred_after']) == (b['committed'], b['incurred'])
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 1000, 10000])
async def test_fill_projection_synthetic_prefix_index_work_is_bounded(database, request, count):
    # Deliberately only a synthetic append/index experiment: these ACCOUNT
    # events have no native approvals/receipts/hold/recovery ancestry. They are
    # NOT a populated PG6 source, send workload or a capacity acceptance pass.
    c = database[0]
    await install(c, sources=False)
    repo, cfg, owner = await spot(database)
    try:
        await c.execute("INSERT INTO life_financial.budget_buckets VALUES($1,'LOSS','CAMPAIGN','index-only',100000,0,0,0)", cfg.account_id)
        await c.execute('SET statement_timeout=1000')
        for start in range(0, count, 50):
            async with c.transaction():
                await c.execute('''INSERT INTO life_financial.financial_events(
                    account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                    SELECT $1,'index-only-'||n,'ACCOUNT','CASHFLOW','index-only-'||n,clock_timestamp(),repeat('a',64),.01,-.02
                    FROM generate_series($2::integer,$3::integer) n''', cfg.account_id, start, min(start + 49, count - 1))
                await c.execute('''INSERT INTO life_financial.budget_events
                    SELECT $1,'index-only-'||n,'LOSS','CAMPAIGN','index-only','SPEND',.03
                    FROM generate_series($2::integer,$3::integer) n''', cfg.account_id, start, min(start + 49, count - 1))
        for table in ('money', 'budget'):
            await c.execute(f'ANALYZE fill_projection_spike.{table}')
        queries = (
            'SELECT ordinal,base_after,quote_after FROM fill_projection_spike.money WHERE account_id=$1 ORDER BY ordinal DESC LIMIT 1',
            "SELECT ordinal,incurred_after FROM fill_projection_spike.budget WHERE account_id=$1 AND family='LOSS' AND scope='CAMPAIGN' AND window_key='index-only' ORDER BY ordinal DESC LIMIT 1",
        )
        reports = []
        for query in queries:
            report = json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + query, cfg.account_id))[0]
            reports.append(report)
            if count:
                nodes = [report['Plan']]
                while nodes:
                    node = nodes.pop()
                    assert node['Actual Rows'] <= 1
                    assert node['Node Type'] not in ('Seq Scan', 'Sort')
                    nodes.extend(node.get('Plans', ()))
        head = await prefix(c, cfg.account_id)
        assert (head is None) == (count == 0)
        if count:
            assert (head['ordinal'], head['base_after'], head['quote_after']) == (count, D('.01') * count, -D('.02') * count)
        request.node.user_properties.append(('synthetic_prefix_index_only', json.dumps(dict(count=count, plans=reports))))
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_projection_concurrent_native_fills_do_not_fork_prefix(database):
    repo, cfg, owner = await ready(database)
    c = database[0]
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        cmd = filled(owner, await c.fetchval('SELECT clock_timestamp()'))
        other = replace(cmd, operation_id='competing-fill', fills=(replace(cmd.fills[0], trade_id='competing-trade'),))
        results = await asyncio.gather(*(repo.apply_fills(row, cut=cut, owner=owner) for row in (cmd, other)), return_exceptions=True)
        assert sum(isinstance(row, StorageError) for row in results) == 1
        assert sum(not isinstance(row, BaseException) for row in results) == 1
        assert (await prefix(c, cfg.account_id))['ordinal'] == 1
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.fill_source') == 1
        for b in await budgets(c, cfg.account_id):
            assert (b['committed_after'], b['incurred_after']) == (b['committed'], b['incurred'])
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_projection_synthetic_budget_windows_never_merge_or_reset(database):
    # Synthetic arithmetic only: distinct fixed window keys simulate rollover;
    # no original production observation or clock is re-dated.
    c = database[0]
    await install(c, sources=False)
    repo, cfg, owner = await spot(database)
    try:
        windows = [('DAY', '2026-01-01'), ('DAY', '2026-01-02'), ('SESSION', 'session-a'),
                   ('SESSION', 'session-b'), ('CAMPAIGN', 'campaign-a')]
        for scope, key in windows:
            await c.execute("INSERT INTO life_financial.budget_buckets VALUES($1,'SUBSIDY',$2,$3,100,0,0,0)", cfg.account_id, scope, key)
            for index, (kind, amount) in enumerate((('COMMIT', D('.3')), ('SPEND', D('.2')), ('RELEASE', D('.1')))):
                await c.execute("INSERT INTO life_financial.budget_events VALUES($1,$2,'SUBSIDY',$3,$4,$5,$6)",
                                cfg.account_id, key + str(index), scope, key, kind, amount)
        # Indexed per-window heads preserve gross SPEND separately from hold
        # RELEASE. Opening the next window does not refund the previous one.
        for scope, key in windows:
            row = await c.fetchrow('''SELECT ordinal,committed_after,incurred_after FROM fill_projection_spike.budget
                WHERE account_id=$1 AND family='SUBSIDY' AND scope=$2 AND window_key=$3 ORDER BY ordinal DESC LIMIT 1''', cfg.account_id, scope, key)
            assert tuple(row) == (3, D('.2'), D('.2'))
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fill_projection_has_no_runtime_permissions(database):
    c = database[0]
    await install(c)
    assert not await c.fetchval("SELECT has_schema_privilege('life_pg2_runtime','fill_projection_spike','USAGE')")
    assert not await c.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='fill_projection_spike'::regnamespace
        AND relkind='r' AND (has_table_privilege('life_pg2_runtime',oid,'SELECT') OR has_table_privilege('life_pg2_runtime',oid,'INSERT')
            OR has_table_privilege('life_pg2_runtime',oid,'UPDATE') OR has_table_privilege('life_pg2_runtime',oid,'DELETE')))''')
    assert not await c.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace='fill_projection_spike'::regnamespace
        AND has_function_privilege('life_pg2_runtime',oid,'EXECUTE'))''')
