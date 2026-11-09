"""A rejected configured entry must reprice through the controller, not retry stale prices."""
from decimal import ROUND_DOWN, Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import (
    controller_actions,
    fill,
    setup_lifecycle,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import MarketOrderFailureEvent, OrderCancelledEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType, TrackedOrder
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('change', ['reference', 'fee'])
async def test_rejected_entry_retires_and_reprices_without_optional_defense(monkeypatch, caplog, connector_name, side, change):
    state = await setup_lifecycle(
        monkeypatch, connector_name, side, scenario='follow_token',
        references=[dict(pair='BTC-USDT', weight='1')], spread_policy=dict(volatility=dict(
            enabled=True, on_unavailable='static', fallback_buy_spreads=['.01'], fallback_sell_spreads=['.01'])))
    controller, executor, order = state.controller, state.executor, state.order
    order.exchange_order_id, order.current_state = None, OrderState.PENDING_CREATE
    state.connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(Decimal('.00000001'), rounding=ROUND_DOWN)
    state.connector.get_order_price_quantum.return_value = Decimal('.00000001')
    state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    policy = controller.config.spread_policy.model_copy(update={'fee_floor': True})
    controller.config = controller.config.model_copy(update={'spread_policy': policy})
    controller._spread_policy = SpreadPolicy(policy)
    controller.update_reference()
    assert controller._spread_policy.defense is None and executor.config.use_configured_entry_price
    host = ServiceHost.__new__(ServiceHost)
    host._mm_controllers = lambda: [controller]
    connector = SimpleNamespace(name=connector_name)
    host.mm_quote_valid(connector, order)
    if change == 'reference':
        state.prices['BTC-USDT'] = Decimal('99') if side == TradeType.BUY else Decimal('101')
    else:
        state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.02'))
    controller.update_reference()
    assert controller.processed_data['ready']
    with pytest.raises(SubmissionBlocked, match='spread_policy_quote_rejected') as blocked:
        host.mm_quote_valid(connector, order)
    assert type(blocked.value).__name__ == 'QuoteRefreshRequired'
    # Model the connector's terminal local failure event: no transport or fill.
    executor.process_order_failed_event(0, state.connector, MarketOrderFailureEvent(
        1001, order.client_order_id, OrderType.LIMIT_MAKER, str(blocked.value), type(blocked.value).__name__))
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == CloseType.POSITION_HOLD
    assert 'reference_price=' in str(blocked.value) and 'buy_spreads' in str(blocked.value)
    record = next(record for record in caplog.records if getattr(record, 'event', None) == 'order_submission_blocked')
    assert record.levelname == 'WARNING' and record.reason_code == 'spread_policy_quote_rejected'
    assert 'Retiring entry executor' in record.getMessage()
    # The old level stays reserved until shutdown; it cannot overlap a new entry.
    assert not any(isinstance(action, CreateExecutorAction) and action.executor_config.side == side
                   for action in await controller_actions(controller, executor))
    executor.place_open_order = MagicMock()
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED
    assert executor.close_type == CloseType.EARLY_STOP
    executor.place_open_order.assert_not_called()
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()
    actions = await controller_actions(controller, executor)
    replacement = next(action.executor_config for action in actions
                       if isinstance(action, CreateExecutorAction) and action.executor_config.side == side)
    assert replacement.entry_price != executor.config.entry_price
    assert controller.spread_quote_valid(side, replacement.entry_price, replacement.amount)


def test_missing_controller_is_still_a_safety_error():
    host = ServiceHost.__new__(ServiceHost)
    host._mm_controllers = lambda: []
    with pytest.raises(SubmissionBlocked) as blocked:
        host.mm_quote_valid(SimpleNamespace(name='okx'), SimpleNamespace(trading_pair='LIFE-USDT'))
    assert type(blocked.value) is SubmissionBlocked


def test_policy_evaluation_error_is_not_a_refresh_instruction():
    host = ServiceHost.__new__(ServiceHost)
    controller = MagicMock()
    controller.config.connector_name, controller.config.trading_pair = 'okx', 'LIFE-USDT'
    controller.config.spread_policy.volatility.defense = None
    controller.spread_quote_valid.side_effect = ValueError('private exception details')
    host._mm_controllers = lambda: [controller]
    with pytest.raises(SubmissionBlocked) as blocked:
        host.mm_quote_valid(SimpleNamespace(name='okx'), SimpleNamespace(
            trading_pair='LIFE-USDT', trade_type=TradeType.BUY, price=Decimal('.099'), amount=Decimal('1')))
    assert type(blocked.value) is SubmissionBlocked
    assert 'private exception details' not in str(blocked.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('already_stopping', [False, True])
async def test_refresh_keeps_prior_fills_and_pending_orders_and_does_not_override_stop(monkeypatch, already_stopping):
    state = await setup_lifecycle(monkeypatch, 'okx_perpetual', TradeType.BUY)
    executor, order = state.executor, state.order
    executor.config = executor.config.model_copy(update={'use_configured_entry_price': True})
    assert fill(order, 'prior-fill', '100')
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    executor._open_order = TrackedOrder('unsent-retry')
    # Retain a separate outstanding order: refresh cannot bypass its ACK.
    executor._take_profit_limit_order = TrackedOrder('pending-tp')
    executor._take_profit_limit_order.order = order.__class__(
        'pending-tp', order.trading_pair, OrderType.LIMIT_MAKER, TradeType.SELL, Decimal('100'), 1001,
        Decimal('.102'), exchange_order_id='exchange-tp', initial_state=OrderState.OPEN)
    if already_stopping:
        executor.early_stop(keep_position=False)
    executor.process_order_failed_event(0, state.connector, MarketOrderFailureEvent(
        1001, 'unsent-retry', OrderType.LIMIT_MAKER, 'spread_policy_quote_rejected', 'QuoteRefreshRequired'))
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == (CloseType.EARLY_STOP if already_stopping else CloseType.POSITION_HOLD)
    assert not any(isinstance(action, CreateExecutorAction) and action.executor_config.side == TradeType.BUY
                   for action in await controller_actions(state.controller, executor))
    executor._wait_for_shutdown_orders = AsyncMock()
    await executor.control_task()
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    state.strategy.cancel.assert_called_once_with(
        connector_name='okx_perpetual', trading_pair='LIFE-USDT', order_id='pending-tp')
    executor._take_profit_limit_order.order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1002, 'pending-tp'))
    if not already_stopping:
        await executor.control_task()
        assert executor.status == RunnableStatus.TERMINATED
        state.orchestrator._update_positions_from_done_executors()
        state.orchestrator._update_positions_from_done_executors()
        holds = state.orchestrator.positions_held[state.controller.config.id]
        assert len(holds) == 1 and holds[0].net_amount_base == Decimal('100')
        assert holds[0].cum_fees_quote == Decimal('.000001')
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['SubmissionBlocked', 'OwnershipLost', 'IOError', 'MarketReadinessBlocked'])
async def test_other_failures_do_not_become_refresh_instructions_from_message_text(monkeypatch, kind):
    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY)
    executor = state.executor
    executor.config = executor.config.model_copy(update={'use_configured_entry_price': True})
    executor.process_order_failed_event(0, state.connector, MarketOrderFailureEvent(
        1001, state.order.client_order_id, OrderType.LIMIT_MAKER, 'spread_policy_quote_rejected', kind))
    assert executor.status == RunnableStatus.RUNNING and executor._current_retries == 1
    assert executor._open_order is None and executor._failed_orders
