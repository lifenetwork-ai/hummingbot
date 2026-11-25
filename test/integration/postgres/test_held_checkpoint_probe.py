import asyncio
import json
import threading
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import executor_info, orchestrator
from test.integration.postgres.test_ownership import owned as owned_fixture
from test.integration.postgres.test_recorder import fill
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.core import engine_diagnostics
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.operational_warnings import PositionCheckpointBusy, PositionCheckpointPending
from hummingbot.model.runtime_journal import RuntimeJournal
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors import executor_orchestrator

owned = owned_fixture


def pending_checkpoint(owned, side=TradeType.BUY):
    runtime = orchestrator()
    runtime.strategy.config.mm_drawdown_scope = None
    info = executor_info('hold-probe', side=side.name)
    info.config.mm_session_id = 'hold-probe-session'
    info.config.mm_generation = 0
    owned.recorder._did_fill_order(
        MarketEvent.OrderFilled.value, owned.market,
        fill(order_id='hold-probe-order', trade_type=side, amount=Decimal('.1'), price=Decimal('100'),
             trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))])),
    )
    runtime.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    return runtime


@pytest.mark.parametrize('apply_during_prefetch', [False, True])
def test_held_checkpoint_prefetch_leaves_recorder_slot_free(owned, monkeypatch, apply_during_prefetch):
    runtime = pending_checkpoint(owned)
    prefetch_entered, release_prefetch = threading.Event(), threading.Event()
    lookup = owned.recorder.applied_position_executors

    def prefetch(identities):
        applied = lookup(identities)
        prefetch_entered.set()
        assert release_prefetch.wait(3)
        return applied

    monkeypatch.setattr(owned.recorder, 'applied_position_executors', prefetch)

    async def exercise():
        with pytest.raises(PositionCheckpointPending):
            runtime.get_all_reports()
        task = runtime._position_checkpoint_task
        try:
            assert await asyncio.to_thread(prefetch_entered.wait, 2)
            assert not owned.recorder.order_writes.locked()
            async with owned.recorder.order_writes:
                assert not runtime.executors_ids_position_held
                if apply_during_prefetch:
                    with owned.engine.begin() as connection:
                        connection.execute(text("""
                            INSERT INTO runtime_journal (event_key, kind, applied, payload)
                            VALUES ('hold:hold-probe', 'hold', true, '{}')
                        """))
        finally:
            release_prefetch.set()
            await asyncio.gather(task, return_exceptions=True)
        if apply_during_prefetch:
            with pytest.raises(ValueError, match='Position application changed'):
                task.result()
        else:
            task.result()

    owned.loop.run_until_complete(exercise())
    assert len(owned.recorder.get_all_positions()) == (0 if apply_during_prefetch else 1)
    assert runtime.executors_ids_position_held == (set() if apply_during_prefetch else {'hold-probe'})


