"""Only exact native executions backed by committed financial receipts settle BUY risk."""
import asyncio
import logging
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowProjection
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
from hummingbot.core.spot_terminal_proof import committed_full_fill


def completed():
    order = InFlightOrder(client_order_id='buy', exchange_order_id='42', trading_pair='BTC-USDT',
                          order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY,
                          amount=Decimal('.00071681'), price=Decimal('69266'), creation_timestamp=1)
    projection = SpotOutflowProjection(SpotOutflowConfig(window_seconds=60, max_net_outflow_quote='100',
                                                         fee_allowance_rate='.01'), 'BTC-USDT', coverage_start_ms=0)
    projection.advance(100000)
    assert projection.reserve(order)
    fee = AddedToCostTradeFee(percent_token='BTC', flat_fees=[TokenAmount('BTC', Decimal('.000000573448'))])
    trade = TradeUpdate(trade_id='1', client_order_id='buy', exchange_order_id='42', trading_pair='BTC-USDT',
                        fill_timestamp=99, fill_price=order.price, fill_base_amount=order.amount,
                        fill_quote_amount=order.amount * order.price, fee=fee)
    assert order.update_with_trade_update(trade)
    order.current_state = OrderState.FILLED
    projection.add_fill(dict(market='okx', symbol=order.trading_pair, order_id='buy', exchange_trade_id='1', timestamp=99000,
                             trade_type='BUY', price=trade.fill_price, amount=trade.fill_base_amount, trade_fee=fee.to_json()))
    return projection, SimpleNamespace(name='okx'), order


def test_positive_receipt_uses_original_fee_without_changing_cash_or_native_fills():
    projection, connector, order = completed()
    before = deepcopy(projection.status()), dict(order.order_fills)
    assert committed_full_fill(projection, connector, order)
    assert before == (projection.status(), order.order_fills)


@pytest.mark.parametrize('mutation', ['uncommitted', 'partial', 'overfilled', 'approximate', 'state', 'fee', 'exchange',
                                      'missing_native', 'missing_receipt', 'reservation', 'invalid', 'side', 'pair',
                                      'price', 'quote', 'trade_identity', 'future', 'expired'])
def test_uncertain_or_conflicting_execution_cannot_replace_exchange_terminal_proof(mutation):
    projection, connector, order = completed()
    native = order.order_fills['1']
    if mutation == 'uncommitted':
        projection.reservations['buy'].committed = Decimal(0)
    elif mutation == 'partial':
        projection.reservations['buy'].committed -= Decimal('.00000001')
    elif mutation == 'overfilled':
        order.executed_amount_base += Decimal('.00000001')
    elif mutation == 'approximate':
        order.executed_amount_base -= Decimal('.000000000000000001')
    elif mutation == 'state':
        order.current_state = OrderState.OPEN
    elif mutation == 'fee':
        order.order_fills['1'] = native._replace(fee=AddedToCostTradeFee())
    elif mutation == 'exchange':
        order.order_fills['1'] = native._replace(exchange_order_id='other')
    elif mutation == 'missing_native':
        order.order_fills.clear()
    elif mutation == 'missing_receipt':
        projection.entries.clear()
    elif mutation == 'reservation':
        projection.reservations.clear()
    elif mutation == 'invalid':
        projection.failure = 'spot_outflow_fill_conflict'
    elif mutation == 'side':
        order.trade_type = TradeType.SELL
    elif mutation == 'pair':
        order.trading_pair = 'ETH-USDT'
    elif mutation == 'price':
        order.order_fills['1'] = native._replace(fill_price=Decimal(1))
    elif mutation == 'quote':
        order.executed_amount_quote += Decimal(1)
    elif mutation == 'trade_identity':
        order.order_fills['1'] = native._replace(trade_id='2')
    elif mutation == 'future':
        projection.clock_failure = True
    elif mutation == 'expired':
        projection.advance(200000)
    assert not committed_full_fill(projection, connector, order)


@pytest.mark.parametrize('stage', ['order_read', 'order_identity', 'fills_read', 'fill_coverage', 'order_recheck', 'recording'])
def test_terminal_failure_reports_fixed_stage_without_exception_payload(stage, caplog):
    guard = SpotOutflowGuard(SimpleNamespace(), dict(trading_pair='BTC-USDT', window_seconds=60,
                                                     max_net_outflow_quote='100', fee_allowance_rate='.01'))
    guard.orders['buy'] = (None, SimpleNamespace(is_done=True))

    async def unavailable(identity):
        guard.terminal_stages[identity] = stage
        raise ValueError('SECRET-CANARY-provided-by-remote')

    guard._confirm = unavailable
    with caplog.at_level(logging.WARNING):
        asyncio.run(guard.reconcile_terminal())
        asyncio.run(guard.reconcile_terminal())
    assert guard.terminal_reason_code() == 'spot_outflow_terminal_' + stage
    assert len(caplog.records) == 1 and 'SECRET-CANARY' not in caplog.text
    assert set(guard.orders) == set(guard.terminal_pending) == {'buy'}
    guard._confirm = AsyncMock(return_value=True)
    asyncio.run(guard.reconcile_terminal())
    assert not guard.terminal_stages and not guard.terminal_failures
    assert not guard.orders and not guard.terminal_pending


def test_a_released_order_cannot_leave_diagnostics_after_an_inflight_reader_fails():
    guard = SpotOutflowGuard(SimpleNamespace(), dict(trading_pair='BTC-USDT', window_seconds=60,
                                                     max_net_outflow_quote='100', fee_allowance_rate='.01'))
    guard.orders['buy'] = (None, SimpleNamespace(is_done=True))

    async def released(identity):
        guard.release_unsent(identity)
        guard.terminal_stages[identity] = 'order_read'
        raise OSError('No longer relevant')

    guard._confirm = released
    asyncio.run(guard.reconcile_terminal())
    assert not guard.terminal_stages and not guard.terminal_failures
    assert not guard.orders and not guard.terminal_pending
