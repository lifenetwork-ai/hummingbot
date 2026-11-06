"""Fresh exposure reads and per-report hold reuse retain valuation/risk boundaries."""
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_report_snapshots import ChangingExecutor, become_held, orchestrator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


def live_executor():
    seed = ChangingExecutor('live')
    item, price = orchestrator([])
    connector = SimpleNamespace(trading_rules={'LIFE-USDT': TradingRule('LIFE-USDT')},
                                quantize_order_amount=lambda **kw: kw['amount'],
                                get_price_by_type=lambda *args: price.value)
    item.strategy.connectors['okx_perpetual'] = connector
    executor = PositionExecutor(item.strategy, seed.config)
    executor._status = RunnableStatus.RUNNING
    executor._open_order = SimpleNamespace(fee_asset='USDT', executed_amount_base=Decimal('2'),
                                           cum_fees_quote=Decimal('1'), is_done=True,
                                           average_executed_price=Decimal('100'), last_update_timestamp=1000,
                                           order_id=seed.config.id)
    item.active_executors['life_perpetual'] = [executor]
    return item, executor, price


def test_valuation_readiness_does_not_construct_pnl_or_custom_dto():
    item, executor, price = live_executor()
    executor.get_custom_info = MagicMock(side_effect=RuntimeError('unneeded custom DTO'))
    executor.get_net_pnl_quote = MagicMock(side_effect=RuntimeError('unneeded PnL'))
    assert item.mm_valuation_ready()
    executor.get_custom_info.assert_not_called()
    executor.get_net_pnl_quote.assert_not_called()
    price.stamp = 980000
    assert not item.mm_valuation_ready()
    price.stamp = 1000000
    assert item.mm_valuation_ready()


def test_unfilled_to_filled_and_hold_transitions_read_current_exposure():
    item, executor, price = live_executor()
    executor._open_order.executed_amount_base = Decimal('0')
    price.stamp = 0
    assert item.mm_valuation_ready()
    executor._open_order.executed_amount_base = Decimal('2')
    assert not item.mm_valuation_ready()
    executor.close_type = CloseType.POSITION_HOLD
    assert executor.filled_amount_quote == 0
    assert not item.mm_valuation_ready()
    price.stamp = 1000000
    assert item.mm_valuation_ready()


@pytest.mark.parametrize('amount', ['NaN', 'sNaN', 'Infinity', '-Infinity'])
def test_invalid_current_exposure_is_not_treated_as_zero_or_known_exposure(amount, monkeypatch):
    item, executor, _ = live_executor()
    monkeypatch.setattr(PositionExecutor, 'filled_amount_quote', property(lambda self: Decimal(amount)))
    assert not item.mm_valuation_ready()


@pytest.mark.parametrize('amount', ['NaN', 'Infinity'])
def test_invalid_open_hold_exposure_is_not_treated_as_valid(amount, monkeypatch):
    item, executor, _ = live_executor()
    executor.close_type = CloseType.POSITION_HOLD
    monkeypatch.setattr(PositionExecutor, 'open_filled_amount', property(lambda self: Decimal(amount)))
    assert not item.mm_valuation_ready()


def test_critical_exposure_read_failure_propagates(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    item, executor, _ = live_executor()

    def failed(self):
        raise SQLAlchemyError('synthetic')
    monkeypatch.setattr(PositionExecutor, 'filled_amount_quote', property(failed))
    with pytest.raises(SQLAlchemyError):
        item.mm_valuation_ready()


@pytest.mark.parametrize('mm', [False, True])
def test_unified_report_reuses_one_hold_summary_and_mark_per_call(mm):
    seed = ChangingExecutor('held')
    become_held(seed)
    item, price = orchestrator([seed], mm=mm)
    item.get_all_reports()
    position = item.positions_held['life_perpetual'][0]
    position.get_position_summary = MagicMock(wraps=position.get_position_summary)
    provider = item.strategy.market_data_provider
    provider.get_price_by_type = MagicMock(wraps=provider.get_price_by_type)
    result = item.get_all_reports()['life_perpetual']
    position.get_position_summary.assert_called_once()
    provider.get_price_by_type.assert_called_once()
    assert result['performance'].positions_summary == result['positions']
    assert result['performance'].global_pnl_quote == Decimal('19' if mm else '20')
    price.value = Decimal('120')
    next_result = item.get_all_reports()['life_perpetual']
    assert position.get_position_summary.call_count == provider.get_price_by_type.call_count == 2
    assert next_result['performance'].global_pnl_quote == Decimal('39' if mm else '40')
    assert result['performance'].global_pnl_quote == Decimal('19' if mm else '20')
    if mm:
        price.stamp = 980000
        with pytest.raises(ValueError, match='drawdown_valuation_unavailable'):
            item.get_all_reports()
        price.stamp = 1000000
        assert item.get_all_reports()['life_perpetual']['performance'] == next_result['performance']


@pytest.mark.parametrize('mm', [False, True])
def test_position_summary_reuse_keeps_performance_scope_filters(mm):
    seed = ChangingExecutor('held')
    become_held(seed)
    item, _ = orchestrator([seed], mm=mm)
    item.get_all_reports()
    item.strategy.markets = {}
    result = item.get_all_reports()['life_perpetual']
    assert len(result['positions']) == 1
    assert len(result['performance'].positions_summary) == (1 if mm else 0)
    assert result['performance'].global_pnl_quote == (Decimal('19') if mm else 0)


@pytest.mark.parametrize('amount', ['NaN', 'sNaN', 'Infinity', '-Infinity'])
def test_invalid_retained_hold_exposure_is_not_ready(amount):
    from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
    item, _, _ = live_executor()
    position = PositionHold('okx_perpetual', 'LIFE-USDT')
    position.net_amount_base = Decimal(amount)
    item.positions_held['life_perpetual'] = [position]
    assert not item.mm_valuation_ready()


def test_zero_retained_exposure_does_not_require_an_available_mark():
    from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
    item, executor, price = live_executor()
    executor._open_order.executed_amount_base = Decimal('0')
    item.positions_held['life_perpetual'] = [PositionHold('okx_perpetual', 'LIFE-USDT')]
    price.stamp = 0
    assert item.mm_valuation_ready()