@pytest.mark.parametrize('stage', ['read', 'commit'])
def test_slow_held_checkpoint_preserves_live_and_only_publishes_durable_hold(owned, stage, monkeypatch):
    runtime = pending_checkpoint(owned, TradeType.SELL)
    info = runtime.active_executors['maker'][0].executor_info
    info.config.level_id = 'sell_0'
    info.config.use_configured_entry_price = True
    info.custom_info.update(entry_order_filled=True, order_ids=['hold-probe-order'])
    now = [1_000_000_000]
    tracer = engine_diagnostics.EngineDiagnostics(clock=lambda: now[0])
    monkeypatch.setattr(engine_diagnostics, '_ACTIVE', tracer)
    lane, _ = engine_diagnostics._spot_quote_lane(info.config)
    tracer.record_quote_marker('quote.sell_fill', engine_diagnostics._identity('hold-probe-order'), lane=lane)
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        release.wait(1.5)

    def query(connection, cursor, statement, parameters, context, executemany):
        if stage == 'read' and 'FROM runtime_checkpoints' in statement and not entered.is_set():
            hold()
        if stage == 'commit' and statement.startswith('INSERT INTO positions'):
            connection.info['test_hold_commit'] = True

    def commit(connection):
        if connection.info.pop('test_hold_commit', False):
            hold()

    async def exercise():
        checkpoint_ready = owned.owner.position_checkpoint_ready
        controller = SimpleNamespace(config=info.config, executors_update_event=asyncio.Event())
        strategy = runtime.strategy
        strategy.controllers['maker'] = controller
        strategy._managed_report_handle = None
        strategy._managed_report_pending = {}
        strategy.executor_orchestrator = runtime
        strategy.logger = MagicMock()
        strategy.update_executors_info = lambda: StrategyV2Base.update_executors_info(strategy)
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)
        app = web.Application()
        app.router.add_get('/live', ServiceHost.health.__get__(host))
        server = web.AppRunner(app)
        await server.setup()
        await web.TCPSite(server, '127.0.0.1', 0).start()
        port = server.addresses[0][1]

        def probe():
            assert entered.wait(2)
            return probe_health(port, '/live')

        checking = asyncio.create_task(asyncio.to_thread(probe))
        await asyncio.sleep(0)
        try:
            strategy.update_executors_info()
            healthy, detail = await checking
            assert healthy, detail
            task = runtime._position_checkpoint_task
            assert task in owned.owner.terminal_tasks
            assert not checkpoint_ready.is_set()
            with pytest.raises(PositionCheckpointPending):
                runtime.get_all_reports()
            assert runtime._position_checkpoint_task is task
            assert not runtime.positions_held['maker'] and not runtime.executors_ids_position_held
            assert owned.recorder.get_all_positions() == []
            assert not controller.executors_update_event.is_set()
            now[0] += 600_000_000
        finally:
            release.set()
            await server.cleanup()
        await asyncio.wait_for(controller.executors_update_event.wait(), 1)
        assert checkpoint_ready.is_set()
        assert owned.recorder.get_all_positions()[0].amount == Decimal('.1')
        assert len(runtime.positions_held['maker']) == 1
        assert runtime.executors_ids_position_held == {'hold-probe'}
        tracer.record_quote_marker('quote.sell_send', 'replacement', lane=lane)
        reopened = tracer.record_quote_marker('quote.sell_open', 'replacement')
        assert reopened['checkpoint_committed_at_ns'] - reopened['checkpoint_started_at_ns'] == 600_000_000
        assert reopened['release_report_at_ns'] == reopened['checkpoint_committed_at_ns']

    event.listen(owned.engine, 'before_cursor_execute', query)
    event.listen(owned.engine, 'commit', commit)
    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.engine, 'before_cursor_execute', query)
        event.remove(owned.engine, 'commit', commit)
    with owned.manager.get_new_session() as session:
        assert session.query(RuntimeJournal).filter_by(event_key='hold:hold-probe').count() == 1


@pytest.mark.parametrize('locked_seconds', [.45, 1.25])
def test_live_during_held_position_checkpoint(owned, locked_seconds):
    runtime = pending_checkpoint(owned)
    controller = SimpleNamespace(executors_update_event=asyncio.Event())
    controller.executors_update_event.set()
    strategy = SimpleNamespace(
        _managed_report_handle=None, _managed_report_pending={}, executor_orchestrator=runtime,
        controllers={'maker': controller}, logger=MagicMock(), controller_reports={'previous': True},
    )
    row_locked, fence_started = threading.Event(), threading.Event()
    queries = []

    def lock_row():
        with owned.engine.begin() as connection:
            connection.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
            row_locked.set()
            assert fence_started.wait(2)
            threading.Event().wait(locked_seconds)

    def detect_fence(connection, cursor, statement, parameters, context, executemany):
        if 'FROM bot_state' in statement and 'FOR SHARE' in statement and row_locked.is_set():
            queries.append(statement)
            fence_started.set()

    async def exercise():
        host = SimpleNamespace(stop=asyncio.Event(), phase='running', observer_progress_at=None)

        async def live(request):
            return await ServiceHost.health(host, request)

        app = web.Application()
        app.router.add_get('/live', live)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        port = runner.addresses[0][1]
        loop = asyncio.get_running_loop()

        def first_probe():
            assert fence_started.wait(2)
            return probe_health(port, '/live')

        blocker = loop.run_in_executor(None, lock_row)
        try:
            assert await asyncio.to_thread(row_locked.wait, 2)
            probing = loop.run_in_executor(None, first_probe)
            StrategyV2Base.update_executors_info(strategy)
            before = await probing
            if before[0]:
                assert not controller.executors_update_event.is_set()
                assert strategy.controller_reports_error == 'position_checkpoint_pending'
                assert strategy.controller_reports == {'previous': True}
                assert runtime.positions_held['maker'] == []
                assert not runtime.executors_ids_position_held
                assert owned.manager.persistence_healthy
                assert owned.recorder.get_all_positions() == []
            await blocker
            if runtime._position_checkpoint_task is not None:
                with pytest.raises(PositionCheckpointBusy):
                    await runtime._position_checkpoint_task
                StrategyV2Base.update_executors_info(strategy)  # Consume the verified NOWAIT rollback.
                assert strategy.controller_reports_error == 'position_checkpoint_waiting'
            if not controller.executors_update_event.is_set():
                StrategyV2Base.update_executors_info(strategy)
                if runtime._position_checkpoint_task is not None:
                    await runtime._position_checkpoint_task
                    StrategyV2Base.update_executors_info(strategy)
            after = await asyncio.to_thread(probe_health, port, '/live')
        finally:
            await blocker
            await runner.cleanup()
        return before, after

    event.listen(owned.engine, 'before_cursor_execute', detect_fence)
    try:
        first, second = owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.engine, 'before_cursor_execute', detect_fence)
    assert queries and second[0]
    assert strategy.controller_reports_error is None and controller.executors_update_event.is_set()
    assert owned.recorder.get_all_positions()[0].amount == Decimal('.1')
    with owned.manager.get_new_session() as session:
        assert session.query(RuntimeJournal).filter_by(event_key='hold:hold-probe').count() == 1
    print(json.dumps({'first_probe': first[1], 'next_probe': second[1],
                      'one_durable_hold': True, 'initial_fence_nowait': 'NOWAIT' in queries[0]}))
    assert first[0] and 'NOWAIT' in queries[0], first[1]


