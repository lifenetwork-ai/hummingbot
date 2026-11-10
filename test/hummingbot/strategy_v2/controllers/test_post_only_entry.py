"""Configured MM prices keep native maker protection before each new executor."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_entry_quote_preparation import queued
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import fill, setup_lifecycle

import pytest

from hummingbot.core.data_type.common import PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import OrderCancelledEvent
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_queued_configured_entry_retains_upstream_maker_price_bound(monkeypatch, name, side):
    state = await queued(monkeypatch, name, side)
    executor, controller = state.executor, state.controller
    original = executor.config.model_copy(deep=True)
    state.prices['BTC-USDT'] = Decimal('102') if side == TradeType.BUY else Decimal('98')

    def price(connector, pair, kind):
        if pair == 'LIFE-USDT':
            return {PriceType.BestBid: Decimal('.0999'), PriceType.BestAsk: Decimal('.1001')}.get(kind, state.prices[pair])
        return state.prices[pair]

    controller.market_data_provider.get_price_by_type.side_effect = price
    executor.place_open_order()
    call = (state.strategy.buy if side == TradeType.BUY else state.strategy.sell).call_args
    assert call is not None
    quote, amount = call.args[4], call.args[2]
    assert quote <= Decimal('.0999') if side == TradeType.BUY else quote >= Decimal('.1001')
    assert amount <= original.amount and quote * amount <= original.entry_price * original.amount
    assert controller.spread_quote_valid(side, quote, amount, level_id=original.level_id)
    assert executor.config.id == original.id


@pytest.mark.asyncio
@pytest.mark.parametrize('before_created', [False, True])
@pytest.mark.parametrize('name', ['okx', 'okx_perpetual'])
async def test_confirmed_zero_cancel_retires_managed_entry_instead_of_retrying_same_price(monkeypatch, name, before_created):
    state = await setup_lifecycle(monkeypatch, name, TradeType.SELL, runtime_config_managed=True,
                                  mm_session_id='session', mm_generation=1)
    executor, order = state.executor, state.order
    executor.config.use_configured_entry_price = True
    executor.config.controller_id = state.controller.config.id
    order.current_state = OrderState.CANCELED
    state.connector._order_tracker.fetch_order.return_value = order
    if before_created:
        executor._open_order.order = None
    previous = executor.config.model_copy(deep=True)
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor.close_type == CloseType.POSITION_HOLD
    assert executor._failed_orders[0].order is order
    assert executor.config == previous
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED
    state.strategy.sell.assert_not_called()
    state.strategy.buy.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_does_not_override_requested_stop_or_drop_late_fill_before_shutdown(monkeypatch):
    state = await setup_lifecycle(monkeypatch, 'okx_perpetual', TradeType.SELL, runtime_config_managed=True,
                                  mm_session_id='session', mm_generation=1)
    executor, order = state.executor, state.order
    executor.config.use_configured_entry_price = True
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert fill(order, 'late-but-prior-execution', '100')
    await executor.control_task()
    assert executor.status == RunnableStatus.TERMINATED
    assert len(executor._held_position_orders) == 1
    assert Decimal(executor._held_position_orders[0]['executed_amount_base']) == Decimal('100')


@pytest.mark.asyncio
@pytest.mark.parametrize('unproven', ['partial_fill', 'not_terminal', 'no_exchange_id', 'unmanaged'])
async def test_other_cancel_paths_do_not_become_zero_fill_refresh(monkeypatch, unproven):
    state = await setup_lifecycle(monkeypatch, 'okx_perpetual', TradeType.SELL,
                                  runtime_config_managed=True, mm_session_id='session', mm_generation=1)
    executor, order = state.executor, state.order
    executor.config.use_configured_entry_price = True
    order.current_state = OrderState.CANCELED
    if unproven == 'partial_fill':
        assert fill(order, 'first', '100')
    elif unproven == 'not_terminal':
        order.current_state = OrderState.OPEN
    elif unproven == 'no_exchange_id':
        order.exchange_order_id = None
    else:
        executor.config.mm_session_id = None
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    assert executor.status == RunnableStatus.RUNNING
    assert len(executor._failed_orders) == 1


@pytest.mark.asyncio
async def test_cancel_preserves_explicit_stop_mode(monkeypatch):
    state = await setup_lifecycle(monkeypatch, 'okx_perpetual', TradeType.SELL,
                                  runtime_config_managed=True, mm_session_id='session', mm_generation=1)
    executor, order = state.executor, state.order
    executor.config.use_configured_entry_price = True
    executor.early_stop(keep_position=False)
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(0, state.connector, OrderCancelledEvent(1001, order.client_order_id))
    assert executor.status == RunnableStatus.SHUTTING_DOWN and executor.close_type == CloseType.EARLY_STOP


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['stale', 'crossed', 'missing', 'bounds'])
async def test_maker_bound_cannot_bypass_book_freshness_or_configured_bounds(monkeypatch, invalid):
    state = await queued(monkeypatch, 'okx', TradeType.SELL)
    provider = state.controller.market_data_provider
    previous_book = provider.get_order_book.side_effect
    if invalid == 'stale':
        from types import SimpleNamespace
        provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(snapshot_uid=900, last_diff_uid=900) if pair == 'LIFE-USDT' else previous_book(name, pair)
    else:
        def price(name, pair, kind):
            if pair != 'LIFE-USDT':
                return state.prices[pair]
            if kind == PriceType.BestBid:
                return Decimal('.2') if invalid == 'crossed' else Decimal('.1')
            if kind == PriceType.BestAsk:
                return Decimal('NaN') if invalid == 'missing' else Decimal('2') if invalid == 'bounds' else Decimal('.1')
            return state.prices[pair]
        provider.get_price_by_type.side_effect = price
    state.executor.place_open_order()
    state.strategy.sell.assert_not_called()
    assert state.executor._open_order is None and state.executor.status == RunnableStatus.SHUTTING_DOWN


@pytest.mark.asyncio
async def test_outward_maker_price_preserves_reduced_current_allocation_too(monkeypatch):
    state = await queued(monkeypatch, 'okx', TradeType.SELL)
    state.controller.config.total_amount_quote = Decimal('20')  # Ten per side, below queued budget.

    def price(name, pair, kind):
        if pair == 'LIFE-USDT':
            return {PriceType.BestBid: Decimal('.7'), PriceType.BestAsk: Decimal('.8')}.get(kind, Decimal('.75'))
        return state.prices[pair]

    state.controller.market_data_provider.get_price_by_type.side_effect = price
    state.executor.place_open_order()
    call = state.strategy.sell.call_args
    assert call is not None
    assert call.args[2] * call.args[4] <= Decimal('10')
