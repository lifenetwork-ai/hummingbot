"""A retired MM entry cannot turn an expected session fence into a retry burst."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import fill, setup_lifecycle
from unittest.mock import MagicMock

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import MarketOrderFailureEvent
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


@pytest.mark.asyncio
@pytest.mark.parametrize('connector', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('configured', [False, True])
async def test_closed_session_retires_managed_maker_without_reopening(monkeypatch, connector, side, configured):
    state = await setup_lifecycle(monkeypatch, connector, side, mm_session_id='retired', mm_generation=1)
    executor, order = state.executor, state.order
    executor.config = executor.config.model_copy(update={'use_configured_entry_price': configured})
    order.exchange_order_id, order.current_state = None, OrderState.FAILED
    event = MarketOrderFailureEvent(1000, order.client_order_id, OrderType.LIMIT_MAKER,
                                    'Session no longer accepts orders', 'SessionSubmissionBlocked')
    executor.process_order_failed_event(None, state.connector, event)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == CloseType.POSITION_HOLD and executor._current_retries == 0
    executor.place_open_order = MagicMock()
    for _ in range(12):
        executor.process_order_failed_event(None, state.connector, event)  # Duplicate callback cannot reopen it.
        await executor.control_task()
    executor.place_open_order.assert_not_called()
    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == CloseType.EARLY_STOP
    assert executor._failed_orders[0].order is order


@pytest.mark.asyncio
async def test_closed_session_keeps_cached_native_partial_before_created_callback(monkeypatch):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY, mm_session_id='retired', mm_generation=1)
    executor, order = state.executor, state.order
    assert fill(order, 'committed-partial', '.25')
    order.current_state = OrderState.FAILED
    executor._open_order.order = None
    executor.get_in_flight_order = lambda *args: order
    executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1001, order.client_order_id, OrderType.LIMIT_MAKER, 'Session closed', 'SessionSubmissionBlocked'))
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == CloseType.POSITION_HOLD
    assert executor._failed_orders[0].order is order
    saved = executor._held_position_orders
    assert len(saved) == 1
    assert saved[0]['executed_amount_base'] == '0.25'
    assert set(saved[0]['order_fills']) == {'committed-partial'}
    assert Decimal(saved[0]['order_fills']['committed-partial']['fee']['flat_fees'][0]['amount']) == Decimal('.000001')
    assert executor._current_retries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type', ['SubmissionBlocked', 'OwnershipLost', 'OSError'])
async def test_safety_or_transport_errors_are_not_reclassified_as_session_retirement(monkeypatch, error_type):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY, mm_session_id='retired', mm_generation=1)
    state.executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1001, state.order.client_order_id, OrderType.LIMIT_MAKER, 'Must retain error', error_type))
    assert state.executor.status == RunnableStatus.RUNNING
    assert state.executor._current_retries == 1


@pytest.mark.asyncio
async def test_legacy_maker_retains_its_original_failure_policy(monkeypatch):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY)
    state.executor.config = state.executor.config.model_copy(update={'mm_session_id': None, 'mm_generation': None})
    state.executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        1001, state.order.client_order_id, OrderType.LIMIT_MAKER, 'Session closed', 'SessionSubmissionBlocked'))
    assert state.executor.status == RunnableStatus.RUNNING
    assert state.executor._current_retries == 1