@pytest.mark.parametrize('worker', [False, True])
def test_held_checkpoint_recovers_after_extended_ownership_contention(owned, monkeypatch, worker):
    runtime = pending_checkpoint(owned)
    now = [100.0]
    monkeypatch.setattr(executor_orchestrator, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    async def start_report():
        with pytest.raises(PositionCheckpointPending):
            runtime.get_all_reports()
        await runtime._position_checkpoint_task

    def attempt_blocked_report():
        if worker:
            with pytest.raises(PositionCheckpointBusy):
                owned.loop.run_until_complete(start_report())
        with pytest.raises(PositionCheckpointBusy):
            runtime.get_all_reports()

    with owned.engine.begin() as connection:
        connection.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        for timestamp in (100.0, 105.0, 110.0):
            now[0] = timestamp
            attempt_blocked_report()
            assert owned.manager.persistence_healthy
            assert not runtime.positions_held['maker'] and not runtime.executors_ids_position_held
            assert owned.recorder.get_all_positions() == []

    # The first tick after the lock clears still observes the retry delay.
    with patch.object(owned.recorder, 'applied_position_executors',
                      wraps=owned.recorder.applied_position_executors) as lookup:
        with pytest.raises(PositionCheckpointBusy):
            runtime.get_all_reports()
        lookup.assert_not_called()
    now[0] += 5.0
    if worker:
        owned.loop.run_until_complete(start_report())
    runtime.get_all_reports()
    runtime.get_all_reports()
    assert runtime.executors_ids_position_held == {'hold-probe'}
    assert [position.amount for position in owned.recorder.get_all_positions()] == [Decimal('.1')]
    with owned.manager.get_new_session() as session:
        assert session.query(RuntimeJournal).filter_by(event_key='hold:hold-probe').count() == 1


def test_held_checkpoint_rechecks_ownership_after_deferral(owned, monkeypatch):
    runtime = pending_checkpoint(owned)
    now = [100.0]
    monkeypatch.setattr(executor_orchestrator, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    async def report():
        with pytest.raises(PositionCheckpointPending):
            runtime.get_all_reports()
        await runtime._position_checkpoint_task

    with owned.engine.begin() as connection:
        connection.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(PositionCheckpointBusy):
            owned.loop.run_until_complete(report())
        with pytest.raises(PositionCheckpointBusy):
            runtime.get_all_reports()
    assert owned.manager.persistence_healthy

    owned.owner.connection.invalidate()
    now[0] += 5.0
    with pytest.raises(OwnershipLost):
        owned.loop.run_until_complete(report())

    assert not owned.manager.persistence_healthy
    assert not runtime.positions_held['maker'] and not runtime.executors_ids_position_held
    assert owned.recorder.get_all_positions() == []
    with owned.manager.get_new_session() as session:
        assert session.query(RuntimeJournal).filter_by(event_key='hold:hold-probe').count() == 0


@pytest.mark.parametrize('worker', [False, True])
def test_held_checkpoint_commit_lock_error_cannot_be_retried_as_prewrite(owned, worker):
    runtime = pending_checkpoint(owned)
    with owned.engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION reject_held_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'deferred lock failure' USING ERRCODE = '55P03';
            END $$;
            CREATE CONSTRAINT TRIGGER reject_held_commit
                AFTER INSERT ON runtime_journal DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW WHEN (NEW.kind = 'hold') EXECUTE FUNCTION reject_held_commit();
        """))

    async def report():
        with pytest.raises(PositionCheckpointPending):
            runtime.get_all_reports()
        await runtime._position_checkpoint_task

    with pytest.raises(OperationalError) as failure:
        if worker:
            owned.loop.run_until_complete(report())
        else:
            runtime.get_all_reports()
    assert failure.value.orig.pgcode == '55P03'
    assert not owned.manager.persistence_healthy
    assert not runtime.positions_held['maker'] and not runtime.executors_ids_position_held
    assert owned.recorder.get_all_positions() == []
    with owned.manager.get_new_session() as session:
        assert session.query(RuntimeJournal).filter_by(event_key='hold:hold-probe').count() == 0
