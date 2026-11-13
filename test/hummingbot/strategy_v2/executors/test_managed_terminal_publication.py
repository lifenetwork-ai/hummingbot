"""Native terminal financial application cannot wait for an unrelated clock tick."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_order_checkpoint import closing_quote
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill, setup_checkpoint
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.test_support.mock_paper_exchange import MockPaperExchange
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.order_submission_guard import current_executor_scope, current_session_scope, session_order_scope
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.utils.perpetual_reduction import HedgeCostBasis


@pytest_asyncio.fixture
async def native_publication(monkeypatch):
    made = []

    def create(side=TradeType.BUY, *, entry=False):
        if entry:
            orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
            executor.config = executor.config.model_copy(update={
                'mm_session_id': 'entry-session', 'mm_generation': 1, 'side': side})
            order.trade_type = side
        else:
            orchestrator, executor, order, recorder = closing_quote(monkeypatch, side)
        with patch('hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class', return_value=MagicMock()):
            strategy = StrategyV2Base({'okx_perpetual': MockPaperExchange()})
        strategy.market_data_provider = executor._strategy.market_data_provider
        strategy.connectors = executor._strategy.connectors
        strategy.config = executor._strategy.config
        strategy.executor_orchestrator = orchestrator
        orchestrator.strategy = strategy
        executor._strategy = strategy
        executor.get_in_flight_order = lambda *args: order
        controller = SimpleNamespace(config=executor.config, executors_update_event=asyncio.Event(),
                                     stop=MagicMock(), wait_closed=AsyncMock())
        strategy.controllers = {'maker': controller}
        recorder.durable_position_checkpoints = True
        recorder.applied_position_executors.return_value = set()
        if not entry:
            held = orchestrator.positions_held['maker'][0]
            held._reduction_basis = HedgeCostBasis(held.side, Decimal('1'), Decimal('.1'), Decimal('.001'))
        monkeypatch.setattr(MarketsRecorder, '_shared_instance', recorder)
        made.append(strategy)
        return strategy, orchestrator, executor, order, recorder, controller

    yield create
    for strategy in made:
        handle = getattr(strategy, '_managed_report_handle', None)
        if handle is not None:
            handle.cancel()
        strategy.listen_to_executor_actions_task.cancel()
        await asyncio.gather(strategy.listen_to_executor_actions_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_native_cancel_applies_partial_then_publishes_without_clock_tick(native_publication, side):
    strategy, orchestrator, executor, order, recorder, controller = native_publication(side)
    fill(order, 'partial', '.2')
    order.current_state = OrderState.CANCELED

    def commit(*args, **kwargs):
        assert not controller.executors_update_event.is_set()
        assert executor.config.id in orchestrator.executors_ids_position_held

    recorder.checkpoint_held_positions.side_effect = commit
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    await executor.control_shutdown_process()
    assert not controller.executors_update_event.is_set()  # Never publish inside a native callback.
    await asyncio.wait_for(controller.executors_update_event.wait(), .25)
    recorder.checkpoint_held_positions.assert_called_once()
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == Decimal('.8')
    assert orchestrator.positions_held['maker'][0].reduction_basis.amount == Decimal('.8')
    assert controller.executors_info[0].is_done
    assert not strategy.controller_reports_error


@pytest.mark.asyncio
async def test_failed_commit_does_not_publish_or_release_and_periodic_retry_remains(native_publication):
    strategy, orchestrator, executor, order, recorder, controller = native_publication()
    fill(order, 'partial', '.2')
    order.current_state = OrderState.CANCELED
    recorder.checkpoint_held_positions.side_effect = RuntimeError('commit unavailable')
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    await executor.control_shutdown_process()
    await asyncio.sleep(.07)
    assert strategy.controller_reports_error == 'RuntimeError'
    assert not controller.executors_update_event.is_set()
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == 1
    assert executor.config.id not in orchestrator.executors_ids_position_held
    recorder.checkpoint_held_positions.side_effect = None
    strategy.update_executors_info()  # The unchanged periodic report is still a retry path.
    assert controller.executors_update_event.is_set()
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == Decimal('.8')
    assert recorder.checkpoint_held_positions.call_count == 2


@pytest.mark.asyncio
async def test_burst_coalesces_and_foreign_generation_does_not_publish(native_publication):
    strategy, _, executor, _, _, controller = native_publication()
    strategy.update_executors_info = MagicMock()
    executor.stop()
    for _ in range(100):
        strategy.notify_executor_terminated(executor)
        await asyncio.sleep(0)  # Adjacent loop turns still belong to one notification window.
    await asyncio.sleep(.07)
    strategy.update_executors_info.assert_called_once()
    controller.config = controller.config.model_copy(update={'mm_generation': 2})
    strategy.notify_executor_terminated(executor)
    await asyncio.sleep(.07)
    strategy.update_executors_info.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_discards_a_queued_publication(native_publication):
    strategy, orchestrator, executor, _, _, _ = native_publication()
    strategy.update_executors_info = MagicMock()
    strategy.market_data_provider.wait_closed = AsyncMock()
    orchestrator.stop = AsyncMock()
    strategy.connectors = {}
    executor.stop()
    await strategy.on_stop()
    await asyncio.sleep(0)
    strategy.update_executors_info.assert_not_called()
    assert strategy._managed_report_handle is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['live', 'unmanaged', 'removed', 'superseded'])
async def test_only_resident_terminal_current_managed_scope_can_wake(native_publication, change):
    strategy, orchestrator, executor, _, _, controller = native_publication()
    strategy.update_executors_info = MagicMock()
    if change == 'unmanaged':
        executor.config = executor.config.model_copy(update={'mm_session_id': None, 'mm_generation': None})
    if change == 'removed':
        orchestrator.active_executors['maker'] = []
    if change != 'live':
        executor.stop()
    if change == 'superseded':
        controller.config = controller.config.model_copy(update={'mm_generation': 2})
    strategy.notify_executor_terminated(executor)
    await asyncio.sleep(.07)
    strategy.update_executors_info.assert_not_called()


@pytest.mark.asyncio
async def test_regular_report_consumes_pending_terminal_notification(native_publication):
    strategy, orchestrator, executor, order, recorder, controller = native_publication()
    original = orchestrator.get_all_reports
    orchestrator.get_all_reports = MagicMock(wraps=original)
    fill(order, 'partial', '.2')
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    await executor.control_shutdown_process()
    strategy.update_executors_info()
    await asyncio.sleep(.07)
    orchestrator.get_all_reports.assert_called_once()
    recorder.checkpoint_held_positions.assert_called_once()
    assert controller.executors_update_event.is_set()


@pytest.mark.asyncio
async def test_deferred_publication_cannot_inherit_a_retired_order_authority(native_publication):
    strategy, _, executor, _, _, _ = native_publication()
    seen = []
    strategy.update_executors_info = lambda: seen.append((current_session_scope(), current_executor_scope()))
    with session_order_scope('retiring-session', 1, executor=executor):
        executor.stop()
    await asyncio.sleep(.07)
    assert seen == [(None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_completed_entry_publishes_fill_without_clock_or_releasing_its_level(native_publication, side):
    strategy, orchestrator, executor, order, recorder, controller = native_publication(side, entry=True)
    fill(order, 'full', '1')
    order.current_state = OrderState.FILLED
    executor.process_order_completed_event(None, None, SimpleNamespace(
        order_id=order.client_order_id, base_asset_amount=order.amount))
    assert strategy._managed_report_handle is not None
    assert not controller.executors_update_event.is_set()
    await asyncio.wait_for(controller.executors_update_event.wait(), .25)
    assert controller.executors_info[0].custom_info['entry_order_filled'] is True
    # Reporting is not a handoff: fresh-account and financial gates still own it.
    assert executor.is_active and not executor.is_closed
    assert not orchestrator.positions_held['maker']
    recorder.checkpoint_held_positions.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['partial', 'flag_without_fills', 'foreign', 'unmanaged', 'removed', 'superseded', 'stopping'])
async def test_entry_notification_cannot_publish_unproven_or_retired_scope(native_publication, case):
    strategy, orchestrator, executor, order, _, controller = native_publication(entry=True)
    if case != 'flag_without_fills':
        fill(order, 'fill', '.2' if case == 'partial' else '1')
    order.current_state = OrderState.FILLED
    if case == 'unmanaged':
        executor.config = executor.config.model_copy(update={'mm_session_id': None})
    elif case == 'removed':
        orchestrator.active_executors['maker'] = []
    elif case == 'superseded':
        controller.config = controller.config.model_copy(update={'mm_generation': 2})
    elif case == 'stopping':
        strategy._is_stop_triggered = True
    executor.process_order_completed_event(None, None, SimpleNamespace(
        order_id='foreign' if case == 'foreign' else order.client_order_id, base_asset_amount=order.amount))
    assert strategy._managed_report_handle is None
    assert not controller.executors_update_event.is_set()
    assert executor.is_active


@pytest.mark.asyncio
async def test_entry_notifications_coalesce_and_do_not_inherit_order_authority(native_publication):
    strategy, _, executor, order, _, _ = native_publication(entry=True)
    fill(order, 'full', '1')
    order.current_state = OrderState.FILLED
    seen = []
    strategy.update_executors_info = lambda: seen.append((current_session_scope(), current_executor_scope()))
    with session_order_scope('entry-session', 1, executor=executor):
        for _ in range(20):
            executor.process_order_completed_event(None, None, SimpleNamespace(
                order_id=order.client_order_id, base_asset_amount=order.amount))
    assert strategy._managed_report_handle is not None
    await asyncio.sleep(.07)
    assert seen == [(None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize('intervention', ['periodic', 'stopping', 'superseded'])
async def test_queued_entry_publication_respects_periodic_report_and_scope_change(native_publication, intervention):
    strategy, orchestrator, executor, order, _, controller = native_publication(entry=True)
    original = orchestrator.get_all_reports
    orchestrator.get_all_reports = MagicMock(wraps=original)
    fill(order, 'full', '1')
    order.current_state = OrderState.FILLED
    executor.process_order_completed_event(None, None, SimpleNamespace(
        order_id=order.client_order_id, base_asset_amount=order.amount))
    assert strategy._managed_report_handle is not None
    if intervention == 'periodic':
        strategy.update_executors_info()
    elif intervention == 'stopping':
        strategy._is_stop_triggered = True
    else:
        controller.config = controller.config.model_copy(update={'mm_generation': 2})
    await asyncio.sleep(.07)
    assert orchestrator.get_all_reports.call_count == (1 if intervention == 'periodic' else 0)
    assert controller.executors_update_event.is_set() == (intervention == 'periodic')
    assert executor.is_active and not orchestrator.positions_held['maker']


@pytest.mark.asyncio
async def test_entry_report_failure_reserves_level_and_recovers_on_regular_clock(native_publication):
    strategy, orchestrator, executor, order, _, controller = native_publication(entry=True)
    original = orchestrator.get_all_reports
    orchestrator.get_all_reports = MagicMock(side_effect=RuntimeError('report unavailable'))
    fill(order, 'full', '1')
    order.current_state = OrderState.FILLED
    executor.process_order_completed_event(None, None, SimpleNamespace(
        order_id=order.client_order_id, base_asset_amount=order.amount))
    await asyncio.sleep(.07)
    orchestrator.get_all_reports.assert_called_once()
    assert strategy.controller_reports_error == 'RuntimeError'
    assert not controller.executors_update_event.is_set()
    assert executor.is_active and not orchestrator.positions_held['maker']
    orchestrator.get_all_reports = original
    strategy.update_executors_info()
    assert strategy.controller_reports_error is None
    assert controller.executors_update_event.is_set() and executor.is_active
