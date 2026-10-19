"""Native cohort permission rechecks immutable horizon facts at financial COMMIT."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk import observe, risk_store

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def refresh(database, repo, cfg, owner, risk, operation):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import nav
    at = await database[0].fetchval('SELECT clock_timestamp()')
    await repo.observe_nav(nav(at, int(at.timestamp() * 1000), operation_id=operation), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return await observe(database, repo, cfg, owner, risk, operation + '-risk')


async def bootstrap(database, *, probe=True, max_quote='.25', max_campaign='1', side='BUY', minimum=1):
    repo, cfg, owner, risk = await risk_store(database)
    await database[0].execute('INSERT INTO life_financial.spot_markout_policies VALUES($1,$2,ARRAY[1]::bigint[],$3,1,60000,1000,1000,0,$4::jsonb)', cfg.account_id, cfg.binding.policy_hash, minimum, json.dumps([[side, 'small', 1]]))
    if probe:
        await database[0].execute('INSERT INTO life_financial.risk_markout_probe_policies VALUES($1,$2,$3,$4)', cfg.account_id, cfg.binding.policy_hash, D(max_quote), D(max_campaign))
    await refresh(database, repo, cfg, owner, risk, 'bootstrap-nav')
    ready = await observe(database, repo, cfg, owner, risk, 'risk2')
    return repo, cfg, owner, risk, ready


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['disabled', 'oversize', 'wrong-side'])
async def test_native_insufficient_markout_allows_no_implicit_or_unmatched_bootstrap(database, fault):
    repo, cfg, owner, risk, ready = await bootstrap(database, probe=fault != 'disabled', side='SELL' if fault == 'wrong-side' else 'BUY')
    try:
        before = await repo.snapshot(cfg.binding)
        child = order(owner, risk_epoch=ready.risk_epoch, quantity_base=D('1') if fault == 'oversize' else D('.2'))
        with pytest.raises(StorageError, match='PG_MARKOUT_PROBE_'):
            await repo.prepare(child, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_markout_bootstrap_is_sequential_and_preserves_original_hold_on_arm(database):
    repo, cfg, owner, risk, ready = await bootstrap(database)
    try:
        child = order(owner, risk_epoch=ready.risk_epoch, quantity_base=D('.2'))
        await repo.prepare(child, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        other = replace(child, operation_id='other', intent_id='other', client_order_id='OtherWire', side='SELL', quote_level=1)
        with pytest.raises(StorageError, match='PG_MARKOUT_PROBE_CAPACITY'):
            await repo.prepare(other, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        await refresh(database, repo, cfg, owner, risk, 'arm-nav')
        await repo.arm(child.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.attempt(child.intent_id, cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('price,expected', [('.99', 'MARKOUT_ADVERSE'), ('1', 'MARKOUT_READY'), ('1.01', 'MARKOUT_READY')])
async def test_native_markout_status_agrees_with_existing_fill_cohort_evaluator(database, price, expected):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_markout import marked

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,2,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        risk = PgRiskRepository(repo)
        await markout.observe(replace(value, independent_price_usdt=D(price)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        current = await risk.markout()
        assert current['reason_code'] == (await markout.evaluate(cfg.binding)).reason_code == expected
        assert current['trading_permission'] is False
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('horizon,observe_price,expected', [(60000, None, 'MARKOUT_HORIZON_PENDING'), (1, None, 'MARKOUT_OBSERVATION_MISSING'), (1, '.99', 'MARKOUT_ADVERSE')])
async def test_native_pending_missing_and_adverse_horizons_cannot_use_bootstrap_escape(database, horizon, observe_price, expected):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import nav
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_markout import marked

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, value, markout = await marked(database, horizons=(horizon,), window=max(10000, horizon + 1))
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.risk_markout_probe_policies VALUES($1,$2,1,10)', cfg.account_id, cfg.binding.policy_hash)
        if observe_price is not None:
            await markout.observe(replace(value, independent_price_usdt=D(observe_price)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()'); ms = int(at.timestamp() * 1000)
        await repo.observe_nav(nav(at, ms), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        risk = PgRiskRepository(repo)
        await observe(database, repo, cfg, owner, risk)
        ready = await observe(database, repo, cfg, owner, risk, 'risk2')
        assert ready.state == 'NORMAL'
        assert (await risk.markout())['reason_code'] == expected
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_MARKOUT_PERMISSION_REQUIRED'):
            await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='NewWire', quote_level=1,
                                     quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert before.balance == (await repo.snapshot(cfg.binding)).balance
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_markout_gross_campaign_survives_terminal_orders_and_window_exclusion(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_settlement import cycle

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, _ = await cycle(database)
    try:
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        # The explicit tiny cohort window excludes old samples, but must not
        # refund gross filled volume in the independent bootstrap campaign.
        await database[0].execute('INSERT INTO life_financial.spot_markout_policies VALUES($1,$2,ARRAY[1]::bigint[],1,1,2,1000,1000,0,$3::jsonb)', cfg.account_id, cfg.binding.policy_hash, json.dumps([['BUY', 'small', 1]]))
        await database[0].execute('INSERT INTO life_financial.risk_markout_probe_policies VALUES($1,$2,.25,2.1)', cfg.account_id, cfg.binding.policy_hash)
        risk = PgRiskRepository(repo)
        await refresh(database, repo, cfg, owner, risk, 'gross-nav')
        ready = await observe(database, repo, cfg, owner, risk, 'normal')
        assert (await risk.markout())['reason_code'] == 'MARKOUT_INSUFFICIENT_SAMPLES'
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.send_attempts WHERE state<>'TERMINAL'") == 0
        assert await database[0].fetchval("SELECT sum(quantity) FROM life_financial.fills WHERE product='SPOT'") == 2
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_MARKOUT_PROBE_CAPACITY'):
            await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='NewWire', quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['rows', 'bytes'])
async def test_native_markout_permission_never_uses_a_partial_bounded_history(database, bound):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_markout import marked

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,$3,$4)', cfg.account_id, cfg.binding.policy_hash, 1 if bound == 'rows' else 64, 1 if bound == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_MARKOUT_READ_BOUND'):
            await PgRiskRepository(repo).markout()
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fact', 'attribution', 'receipt'])
async def test_native_markout_cold_read_rejects_corrupt_original_values_without_spending(database, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_markout import marked

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, value, markout = await marked(database)
    try:
        await markout.observe(value, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        before = await repo.snapshot(cfg.binding)
        table = 'spot_markout_facts' if fault == 'fact' else 'attribution' if fault == 'attribution' else 'operations'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        query = 'UPDATE life_financial.' + table + ' SET markout_usdt=0' if fault != 'receipt' else "UPDATE life_financial.operations SET receipt=receipt-'markout_usdt' WHERE operation_id='markout1'"
        await database[0].execute(query)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_MARKOUT_UNVERIFIED'):
            await PgRiskRepository(repo).markout()
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_markout_policies_and_private_permission_helpers_are_not_runtime_writable(database):
    import asyncpg
    repo, cfg, owner, risk, _ = await bootstrap(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute('DELETE FROM life_financial.risk_markout_probe_policies')
        for signature in ('risk_markout_status(uuid)', 'risk_markout_admit(uuid,life_financial.operations)'):
            assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'life_financial.' + signature)
        assert (await risk.markout())['trading_permission'] is False
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('expire_at_commit', [False, True])
async def test_native_ready_markout_is_rechecked_at_physical_prepare_commit(database, monkeypatch, expire_at_commit):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_ingress import setup

    from hummingbot.strategy_v2.life_liquidity.storage.markout import ObserveSpotMarkout
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.markout import PgSpotMarkoutRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.risk import PgRiskRepository
    repo, cfg, owner, fill, intake = await setup(database)
    try:
        await intake.capture_and_apply(fill, owner=owner, max_rows=64, max_bytes=1048576)
        await database[0].execute('INSERT INTO life_financial.risk_service_policies VALUES($1,$2,0,1000,1,0,.25,10,1000,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        risk = PgRiskRepository(repo)
        await refresh(database, repo, cfg, owner, risk, 'ready-nav')
        await observe(database, repo, cfg, owner, risk, 'normal')
        # Independent test horizon is chosen from DB time; it matures shortly
        # after import, then its original cohort window expires 750 ms later.
        now = await database[0].fetchval('SELECT clock_timestamp()')
        original_fill = fill.fills[0]
        horizon = int(now.timestamp() * 1000) - original_fill.fill_at_ms + 10
        await database[0].execute('INSERT INTO life_financial.spot_markout_policies VALUES($1,$2,$3::bigint[],1,2,$4,1000,1000,0,$5::jsonb)', cfg.account_id, cfg.binding.policy_hash, [horizon], horizon + 750, json.dumps([['BUY', 'small', horizon]]))
        target = original_fill.fill_at_ms + horizon
        await database[0].execute('SELECT pg_sleep(greatest(0,($1-floor(extract(epoch FROM clock_timestamp())*1000))/1000))', target)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        marker = ObserveSpotMarkout('ready-horizon', 'trade1', horizon, D('1'), target, target, at)
        await PgSpotMarkoutRepository(repo, max_rows=64, max_bytes=1048576).observe(marker, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        ready = await refresh(database, repo, cfg, owner, risk, 'before-prepare')
        original_commit = repo._runtime._commit
        reached = False

        async def deadline(tx):
            nonlocal reached
            reached = True
            frame = await tx._connection.fetchval('SELECT life_financial.risk_markout_frame($1)', cfg.account_id)
            assert frame['reason_code'] == 'MARKOUT_READY'
            if expire_at_commit:
                await tx._connection.execute('SELECT pg_sleep(greatest(0,($1-floor(extract(epoch FROM clock_timestamp())*1000))/1000)+.01)', target + 750)
            await original_commit(tx)
        monkeypatch.setattr(repo._runtime, '_commit', deadline)
        before = await repo.snapshot(cfg.binding)
        child = order(owner, operation_id='new', intent_id='new', client_order_id='NewWire', quote_level=1, quantity_base=D('.2'), risk_epoch=ready.risk_epoch)
        if expire_at_commit:
            with pytest.raises(StorageError, match='PG_MARKOUT_PROBE_POLICY_REQUIRED'):
                await repo.prepare(child, cut=before.cut, owner=owner)
            after = await repo.snapshot(cfg.binding)
            assert (after.balance, after.budget) == (before.balance, before.budget)
            assert await repo.attempt('new', cfg.binding) is None
        else:
            assert (await repo.prepare(child, cut=before.cut, owner=owner)).state == 'PREPARED'
        assert reached
    finally:
        await repo.close()
