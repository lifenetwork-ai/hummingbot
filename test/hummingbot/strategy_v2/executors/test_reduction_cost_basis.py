"""Remaining opening costs travel with native holds, never lifetime totals."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator, PositionHold
from hummingbot.strategy_v2.utils.perpetual_reduction import HedgeCostBasis


def executor(identity, side, effect, amount, quote, fee):
    return SimpleNamespace(id=identity, close_type=None, config=SimpleNamespace(side=side), custom_info={
        'held_position_orders': [dict(client_order_id=identity, trade_type=side.name, position=effect,
                                      executed_amount_base=amount, executed_amount_quote=quote,
                                      cumulative_fee_paid_quote=fee)]})


@pytest.mark.parametrize('leg', [TradeType.BUY, TradeType.SELL])
def test_hold_allocates_fees_once_across_partial_close_and_new_opening(leg):
    hold = PositionHold('okx_perpetual', 'BTC-USDT', leg)
    opposite = TradeType.BUY if leg == TradeType.SELL else TradeType.SELL
    opening = executor('open', leg, 'OPEN', '2', '200', '2')
    closing = executor('close', opposite, 'CLOSE', '.5', '60', '.6')
    hold.add_orders_from_executor(opening)
    hold.add_orders_from_executor(closing)
    hold.add_orders_from_executor(closing)
    assert hold.reduction_basis == HedgeCostBasis(leg, Decimal('1.5'), Decimal('150'), Decimal('1.5'))
    assert hold.cum_fees_quote == Decimal('2.6')
    hold.add_orders_from_executor(executor('add', leg, 'OPEN', '1', '120', '1.2'))
    assert hold.reduction_basis == HedgeCostBasis(leg, Decimal('2.5'), Decimal('270'), Decimal('2.7'))


@pytest.mark.parametrize('effect', [None, 'NIL', 'unknown'])
def test_unproven_effect_cannot_seed_cost_authority_from_lifetime_fees(effect):
    hold = PositionHold('okx_perpetual', 'BTC-USDT', TradeType.BUY)
    hold.add_orders_from_executor(executor('unknown', TradeType.BUY, effect, '1', '100', '1'))
    assert hold.net_amount_base == 1 and hold.cum_fees_quote == 1
    assert hold.reduction_basis is None
    hold.add_orders_from_executor(executor('new-open', TradeType.BUY, 'OPEN', '1', '100', '1'))
    assert hold.reduction_basis is None  # A later known fill cannot repair an unknown initial cost.


def test_restored_nonflat_hold_never_treats_lifetime_fees_as_remaining_fees():
    orchestrator = object.__new__(ExecutorOrchestrator)
    orchestrator.positions_held = {'maker': []}
    row = SimpleNamespace(connector_name='okx_perpetual', trading_pair='BTC-USDT', side='BUY',
                          volume_traded_quote=Decimal('1000'), cum_fees_quote=Decimal('10'),
                          amount=Decimal('2'), breakeven_price=Decimal('100'), realized_pnl_quote=Decimal('5'))
    orchestrator._load_position_from_db('maker', row)
    hold = orchestrator.positions_held['maker'][0]
    assert hold.reduction_basis is None
    assert hold.cum_fees_quote == 10 and hold.avg_entry_price == 100


def test_flat_hedge_legs_have_zero_remaining_cost_despite_lifetime_fees():
    hold = PositionHold('okx_perpetual', 'BTC-USDT', TradeType.BUY)
    hold.add_orders_from_executor(executor('open', TradeType.BUY, 'OPEN', '2', '200', '2'))
    hold.add_orders_from_executor(executor('close', TradeType.SELL, 'CLOSE', '2', '220', '2.2'))
    assert hold.cum_fees_quote == Decimal('4.2')
    assert hold.reduction_basis == HedgeCostBasis(TradeType.BUY, Decimal(0), Decimal(0), Decimal(0))


def test_spot_and_unreviewed_connectors_do_not_acquire_hedge_reduction_authority():
    for connector in ('okx', 'binance_perpetual'):
        hold = PositionHold(connector, 'BTC-USDT', TradeType.BUY)
        hold.add_orders_from_executor(executor('open', TradeType.BUY, 'OPEN', '2', '200', '2'))
        assert hold.reduction_basis is None


def test_legacy_missing_fee_field_does_not_prove_zero_remaining_opening_fees():
    hold = PositionHold('okx_perpetual', 'BTC-USDT', TradeType.BUY)
    info = executor('open', TradeType.BUY, 'OPEN', '2', '200', '2')
    del info.custom_info['held_position_orders'][0]['cumulative_fee_paid_quote']
    hold.add_orders_from_executor(info)
    assert hold.reduction_basis is None
