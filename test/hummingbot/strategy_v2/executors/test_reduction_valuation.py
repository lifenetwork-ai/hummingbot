"""A partial close removes old marked exposure before its terminal hold transfer."""
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_order_checkpoint import closing_quote
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_partial_close_valuation_matches_its_later_exactly_once_hold_transfer(monkeypatch, side):
    orchestrator, executor, order, _ = closing_quote(monkeypatch, side)
    orchestrator.strategy.config.mm_drawdown_scope = {
        'maker': SimpleNamespace(connector_name='okx_perpetual', trading_pair='LIFE-USDT')}
    orchestrator._mm_mark_price = lambda *args: Decimal('.12')
    executor.get_price = lambda *args, **kwargs: Decimal('.12')
    fill(order, 'partial', '.5')
    # A CLOSE's .5 units no longer earn/lose marked PnL as if they were still open.
    expected = Decimal('.01') * (1 if side == TradeType.SELL else -1) - Decimal('.001001')
    before = orchestrator.generate_performance_report('maker')
    assert before.global_pnl_quote == expected
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == 1
    executor._held_position_orders = executor._collect_held_position_orders()
    executor.close_type, executor._status = CloseType.POSITION_HOLD, RunnableStatus.TERMINATED
    orchestrator._update_positions_from_done_executors()
    after = orchestrator.generate_performance_report('maker')
    assert after.global_pnl_quote == expected
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == Decimal('.5')


@pytest.mark.parametrize('defect', ['unknown_fee', 'missing_fill'])
def test_unknown_original_closing_fee_cannot_appear_as_profitable_zero_cost(monkeypatch, defect):
    orchestrator, executor, order, _ = closing_quote(monkeypatch)
    orchestrator.strategy.config.mm_drawdown_scope = {
        'maker': SimpleNamespace(connector_name='okx_perpetual', trading_pair='LIFE-USDT')}
    orchestrator._mm_mark_price = lambda *args: Decimal('.12')
    fill(order, 'partial', '.5')
    if defect == 'unknown_fee':
        trade = order.order_fills['partial']
        monkeypatch.setattr(type(trade.fee), 'fee_amount_in_token', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('fee unavailable')))
    else:
        order.order_fills.clear()
    with pytest.raises(ValueError):
        orchestrator.generate_performance_report('maker')
