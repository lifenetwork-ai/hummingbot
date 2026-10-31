"""Reuse one observation within a report, never across reports or state changes."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import PositionMode, TradeType
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo, PerformanceReport


class ChangingExecutor:
    def __init__(self, identity, *, status=RunnableStatus.RUNNING, close_type=None, pnl='9', volume='100'):
        self.config = PositionExecutorConfig(id=identity, controller_id='life_perpetual', timestamp=1000,
                                             connector_name='okx_perpetual', trading_pair='LIFE-USDT',
                                             side=TradeType.BUY, amount=Decimal('2'), entry_price=Decimal('100'))
        self.state, self.close_type = status, close_type
        self.pnl, self.volume = Decimal(pnl), Decimal(volume)
        self.orders = []
        self.reads = 0
        self.fail = False

    @property
    def executor_info(self):
        self.reads += 1
        if self.fail:
            raise ValueError('Unavailable executor observation')
        return ExecutorInfo(id=self.config.id, timestamp=1000, type='position_executor', config=self.config,
                            status=self.state, close_type=self.close_type, controller_id='life_perpetual',
                            net_pnl_quote=self.pnl, net_pnl_pct=Decimal('.01'), cum_fees_quote=Decimal('1'),
                            filled_amount_quote=self.volume, is_active=self.state == RunnableStatus.RUNNING,
                            is_trading=self.state == RunnableStatus.RUNNING and self.volume > 0,
                            custom_info={'held_position_orders': self.orders})


def orchestrator(executors, *, mm=True):
    item = ExecutorOrchestrator.__new__(ExecutorOrchestrator)
    item.active_executors = {'life_perpetual': executors}
    item.positions_held = {'life_perpetual': []}
    item.cached_performance = {'life_perpetual': PerformanceReport()}
    item.executors_ids_position_held = set()
    scope = {'life_perpetual': SimpleNamespace(connector_name='okx_perpetual', trading_pair='LIFE-USDT')}
    price = SimpleNamespace(value=Decimal('110'), stamp=1000000)
    item.strategy = SimpleNamespace(
        config=SimpleNamespace(mm_drawdown_scope=scope if mm else None, mm_drawdown_max_data_age_seconds=10),
        connectors={'okx_perpetual': SimpleNamespace(position_mode=PositionMode.HEDGE)},
        markets={'okx_perpetual': {'LIFE-USDT'}},
        market_data_provider=SimpleNamespace(time=lambda: 1000, get_price_by_type=lambda *a: price.value,
                                             get_order_book=lambda *a: SimpleNamespace(snapshot_uid=price.stamp, last_diff_uid=0)))
    return item, price


def become_held(executor):
    executor.state, executor.close_type = RunnableStatus.TERMINATED, CloseType.POSITION_HOLD
    executor.orders = [dict(client_order_id=executor.config.id, trade_type='BUY', executed_amount_base='2',
                            executed_amount_quote='200', cumulative_fee_paid_quote='1')]


@pytest.mark.parametrize('mm', [False, True])
def test_one_dto_per_resident_preserves_fee_pnl_and_hold_deduplication(mm):
    active = ChangingExecutor('active')
    closed = ChangingExecutor('closed', status=RunnableStatus.TERMINATED, close_type=CloseType.EARLY_STOP, pnl='3', volume='50')
    held = ChangingExecutor('held')
    become_held(held)
    item, _ = orchestrator([active, closed, held], mm=mm)
    first = item.get_all_reports()['life_perpetual']
    assert [e.reads for e in (active, closed, held)] == [1, 1, 1]
    assert len(first['positions']) == 1
    assert first['performance'].global_pnl_quote == Decimal('31' if mm else '32')
    assert first['performance'].volume_traded == Decimal('350')
    assert first['performance'].close_type_counts == {CloseType.EARLY_STOP: 1, CloseType.POSITION_HOLD: 1}
    second = item.get_all_reports()['life_perpetual']
    assert [e.reads for e in (active, closed, held)] == [2, 2, 2]
    assert first['performance'] == second['performance']
    assert item.positions_held['life_perpetual'][0].cum_fees_quote == Decimal('1')


def test_next_call_sees_active_to_held_transition_and_updated_mark():
    executor = ChangingExecutor('changing')
    item, price = orchestrator([executor])
    first = item.get_all_reports()['life_perpetual']
    assert first['performance'].global_pnl_quote == Decimal('9') and not first['positions']
    become_held(executor)
    price.value = Decimal('120')
    second = item.get_all_reports()['life_perpetual']
    assert executor.reads == 2
    assert second['performance'].global_pnl_quote == Decimal('39')
    assert second['executors'][0].is_done and not first['executors'][0].is_done
    assert second['positions'][0].amount == Decimal('2')
    assert second['performance'].volume_traded == Decimal('200')


def test_failed_report_does_not_pin_prior_dtos_or_mask_a_stale_mark():
    executor = ChangingExecutor('changing')
    item, price = orchestrator([executor])
    item.get_all_reports()
    price.stamp = 980000
    with pytest.raises(ValueError, match='drawdown_valuation_unavailable'):
        item.get_all_reports()
    price.stamp = 1000000
    executor.pnl = Decimal('17')
    assert item.get_all_reports()['life_perpetual']['performance'].global_pnl_quote == Decimal('17')
    executor.fail = True
    with pytest.raises(ValueError, match='Unavailable executor observation'):
        item.get_all_reports()
    executor.fail, executor.pnl = False, Decimal('23')
    assert item.get_all_reports()['life_perpetual']['performance'].global_pnl_quote == Decimal('23')


def test_standalone_reports_keep_reading_fresh_state_without_a_cross_call_cache():
    executor = ChangingExecutor('changing')
    item, _ = orchestrator([executor])
    assert item.generate_performance_report('life_perpetual').global_pnl_quote == Decimal('9')
    executor.pnl = Decimal('13')
    assert item.generate_performance_report('life_perpetual').global_pnl_quote == Decimal('13')
    assert item.get_executors_report()['life_perpetual'][0].net_pnl_quote == Decimal('13')
    assert executor.reads == 3
