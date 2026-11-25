"""Offline composition replay of quote gaps; no exchange, cloud or live database.

Run native controller/executor loops, action listener, account watcher and report
publication together. Fake only venue/account observations and persistence. The
long periodic interval isolates event progress from the regular clock fallback;
it is not a proposed production setting or a Cloud latency benchmark.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_filled_entry_handoff import account_observed
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import fill, observe_account, setup_lifecycle
from threading import Event
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import TrackedOrder


async def eventually(predicate, *, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), timeout)


@asynccontextmanager
async def replay(monkeypatch, side, *, state=None, peers=None):
    if state is None:
        state = await setup_lifecycle(
            monkeypatch, 'okx', side, initial=(2000, 0), executor_refresh_time=60,
            runtime_config_managed=True, mm_session_id='offline-replay', mm_generation=1)
    controller, strategy, executor = state.controller, state.strategy, state.executor
    # Cloud's enabled spread policy seeds configured-price maker entries. The
    # shared lifecycle fixture defaults to the legacy (unconfigured) variant.
    executor.config = executor.config.model_copy(update={
        'use_configured_entry_price': True, 'controller_id': controller.config.id})
    strategy.executor_orchestrator = state.orchestrator
    strategy.market_data_provider = controller.market_data_provider
    strategy.actions_queue = controller.actions_queue
    strategy._is_stop_triggered = False
    strategy._managed_report_pending = {}
    strategy._managed_report_handle = None
    strategy.MANAGED_REPORT_COALESCE_SECONDS = StrategyV2Base.MANAGED_REPORT_COALESCE_SECONDS
    strategy.controller_reports = {}
    strategy.controller_reports_error = None
    for name in ('notify_executor_terminated', 'notify_executor_entry_filled',
                 '_queue_managed_executor_report', '_publish_managed_terminal_reports',
                 'update_executors_info', 'get_executors_by_controller',
                 'listen_to_executor_actions', '_watch_spot_account_progress'):
        setattr(strategy, name, MethodType(getattr(StrategyV2Base, name), strategy))
    recorder = MarketsRecorder.get_instance()
    recorder.durable_position_checkpoints = True
    recorder.applied_position_executors.return_value = set()
    monkeypatch.setattr(MarketsRecorder, '_shared_instance', recorder)
    # Keep the opposite quote resident, so only the replayed level needs replacing.
    if peers is None:
        opposite = TradeType.SELL if side == TradeType.BUY else TradeType.BUY
        peers = [controller.get_executor_config('sell_0' if opposite == TradeType.SELL else 'buy_0',
                                                Decimal('.101') if opposite == TradeType.SELL else Decimal('.099'),
                                                Decimal('600'))]
    for config in peers:
        other = PositionExecutor(strategy, config)
        other.config.controller_id = controller.config.id
        other._status = RunnableStatus.RUNNING
        order_id = f'peer-{config.level_id}'
        other._open_order = TrackedOrder(order_id)
        other._open_order.order = InFlightOrder(
            order_id, config.trading_pair, state.order.order_type, config.side, config.amount, strategy.current_timestamp,
            config.entry_price, exchange_order_id=f'{order_id}-exchange', initial_state=OrderState.OPEN)
        state.orchestrator.active_executors[controller.config.id].append(other)
    executor.get_in_flight_order = lambda *args: state.order
    executor.validate_sufficient_balance = AsyncMock()
    controller.on_start = AsyncMock()  # The already admitted fixture has no network startup.
    controller.update_interval = executor.update_interval = 60
    controller._status = RunnableStatus.RUNNING
    state.account_event = asyncio.Event()
    state.creates = []
    state.created = asyncio.Event()
    execute_actions = state.orchestrator.execute_actions
    controller_waiting = asyncio.Event()
    wait_for_tick = controller._wait_for_control_tick

    async def observe_controller_wait():
        # Synchronize with the first completed control cycle without polling
        # a transient diagnostic stage on a loaded coverage runner.
        controller_waiting.set()
        await wait_for_tick()

    monkeypatch.setattr(controller, '_wait_for_control_tick', observe_controller_wait)

    def actions(rows):
        creates = [row for row in rows if isinstance(row, CreateExecutorAction)]
        if creates:
            # End at the native proposal boundary; do not fake an exchange ACK.
            state.creates.extend(creates)
            state.created.set()
            controller.stop()
        else:
            execute_actions(rows)

    state.orchestrator.execute_actions = actions
    strategy.update_executors_info()
    tasks = [asyncio.create_task(controller.control_loop()), asyncio.create_task(executor.control_loop()),
             asyncio.create_task(strategy.listen_to_executor_actions()),
             asyncio.create_task(strategy._watch_spot_account_progress(state.connector, state.account_event))]
    state.recorder = recorder
    try:
        await asyncio.wait_for(controller_waiting.wait(), timeout=5)
        assert controller.status == RunnableStatus.RUNNING and not state.creates
        yield state
    finally:
        strategy._is_stop_triggered = True
        if strategy._managed_report_handle is not None:
            strategy._managed_report_handle.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def complete(state):
    assert fill(state.order, 'full', str(state.order.amount))
    assert not fill(state.order, 'full', str(state.order.amount))
    state.order.current_state = OrderState.FILLED
    state.controller.market_data_provider.time.return_value = state.strategy.current_timestamp = 1003
    state.executor.process_order_completed_event(None, None, SimpleNamespace(
        order_id=state.order.client_order_id, base_asset_amount=state.order.amount))


def assert_replaced(state, side):
    assert len(state.creates) == 1 and state.creates[0].executor_config.side == side
    holds = state.orchestrator.positions_held[state.controller.config.id]
    assert len(holds) == 1
    assert holds[0].net_amount_base == state.order.amount * (1 if side == TradeType.BUY else -1)
    assert holds[0].cum_fees_quote == Decimal('.000001')
    state.recorder.checkpoint_held_positions.assert_called_once()
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('account_first', [False, True])
async def test_full_loop_replaces_filled_quote_after_both_cash_assets_cover_fill(monkeypatch, side, account_first):
    async with replay(monkeypatch, side) as state:
        if account_first:
            account_observed(state, side, initial=(2000, 0))
        complete(state)
        if not account_first:
            await asyncio.sleep(.08)  # The full-fill publication cannot replace missing cash evidence.
            assert state.executor.is_active and not state.creates
            account_observed(state, side, initial=(2000, 0))
            state.connector.account_balance_observations['USDT']['observed_at'] = 1000
            state.account_event.set()
            await asyncio.sleep(.08)
            assert state.executor.is_active and not state.creates
            state.connector.account_balance_observations['USDT']['observed_at'] = 1002
            state.account_event.set()
        await asyncio.wait_for(state.created.wait(), .5)
        assert_replaced(state, side)


@pytest.mark.asyncio
async def test_outflow_terminal_proof_wakes_spot_replacement_before_the_next_report_tick(monkeypatch):
    from test.hummingbot.cli.test_service_availability import host

    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
    from hummingbot.core.spot_outflow import SpotOutflowProjection
    from hummingbot.core.spot_outflow_guard import SpotOutflowGuard

    async with replay(monkeypatch, TradeType.BUY) as state:
        service = host()
        guard = SpotOutflowGuard(SimpleNamespace(), dict(trading_pair='LIFE-USDT', window_seconds=60,
                                                         max_net_outflow_quote='10000', fee_allowance_rate='.01'),
                                 on_terminal_pending=service.poll_outflow)
        service.owner = SimpleNamespace(spot_outflow=guard, allow_new_orders=True)
        service.running = True
        service.next_outflow_poll = time.monotonic() + 60
        monkeypatch.setattr(service, '_mm_controllers', lambda: [state.controller])
        guard.reason_code = None
        guard.identity = lambda: {'account_id': 'offline'}
        guard.binding = guard.identity()
        guard.projection = SpotOutflowProjection(guard.config, 'LIFE-USDT', coverage_start_ms=0)
        guard.projection.advance(1003000)
        state.connector.name = 'okx'
        state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
        prior = InFlightOrder('prior-buy', 'LIFE-USDT', state.order.order_type, TradeType.BUY,
                              Decimal('1'), 999, Decimal('.1'), exchange_order_id='venue-buy',
                              initial_state=OrderState.CANCELED)
        assert guard.projection.reserve(prior)
        guard.orders[prior.client_order_id] = (state.connector, prior)
        waiting_for_proof = asyncio.Event()
        finish_proof = asyncio.Event()

        def preview(*args, **kwargs):
            allowed = guard.preview(*args, **kwargs)
            if not allowed and guard.terminal_pending:
                waiting_for_proof.set()
            return allowed

        async def confirm(identity):
            await finish_proof.wait()
            guard.projection.confirm_terminal(identity, Decimal(0))
            return True

        monkeypatch.setattr('hummingbot.core.order_submission_guard.spot_buy_preview', preview)
        monkeypatch.setattr(guard, '_confirm', confirm)
        account_observed(state, TradeType.BUY, initial=(2000, 0))
        complete(state)
        await asyncio.wait_for(waiting_for_proof.wait(), 5)
        assert not state.creates and guard.terminal_pending
        state.recorder.checkpoint_held_positions.assert_called_once()
        assert service.outflow_task is not None
        finish_proof.set()
        await service.outflow_task
        assert not guard.terminal_pending
        await asyncio.wait_for(state.created.wait(), 1)
        assert_replaced(state, TradeType.BUY)


@pytest.mark.asyncio
async def test_failed_hold_commit_keeps_level_until_native_report_retry_succeeds(monkeypatch):
    async with replay(monkeypatch, TradeType.BUY) as state:
        state.recorder.checkpoint_held_positions.side_effect = OSError('offline commit unavailable')
        account_observed(state, TradeType.BUY, initial=(2000, 0))
        complete(state)
        await eventually(lambda: state.strategy.controller_reports_error is not None)
        assert state.executor.is_closed and not state.creates
        assert not state.orchestrator.positions_held[state.controller.config.id]
        assert state.executor.config.id not in state.orchestrator.executors_ids_position_held
        await asyncio.sleep(.08)
        assert not state.creates  # No busy retry or uncommitted capacity release.
        state.recorder.checkpoint_held_positions.reset_mock(side_effect=True)
        state.strategy.update_executors_info()  # The unchanged regular report tick retries persistence.
        await asyncio.wait_for(state.created.wait(), .5)
        assert state.strategy.controller_reports_error is None
        assert_replaced(state, TradeType.BUY)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('partial', ['0', '200'])
async def test_full_loop_reserves_canceling_quote_until_ack_including_late_fill(monkeypatch, side, partial):
    async with replay(monkeypatch, side) as state:
        canceled = asyncio.Event()
        state.strategy.cancel.side_effect = lambda **kwargs: canceled.set()
        await state.controller.actions_queue.put([StopExecutorAction(
            controller_id=state.controller.config.id, executor_id=state.executor.config.id, keep_position=True)])
        await asyncio.wait_for(canceled.wait(), .5)
        await asyncio.sleep(.08)
        assert state.executor.status == RunnableStatus.SHUTTING_DOWN
        assert not state.creates and not state.orchestrator.positions_held[state.controller.config.id]
        state.recorder.checkpoint_held_positions.assert_not_called()
        amount = Decimal(partial)
        if amount:
            assert fill(state.order, 'late-fill', partial)
            assert not fill(state.order, 'late-fill', partial)
            state.order.current_state = OrderState.PARTIALLY_FILLED
            observe_account(state.connector, 'okx', (Decimal('2000') + amount * (
                1 if side == TradeType.BUY else -1), 0))
        state.order.current_state = OrderState.CANCELED
        state.executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=state.order.client_order_id))
        await asyncio.wait_for(state.created.wait(), .5)
        assert len(state.creates) == 1 and state.creates[0].executor_config.side == side
        holds = state.orchestrator.positions_held[state.controller.config.id]
        if amount:
            assert len(holds) == 1
            assert holds[0].net_amount_base == amount * (1 if side == TradeType.BUY else -1)
            assert holds[0].cum_fees_quote == Decimal('.000001')
            state.recorder.checkpoint_held_positions.assert_called_once()
        else:
            assert not holds
            state.recorder.checkpoint_held_positions.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_late_account_after_session_revocation_cannot_reopen_quote(monkeypatch, side):
    async with replay(monkeypatch, side) as state:
        waiting_for_account = asyncio.Event()
        held_report_consumed = asyncio.Event()
        covers_fill = state.controller._entry_account_covers_fill
        control_task = state.controller.control_task
        executor_id = state.executor.config.id

        def observe_coverage(executor, fill_at):
            covered = covers_fill(executor, fill_at)
            if executor.config.id == executor_id and not covered:
                waiting_for_account.set()
            return covered

        async def observe_control():
            await control_task()
            if executor_id in state.orchestrator.executors_ids_position_held:
                held_report_consumed.set()

        monkeypatch.setattr(state.controller, '_entry_account_covers_fill', observe_coverage)
        monkeypatch.setattr(state.controller, 'control_task', observe_control)
        complete(state)
        await asyncio.wait_for(waiting_for_account.wait(), 5)
        assert state.executor.is_active and not state.creates

        state.controller.revoke_session()
        account_observed(state, side, initial=(2000, 0))
        state.account_event.set()
        # A revoked controller cannot use account progress to resume trading.
        await eventually(lambda: not state.account_event.is_set(), timeout=5)
        assert not state.creates

        # Even an ordinary report cannot bypass session admission.
        state.strategy.update_executors_info()
        await asyncio.wait_for(held_report_consumed.wait(), 5)
        assert state.executor.is_closed
        state.recorder.checkpoint_held_positions.assert_called_once()
        assert state.controller.actions_queue.empty()
        assert not state.creates and not state.controller.session_admitted


@pytest.mark.asyncio
async def test_slow_synchronous_checkpoint_reproduces_watchdog_stall_then_native_progress(monkeypatch):
    records = []
    loop = asyncio.get_running_loop()
    checkpoint_entered = Event()
    stall_sampled = Event()
    recovered = asyncio.Event()

    def emit(record):
        records.append(record)
        if record['event'] == 'engine_loop_stall':
            stall_sampled.set()
        elif record['event'] == 'engine_loop_recovered':
            loop.call_soon_threadsafe(recovered.set)

    tracer = EngineDiagnostics(emit=emit)
    watch_once = tracer.watch_once

    def sample_after_checkpoint_enters():
        if checkpoint_entered.is_set():
            watch_once()

    # Only sample the injected fault, so setup scheduling cannot consume the
    # watchdog's first stall report before the checkpoint starts.
    monkeypatch.setattr(tracer, 'watch_once', sample_after_checkpoint_enters)
    async with replay(monkeypatch, TradeType.BUY) as state:
        assert await tracer.start()
        try:
            first_heartbeat = tracer._beat_ns
            await eventually(lambda: tracer._beat_ns != first_heartbeat, timeout=5)

            def slow_checkpoint(*args, **kwargs):
                assert not state.creates
                with tracer.span('position.checkpoint'):
                    # Keep the synchronous persistence fault active until the
                    # real watchdog has sampled it, even on a loaded runner.
                    checkpoint_entered.set()
                    assert stall_sampled.wait(5), 'Watchdog did not sample the blocked checkpoint'
                assert not state.creates

            state.recorder.checkpoint_held_positions.side_effect = slow_checkpoint
            account_observed(state, TradeType.BUY, initial=(2000, 0))
            complete(state)
            await asyncio.wait_for(state.created.wait(), 6)
            assert_replaced(state, TradeType.BUY)
            await asyncio.wait_for(recovered.wait(), 5)
            stalls = [row for row in records if row['event'] == 'engine_loop_stall']
            assert stalls and stalls[0]['heartbeat_gap_ms'] >= 250
            assert any(row['operation'] == 'strategy.reports' for row in stalls[0]['active'])
            assert any(row['operation'] == 'position.checkpoint' for row in stalls[0]['active'])
            assert state.executor.is_closed and state.strategy.controller_reports_error is None
        finally:
            await tracer.close()
        assert not tracer.worker.is_alive() and tracer.heartbeat_task.done()
