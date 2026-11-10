"""A flat hedge leg retains its independent accounting identity."""
from decimal import Decimal

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_flat_perpetual_summary_retains_the_original_leg(side):
    hold = PositionHold('okx_perpetual', 'BTC-USDT', side)
    hold._process_order(side == TradeType.BUY, Decimal('1'), Decimal('100'))
    hold._process_order(side != TradeType.BUY, Decimal('1'), Decimal('110'))
    summary = hold.get_position_summary(Decimal('120'))
    assert summary.amount == 0 and summary.breakeven_price == 0
    assert summary.side == side
    assert summary.realized_pnl_quote == (10 if side == TradeType.BUY else -10)


@pytest.mark.parametrize('connector', ['okx', 'okx_perpetual'])
def test_nonzero_summary_still_uses_actual_net_exposure(connector):
    hold = PositionHold(connector, 'BTC-USDT', TradeType.SELL)
    hold.net_amount_base = Decimal('1')
    assert hold.get_position_summary(Decimal('100')).side == TradeType.BUY


def test_flat_spot_summary_keeps_existing_net_position_convention():
    hold = PositionHold('okx', 'BTC-USDT', TradeType.SELL)
    assert hold.get_position_summary(Decimal('100')).side == TradeType.BUY
