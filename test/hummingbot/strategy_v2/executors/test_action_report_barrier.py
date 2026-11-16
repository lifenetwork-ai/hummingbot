"""An action handoff must publish only after native financial reporting succeeds."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_managed_terminal_publication import (
    native_publication as native_publication_fixture,
)
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.operational_warnings import DrawdownValuationUnavailable
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import PerformanceReport

native_publication = native_publication_fixture


@pytest.mark.asyncio
async def test_action_listener_keeps_readiness_closed_when_native_hold_commit_fails(native_publication):
    strategy, orchestrator, executor, order, recorder, controller = native_publication(entry=True)
    orchestrator.cached_performance['maker'] = PerformanceReport()
    previous = [executor.executor_info]
    strategy.controller_reports = {'maker': dict(executors=previous, positions=[], performance=None)}
    controller.executors_info = previous
    fill(order, 'partial', '.2')
    executor.early_stop(keep_position=True)
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id=order.client_order_id))
    await executor.control_shutdown_process()
    assert executor.is_closed
    # The queued native action must perform the same financial handoff as the
    # regular/coalesced report. Keep the independent timer out of this trigger.
    if strategy._managed_report_handle is not None:
        strategy._managed_report_handle.cancel()
        strategy._managed_report_handle = None
    controller.executors_update_event.clear()
    committed = asyncio.Event()

    def fail_commit(*args, **kwargs):
        committed.set()
        raise OSError('offline checkpoint commit unavailable')

    recorder.checkpoint_held_positions.side_effect = fail_commit
    await strategy.actions_queue.put([StopExecutorAction(
        controller_id='maker', executor_id=executor.config.id, keep_position=True)])
    await asyncio.wait_for(committed.wait(), 1)
    assert strategy.controller_reports_error == 'OSError'
    assert not controller.executors_update_event.is_set(), 'A failed financial report reopened quote evaluation'
    assert controller.executors_info is previous
    assert not orchestrator.positions_held['maker']
    assert executor.config.id not in orchestrator.executors_ids_position_held

    recorder.checkpoint_held_positions.reset_mock(side_effect=True)
    strategy.update_executors_info()  # Preserve the normal retry; no busy loop.
    assert strategy.controller_reports_error is None and controller.executors_update_event.is_set()
    recorder.checkpoint_held_positions.assert_called_once()
    held = orchestrator.positions_held['maker']
    assert len(held) == 1 and held[0].net_amount_base == Decimal('.2')


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [OSError('offline report read unavailable'),
                                   DrawdownValuationUnavailable('okx_perpetual', 'LIFE-USDT', 'stale_mark', 10)])
async def test_failed_report_revokes_previously_published_readiness_until_successful_retry(native_publication, monkeypatch, error):
    strategy, orchestrator, _, _, _, controller = native_publication(entry=True)
    strategy.update_executors_info()
    assert controller.executors_update_event.is_set()
    previous = controller.executors_info
    original = orchestrator.get_all_reports

    def failed():
        raise error

    monkeypatch.setattr(orchestrator, 'get_all_reports', failed)
    strategy.update_executors_info()
    assert strategy.controller_reports_error == type(error).__name__
    assert not controller.executors_update_event.is_set(), 'A previous report remained admission-ready after failure'
    assert controller.executors_info is previous  # Retain UI/exposure data, without admitting new actions.
    monkeypatch.setattr(orchestrator, 'get_all_reports', original)
    strategy.update_executors_info()
    assert strategy.controller_reports_error is None and controller.executors_update_event.is_set()
