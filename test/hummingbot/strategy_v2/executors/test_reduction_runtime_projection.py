"""Terminal absence proof releases a level independently of stale admission caches."""
from copy import copy
from test.hummingbot.strategy_v2.executors.test_terminal_order_checkpoint import closing_quote
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType


@pytest.mark.parametrize('proved', [False, True])
def test_proved_empty_terminal_order_does_not_wait_for_an_unrelated_next_admission(monkeypatch, proved):
    orchestrator, executor, order, recorder = closing_quote(monkeypatch)
    recorder.durable_position_checkpoints = True
    recorder.sql_manager.persistence_healthy = True
    orchestrator.strategy.controllers = {'maker': SimpleNamespace(config=SimpleNamespace(
        connector_name='okx_perpetual', trading_pair='LIFE-USDT',
        inventory_reduction=SimpleNamespace(mode='close_first')))}
    order.current_state = OrderState.CANCELED
    order._managed_terminal_proved = proved
    executor._status, executor.close_type = RunnableStatus.TERMINATED, CloseType.EARLY_STOP
    monkeypatch.setattr('hummingbot.core.order_submission_guard.active_guard', lambda: SimpleNamespace(
        active_intents={order.client_order_id}, sent_intents={order.client_order_id}))
    state = orchestrator.reduction_runtime('maker')
    assert state.executors[0].applied is proved


def test_the_native_default_closed_buffer_does_not_consume_live_reduction_capacity(monkeypatch):
    orchestrator, executor, _, recorder = closing_quote(monkeypatch)
    recorder.durable_position_checkpoints = recorder.sql_manager.persistence_healthy = True
    orchestrator.strategy.controllers = {'maker': SimpleNamespace(config=SimpleNamespace(
        connector_name='okx_perpetual', trading_pair='LIFE-USDT',
        inventory_reduction=SimpleNamespace(mode='close_first')))}
    monkeypatch.setattr('hummingbot.core.order_submission_guard.active_guard', lambda: None)
    resident = []
    for index in range(101):
        item = copy(executor)
        item.config = executor.config.model_copy(update={'id': 'resident-' + str(index)})
        item._order = None
        item._status = RunnableStatus.TERMINATED if index < 100 else RunnableStatus.RUNNING
        item.close_type = CloseType.EARLY_STOP if index < 100 else None
        resident.append(item)
    orchestrator.active_executors['maker'] = resident
    state = orchestrator.reduction_runtime('maker')
    assert len(state.executors) == 101
    assert sum(not row.applied for row in state.executors) == 1


@pytest.mark.parametrize('close_type', [CloseType.EARLY_STOP, CloseType.POSITION_HOLD])
def test_delayed_stop_action_cannot_resurrect_a_terminated_executor(monkeypatch, close_type):
    orchestrator, executor, _, _ = closing_quote(monkeypatch)
    executor.close_type = close_type
    executor.stop()
    orchestrator.stop_executor(StopExecutorAction(controller_id='maker', executor_id=executor.config.id, keep_position=True))
    assert executor.terminated.is_set()
    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == close_type


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [OrderState.CANCELED, OrderState.FILLED])
@pytest.mark.parametrize('late', [OrderState.PENDING_CANCEL, OrderState.OPEN])
async def test_late_ack_or_old_rest_read_cannot_undo_positive_terminal_proof(monkeypatch, terminal, late):
    _, _, order, _ = closing_quote(monkeypatch)
    if terminal == OrderState.FILLED:
        fill(order, 'filled', '1')
    order._managed_terminal_proved = True
    order.current_state = terminal
    tracker = ClientOrderTracker(MagicMock())
    tracker.start_tracking_order(order)
    await tracker._process_order_update(OrderUpdate(
        client_order_id=order.client_order_id, trading_pair=order.trading_pair,
        exchange_order_id=order.exchange_order_id, update_timestamp=10, new_state=late))
    assert order.current_state == terminal
    assert order.is_done
