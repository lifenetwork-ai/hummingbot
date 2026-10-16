"""Exact independent horizon observations over already-accounted SPOT fills."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_ingress import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.markout import ObserveSpotMarkout
from hummingbot.strategy_v2.life_liquidity.storage.postgres.markout import PgSpotMarkoutRepository


async def marked(database, *, horizons=(1,), minimum=1, window=10000, max_age_ms=1000):
    repo, cfg, owner, command, intake = await setup(database)
    await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
    await database[0].execute("""INSERT INTO life_financial.spot_markout_policies VALUES(
        $1,$2,$3::bigint[],$4,2,$5,1000,$6,0,$7::jsonb)""", cfg.account_id, cfg.binding.policy_hash, list(horizons), minimum, window, max_age_ms,
                              __import__('json').dumps([['BUY', 'small', horizons[0]]]))
    at = (await repo.observation_cut(cfg.binding)).observed_at
    target = command.fills[0].fill_at_ms + horizons[0]
    value = ObserveSpotMarkout('markout1', 'trade1', horizons[0], D('.99'), target, target, at)
    return repo, cfg, owner, value, PgSpotMarkoutRepository(repo, max_rows=64, max_bytes=1048576)


@pytest.mark.asyncio
async def test_exact_horizon_is_accounting_neutral_and_matches_native_markout_arithmetic(database):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        before = await repo.snapshot(cfg.binding)
        receipt = await markout.observe(value, cut=before.cut, owner=owner)
        assert receipt.markout_usdt == D('-.01') and not receipt.replayed and not receipt.trading_permission
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert (await markout.observe(value, cut=before.cut, owner=owner)).replayed
        summary = await markout.summary(cfg.binding, side='BUY', size_bucket='small', horizon_ms=1)
        assert summary.sample_count == 1 and summary.mean_markout_quote == D('-.01')
        assert (await markout.lookup('markout1', cfg.binding)).markout_usdt == D('-.01')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(trade_id='foreign'), dict(horizon_ms=2), dict(value_at_ms=1, reference_observed_at_ms=1),
                                     dict(value_at_ms=253402300799000, reference_observed_at_ms=253402300799000)])
async def test_unaccounted_fill_horizon_or_unqualified_value_creates_no_attribution(database, changes):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await markout.observe(replace(value, **changes), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.attribution') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_missing_horizon_is_not_an_empty_ready_cohort_and_adverse_does_not_spend_money(database):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        missing = await markout.evaluate(cfg.binding)
        assert missing.reason_code == 'MARKOUT_OBSERVATION_MISSING' and not missing.cohorts_ready and not missing.trading_permission
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        result = await markout.evaluate(cfg.binding)
        assert result.reason_code == 'MARKOUT_ADVERSE' and not result.cohorts_ready and not result.trading_permission
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_changed_original_horizon_value_or_reused_operation_never_reprices(database):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        for changed in (replace(value, independent_price_usdt=D('2')), replace(value, operation_id='markout2', independent_price_usdt=D('2'))):
            with pytest.raises(StorageError, match='CONFLICT'):
                await markout.observe(changed, cut=before.cut, owner=owner)
        assert (await markout.lookup('markout1', cfg.binding)).markout_usdt == D('-.01')
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['rows', 'bytes'])
async def test_explicit_read_bounds_never_return_partial_cohort_as_ready(database, bound):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        limited = PgSpotMarkoutRepository(repo, max_rows=1 if bound == 'rows' else 64, max_bytes=8 if bound == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_MARKOUT_READ_BOUND'):
            await limited.evaluate(cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('horizons,window,cohorts', [
    ([1, 1], 10000, [["BUY", "small", 1]]), ([None], 10000, [["BUY", "small", 1]]),
    ([0], 10000, [["BUY", "small", 1]]), ([1], 1, [["BUY", "small", 1]]),
    ([1], 10000, [["BUY", "small", 1], ["BUY", "small", 1]]), ([1], 10000, [["BUY", "huge", 1]]),
    ([1], 10000, [["BOTH", "small", 1]]), ([1], 10000, [["BUY", "small", 2]]),
    ([1], 10000, [["BUY", "small", True]]), ([1], 10000, [["BUY", "small", "1"]]),
    ([1], 10000, [["BUY", "small"]]), ([1], 10000, []), ([1], 10000, [None]),
])
async def test_malformed_imported_policy_is_rejected_before_it_can_define_risk(database, horizons, window, cohorts):
    import json
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

    import asyncpg
    runtime, cfg, owner = await backend(database)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await database[0].execute('''INSERT INTO life_financial.spot_markout_policies VALUES(
                $1,$2,$3::bigint[],1,2,$4,1000,1000,0,$5::jsonb)''', cfg.account_id, cfg.binding.policy_hash, horizons, window, json.dumps(cohorts))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_markout_policies') == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('price,minimum,expected', [(D('1.01'), 1, 'MARKOUT_READY'), (D('1.01'), 2, 'MARKOUT_INSUFFICIENT_SAMPLES')])
async def test_cohort_arithmetic_matches_shared_file_tracker_without_granting_risk(database, price, minimum, expected):
    from hummingbot.strategy_v2.life_liquidity.markout import MarkoutTracker
    repo, cfg, owner, value, markout = await marked(database, minimum=minimum)
    try:
        value = replace(value, independent_price_usdt=price)
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        frame = await markout.frame(cfg.binding)
        row = frame['fills'][0]
        file_tracker = MarkoutTracker(horizons_ms=(1,), min_samples=minimum, size_bucket_edges=(D(2),))
        file_tracker.record_fill('trade1', 'BUY', D(row['quantity_base']), D(row['price_usdt']), fill_at_ms=row['fill_at_ms'])
        file_tracker.observe('trade1', 1, price, observed_at_ms=value.reference_observed_at_ms, source_kind='independent_market')
        result = await markout.evaluate(cfg.binding)
        assert result.reason_code == expected and result.cohorts_ready == (minimum == 1) and not result.trading_permission
        assert result.summaries[0][3] == file_tracker.summary('BUY', 'small', 1, now_ms=frame['now_ms'])
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unmatured_horizon_and_window_exclusion_do_not_fabricate_samples(database):
    repo, cfg, owner, value, markout = await marked(database, horizons=(100000,), window=200000)
    try:
        result = await markout.evaluate(cfg.binding)
        assert result.reason_code == 'MARKOUT_HORIZON_PENDING' and not result.cohorts_ready
        with pytest.raises(StorageError, match='PG_MARKOUT_VALUE_UNQUALIFIED'):
            await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fact', 'missing_fact', 'missing_attribution', 'attribution', 'receipt', 'receipt_missing', 'command'])
async def test_missing_or_corrupt_original_markout_never_becomes_verified(database, fault):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        table = 'spot_markout_facts' if fault in ('fact', 'missing_fact') else 'attribution' if fault in ('missing_attribution', 'attribution') else 'operations'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if fault.startswith('missing_'):
            await database[0].execute('DELETE FROM life_financial.' + table)
        elif fault == 'fact':
            await database[0].execute('UPDATE life_financial.spot_markout_facts SET markout_usdt=0')
        elif fault == 'attribution':
            await database[0].execute('UPDATE life_financial.attribution SET markout_usdt=0')
        elif fault == 'command':
            await database[0].execute("UPDATE life_financial.operations SET command=command #- '{command,source_kind}' WHERE operation_id='markout1'")
        else:
            await database[0].execute("UPDATE life_financial.operations SET receipt=" + ("receipt-'markout_usdt'" if fault == 'receipt_missing' else "jsonb_set(receipt,'{trade_id}','\"other\"')") + " WHERE operation_id='markout1'")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_MARKOUT_UNVERIFIED'):
            await markout.evaluate(cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_readonly_and_foreign_scope_cannot_write_or_invoke_private_proofs(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, value, markout = await marked(database)
    reader = c = None
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        rcfg = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reader = PgSpotRepository(await PgRuntime.open(rcfg))
        store = PgSpotMarkoutRepository(reader, max_rows=64, max_bytes=1048576)
        assert (await store.lookup('markout1', cfg.binding)).markout_usdt == D('-.01')
        with pytest.raises(StorageError):
            await store.observe(replace(value, operation_id='reader'), cut=(await reader.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError, match='BINDING_MISMATCH'):
            await store.evaluate(replace(cfg.binding, account_uid='999'))
        c = await asyncpg.connect(cfg.dsn)
        for query in ('SELECT life_financial.spot_markouts_verified($1)',
                      'UPDATE life_financial.spot_markout_facts SET markout_usdt=0 WHERE account_id=$1',
                      'DELETE FROM life_financial.spot_markout_policies WHERE account_id=$1'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(query, cfg.account_id)
    finally:
        if c is not None:
            await c.close()
        if reader is not None:
            await reader.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('physical', ['generation', 'age'])
async def test_physical_commit_rechecks_generation_and_original_fact_age(database, monkeypatch, physical):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, value, markout = await marked(database)
    original = repo._runtime._commit
    if physical == 'generation':
        await install_generation_fault(database)

    async def fault(tx):
        if physical == 'generation':
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        else:
            await asyncio.sleep(1.05)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', fault)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT' if physical == 'generation' else 'PG_MARKOUT_FACTS_STALE_AT_COMMIT'):
            await markout.observe(value, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.attribution') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_commit_loss_recovers_original_horizon_without_double_publication(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.markout_process import exit_markout

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, value, markout = await marked(database, max_age_ms=10000)  # Explicit child-process-start fixture allowance.
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        before = await repo.snapshot(cfg.binding)
        if fault == 'process':
            await exit_markout(cfg, value, owner, committed)
        else:
            original = repo._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic markout COMMIT reply lost')
            monkeypatch.setattr(repo._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await markout.observe(value, cut=before.cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgSpotMarkoutRepository(cold, max_rows=64, max_bytes=1048576)
        found = await restored.lookup('markout1', cfg.binding)
        assert (found is not None) == committed
        if not committed:
            fresh = replace(value, observed_at=(await cold.observation_cut(cfg.binding)).observed_at)
            await restored.observe(fresh, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
            value = fresh
        current = await cold.snapshot(cfg.binding)
        replay = await restored.observe(value, cut=before.cut, owner=owner)
        assert replay.replayed and replay.markout_usdt == D('-.01')
        assert (await cold.snapshot(cfg.binding)).cut == current.cut and current.balance == before.balance and current.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_markout_facts') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.attribution') == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
async def test_aged_out_fill_is_not_a_ready_empty_cohort_and_keeps_permanent_facts(database):
    repo, cfg, owner, value, markout = await marked(database, window=2)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute('SELECT pg_sleep(.005)')
        frame = await markout.frame(cfg.binding)
        assert frame['fills'] == frame['observations'] == []
        result = await markout.evaluate(cfg.binding)
        assert result.reason_code == 'MARKOUT_INSUFFICIENT_SAMPLES' and not result.cohorts_ready
        assert (await markout.lookup('markout1', cfg.binding)).markout_usdt == D('-.01')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'horizon_ms': True}, {'horizon_ms': '1'}, {'value_at_ms': 1.5},
                                     {'independent_price_usdt': 1.01}, {'source_kind': None}, {'observed_at': '2026-10-17T00:00:00'}])
async def test_direct_sql_command_cannot_bypass_exact_wire_value_types(database, changes):
    import json

    import asyncpg
    repo, cfg, owner, value, markout = await marked(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        before = await repo.snapshot(cfg.binding)
        body = value.json()
        body.update(changes)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_MARKOUT_COMMAND_INVALID'):
            await c.fetchval('SELECT life_financial.spot_observe_markout($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), before.cut.checkpoint.revision,
                             repo._cut(before.cut, owner)[1], 'f' * 64, json.dumps(body))
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_direct_sql_must_bind_permanent_fingerprint_to_original_command(database):
    import json

    import asyncpg
    repo, cfg, owner, value, markout = await marked(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_MARKOUT_FINGERPRINT_INVALID'):
            await c.fetchval('SELECT life_financial.spot_observe_markout($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), before.cut.checkpoint.revision,
                             repo._cut(before.cut, owner)[1], 'f' * 64, json.dumps(value.json()))
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_original_observation_has_one_commit_and_one_historical_readback(database):
    import asyncio
    repo, cfg, owner, value, markout = await marked(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(*(markout.observe(value, cut=cut, owner=owner) for _ in range(2)))
        assert sorted(row.replayed for row in results) == [False, True]
        assert all(row.markout_usdt == D('-.01') and not row.trading_permission for row in results)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.attribution') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_buy_sell_markout_signs_use_native_fill_terms_and_do_not_recompute_fees(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_settlement import cycle
    repo, cfg, owner, settlement = await cycle(database)
    try:
        await database[0].execute('''INSERT INTO life_financial.spot_markout_policies VALUES(
            $1,$2,ARRAY[1]::bigint[],1,2,10000,1000,1000,0,'[["BUY","small",1],["SELL","small",1]]')''', cfg.account_id, cfg.binding.policy_hash)
        store = PgSpotMarkoutRepository(repo, max_rows=64, max_bytes=1048576)
        before = await repo.snapshot(cfg.binding)
        frame = await store.frame(cfg.binding)
        for index, row in enumerate(frame['fills']):
            target = row['fill_at_ms'] + 1
            at = (await repo.observation_cut(cfg.binding)).observed_at
            value = ObserveSpotMarkout('signed' + str(index), row['trade_id'], 1, D('1.1'), target, target, at)
            result = await store.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            assert result.markout_usdt == (D('.1') if row['side'] == 'BUY' else D('-.1'))
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        decision = await store.evaluate(cfg.binding)
        assert decision.reason_code == 'MARKOUT_ADVERSE' and not decision.trading_permission
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_qualified_late_markout_after_halt_cannot_restore_activation(database):
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await database[0].execute("UPDATE life_financial.accounts SET mode='DRAINING',halted=true WHERE account_id=$1", cfg.account_id)
        before = await repo.snapshot(cfg.binding)
        value = replace(value, observed_at=(await repo.observation_cut(cfg.binding)).observed_at)
        receipt = await markout.observe(value, cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert not receipt.trading_permission and after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval("SELECT mode='DRAINING' AND halted FROM life_financial.accounts WHERE account_id=$1", cfg.account_id)
    finally:
        await repo.close()
