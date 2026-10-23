"""PG6-X1 opt-in capacity gate. Generate real completed native commands.

Source construction is outside measured restore work. No copied receipts,
disabled triggers, empty-history substitution or threshold changes are used.
This suite is intentionally separate from small functional pg6_native cases.
"""
import asyncio
import json
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_fixture import recovered
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import (
    QuoteAdmissionFacts,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from time import perf_counter

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts, TransitionQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import BackupLimits
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import PgInventorySettlementRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

# Explicit qualification workload capacity, matching PG8.7's 1-GiB target.
# Existing quote/COMMIT/actor deadlines are unchanged. The outer measured
# restore/reconciliation budget remains 120 s, including all constituent work.
CAPACITY = BackupLimits(max_tables=512, max_rows=1000000, max_row_bytes=2**20,
                        max_total_bytes=2**30, timeout_ms=120000, max_schema_objects=8192, max_schema_bytes=32 * 2**20)
MODEL = {'synthetic_retained_history': True}
HISTORY_RETENTION = dict(mode='SETTLED_NATIVE_V1', max_retained_tickets=10000,
                         max_retained_body_bytes=128 * 2**20)
RETENTION_MODEL = {**MODEL, 'birth_retention': HISTORY_RETENTION}


async def seed_completed(database, f, cfg, owner, lease, count, progress, *, model=MODEL, hot_tickets=4096, retention=None):
    c = database[0]
    await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',100000)", cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,10000)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,1000)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute("INSERT INTO life_financial.spot_action_policies VALUES($1,$2,'controller1',8,65536,1000)", cfg.account_id, cfg.binding.policy_hash)
    await c.execute("INSERT INTO life_financial.spot_quote_admission_policies VALUES($1,$2,'liquidity_service',0,0,.01,.01,.01,1000,1000,1000,1000)", cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.quote_birth_policies VALUES($1,$2,$3,16777216)', cfg.account_id, cfg.binding.policy_hash, hot_tickets)
    if retention is not None:
        await c.execute('INSERT INTO life_financial.quote_birth_retention_policies VALUES($1,$2,$3,$4,$5)', cfg.account_id,
                        cfg.binding.policy_hash, fingerprint(model), retention['max_retained_tickets'], retention['max_retained_body_bytes'])
        maximum = retention['max_retained_tickets']
    else:
        maximum = await c.fetchval('SELECT max_tickets FROM life_financial.quote_birth_policies WHERE account_id=$1', cfg.account_id)
    assert count <= maximum, (
        f'PG6_X1_SOURCE_PROFILE_UNSUPPORTED: {count} retained provisional births exceed '
        f'the original {maximum}-ticket account policy. This is not a restore timing result.')
    births = PgQuoteBirthRepository(f)
    recorder = PgRecorderRepository(f, controller_id='controller1', max_rows=4096, max_read_bytes=16 * 2**20)
    settlement = PgInventorySettlementRepository(f)
    trades, pair = [], []
    stop = asyncio.Event()

    async def heartbeats():
        n = 0
        while not stop.is_set():
            await lease.heartbeat('capacity-heartbeat:' + str(n))
            n += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=.25)
            except TimeoutError:
                pass
    heartbeat = asyncio.create_task(heartbeats())
    failed = False
    try:
        for n in range(count):
            if heartbeat.done():
                await heartbeat
            key, trade = f'capacity-{n}', f'capacity-trade-{n:05}'
            side = 'BUY' if n % 2 == 0 else 'SELL'
            snapshot = await f.snapshot(cfg.binding)
            at = await c.fetchval('SELECT clock_timestamp()')
            intent = PgSpotQuoteIntent(key, 'controller1', owner, 1, TradeType[side], 0, D('1'), D('1'))
            wire = intent.wire_id
            ticket = await births.issue('birth:' + key, intent, model_hash=fingerprint(model), expected_version=snapshot.cut.checkpoint.revision)
            action = ticket.action
            executor = OrderExecutorConfig.model_validate_json(action.payload_json)
            # Production provisional capture is born after preflight. Original
            # acceptance/preparation commits together; no old quote is re-dated.
            cut = (await f.snapshot(cfg.binding)).cut
            at = await c.fetchval('SELECT clock_timestamp()')
            admission = QuoteAdmissionFacts(cfg.binding, at, at, at, at, D('1'), D('1'), D('.99'), D('1.01'),
                                            D('0'), D('0'), D('0'), D('0'), D('0'), False, 'independent_market')
            prepared = order(owner, operation_id='prepare:' + key, intent_id=key, client_order_id=wire, quantity_base=D('1'), side=side,
                             subsidy_quote_usdt=D('0'), fee_buffer_quote_usdt=D('0'), fee_buffer_base_life=D('0'))
            dispatch = RunnerActionFacts(cfg.binding, at, 'controller1', (key,), True)
            await births.prepare(ticket, PrepareQuoteAction(action, prepared, admission), dispatch, cut=cut, owner=owner)
            info = ExecutorInfo(id=key, timestamp=executor.timestamp, type='order_executor', controller_id='controller1', config=executor,
                                status=RunnableStatus.NOT_STARTED, net_pnl_pct=D('0'), net_pnl_quote=D('0'), cum_fees_quote=D('0'),
                                filled_amount_quote=D('0'), is_active=False, is_trading=False,
                                custom_info={'recovery_account_uid': cfg.binding.account_uid, 'recovery_order_ids': []})
            await recorder.project(ExecutorProjection.from_info('recorder-initial:' + key, cfg.binding, info), owner=owner)
            await f.arm(prepared.operation_id, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            observed = await f.observation_cut(cfg.binding)
            at, ms = observed.observed_at, int(observed.observed_at.timestamp() * 1000)
            row = SpotFillObservation(trade, D('1'), D('1'), 'USDT', D('0'), ms, D('1'), ms, ms, 'independent_market')
            fill = FillSpot('fill:' + key, prepared.operation_id, key, wire, 'LIFE-USDT', side,
                            str(owner.session_id), owner.epoch, str(100000 + n), D('1'), (row,), at)
            await f.apply_fills(fill, cut=observed.cut, owner=owner)
            trades.append(trade)
            snapshot = await f.snapshot(cfg.binding)
            at = await c.fetchval('SELECT clock_timestamp()')
            facts = SpotTerminalFacts(cfg.binding, at, 'FILLED', D('1'), D('1'), D('1'), (row,), snapshot.balance.total_base_life,
                                      snapshot.balance.total_quote_usdt, D('0'), D('0'), tuple(trades), (), (), (), (), True, True, True)
            terminal = TerminalSpot('terminal:' + key, prepared.operation_id, key, wire, 'LIFE-USDT', side,
                                    str(owner.session_id), owner.epoch, str(100000 + n), facts)
            await f.confirm_terminal(terminal, cut=snapshot.cut, owner=owner)
            at = await c.fetchval('SELECT clock_timestamp()')
            facts = RunnerActionFacts(cfg.binding, at, 'controller1', (), True)
            await f.transition_quote_action(TransitionQuoteAction('reconcile:' + key, action, 'PREPARED', 'RECONCILED', facts),
                                            cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            info.status = RunnableStatus.TERMINATED
            info.filled_amount_quote = D('1')
            info.custom_info['recovery_order_ids'] = [wire]
            await recorder.project(ExecutorProgress.from_info('recorder-final:' + key, cfg.binding, 0, info), owner=owner)
            pair.append(key)
            if n % 2:
                snapshot = await f.snapshot(cfg.binding)
                at = await c.fetchval('SELECT clock_timestamp()')
                facts = SpotFinancialRecoveryFacts(cfg.binding, at, snapshot.balance.total_base_life, snapshot.balance.total_quote_usdt,
                                                   D('0'), D('0'), tuple(trades), (), (), (), True, True, True)
                await settlement.settle_inventory(
                    SettleSpotInventory('settlement:' + key, 'cycle:' + key, tuple(pair)), facts=facts, cut=snapshot.cut, owner=owner)
                pair.clear()
            progress['completed_orders'] = n + 1
    except BaseException:
        failed = True
        raise
    finally:
        stop.set()
        # Drain the original physical heartbeat. Cancelling its caller can
        # correctly fence the runtime on an unknown COMMIT, which would make
        # later maintenance on this otherwise valid source unqualified.
        result, = await asyncio.gather(heartbeat, return_exceptions=True)
        if not failed and isinstance(result, BaseException):
            raise result


@pytest.mark.asyncio
@pytest.mark.timeout(1800)
@pytest.mark.parametrize('count', [0, 2, 1000, 10000], ids=['h0', 'sanity2', 'h1k', 'h10k'])
async def test_complete_settled_native_history_restore_capacity(database, tmp_path, request, count):
    values, private, verifier = await source(database, managed_options=dict(owner_duration_ms=1800000,
                                                                            spot_options=dict(session_duration=timedelta(hours=1), safety_size=2)), spot_quote_producer=RETENTION_MODEL)
    f, cfg, owner, _, lease, *_, keys = values
    # PgLiquidityService already refuses fewer than two safety connections.
    # The old repository-only helper default (one) is not an eligible service
    # profile: a long audit can starve the original heartbeat before SQL starts.
    assert cfg.safety_size == 2 and cfg.max_size == 2
    progress = dict(requested_orders=count, completed_orders=0, measured_restore_complete=False,
                    retention_mode=HISTORY_RETENTION['mode'], safety_pool_limit=cfg.safety_size,
                    risk_pool_limit=cfg.max_size, acquire_timeout_ms=cfg.acquire_timeout_ms,
                    statement_timeout_ms=cfg.statement_timeout_ms)
    seed_start = perf_counter()
    try:
        await seed_completed(database, f, cfg, owner, lease, count, progress, model=RETENTION_MODEL, retention=HISTORY_RETENTION)
        progress['source_build_ms'] = round((perf_counter() - seed_start) * 1000, 3)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == count
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_settlements') == count // 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_inventory_members') == count
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == count
        physical = await database[0].fetchval('SELECT pg_database_size(current_database())')
        progress['source_physical_bytes'] = physical
        assert physical <= 2**30
        started = perf_counter()
        async with asyncio.timeout(120):
            async with recovered(database, tmp_path, f, cfg, owner, keys, private, verifier, limits=CAPACITY) as r:
                snapshot = await r.actor.financial.snapshot(cfg.binding)
                assert (snapshot.balance.total_base_life, snapshot.balance.total_quote_usdt) == (D('5'), D('10'))
                assert snapshot.balance.reserved_quote_usdt == snapshot.balance.reserved_base_life == 0
                assert snapshot.budget.campaign_execution_loss_usdt == snapshot.budget.campaign_subsidy_committed_usdt == 0
                assert await r.c.fetchval('SELECT count(*) FROM life_financial.fills') == count
                assert await r.c.fetchval('SELECT count(*) FROM life_financial.spot_inventory_members') == count
                assert await r.c.fetchval('SELECT count(*) FROM life_recorder."Executors"') == count
                progress.update(measured_restore_complete=True, restore_to_observation_ms=round((perf_counter() - started) * 1000, 3),
                                stages=r.timing, archive_bytes=r.bundle.archive_bytes)
        assert progress['restore_to_observation_ms'] <= 120000
    finally:
        progress['source_build_elapsed_ms'] = round((perf_counter() - seed_start) * 1000, 3) if 'source_build_ms' not in progress else progress['source_build_ms']
        request.node.user_properties.append(('life_pg6_capacity', json.dumps(progress)))
        print('life_pg6_capacity=' + json.dumps(progress), flush=True)
        await f.close()


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_retained_source_diagnostic_profile(database, request):
    """Profile the genuine command flow separately from the capacity gate.

    Enable LIFE_PG_TEST_FUNCTION_PROFILE=1 and select this case explicitly.
    Thirty-two source orders are a diagnostic sample, never 1k/10k acceptance.
    """
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    progress = dict(requested_orders=32, completed_orders=0)
    started = perf_counter()
    try:
        await seed_completed(database, f, cfg, owner, lease, 32, progress)
        assert progress['completed_orders'] == 32
    finally:
        progress['source_build_elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
        request.node.user_properties.append(('life_pg6_diagnostic', json.dumps(progress)))
        print('life_pg6_diagnostic=' + json.dumps(progress), flush=True)
        await f.close()
