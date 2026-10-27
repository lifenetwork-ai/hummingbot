from collections import deque
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_mm_drawdown_reports import orchestrator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType


def executor(index):
    config = SimpleNamespace(id=f'executor-{index}', side=TradeType.BUY)
    info = SimpleNamespace(id=config.id, config=config, connector_name='okx', trading_pair='LIFE-USDT',
                           is_done=True, close_type=CloseType.POSITION_HOLD, net_pnl_quote=Decimal(0),
                           filled_amount_quote=Decimal('.1'), custom_info={'held_position_orders': [dict(
                               client_order_id=f'order-{index}', trade_type='BUY', executed_amount_base='1',
                               executed_amount_quote='.1', cumulative_fee_paid_quote='.001')]})
    return SimpleNamespace(config=config, executor_info=info, is_active=False)


def test_retired_executor_ids_do_not_accumulate_or_lose_position_totals():
    item = orchestrator()
    item.executors_ids_position_held = deque(maxlen=50)
    with patch.object(MarketsRecorder, 'get_instance', return_value=MagicMock()):
        for index in range(300):
            row = executor(index)
            item.active_executors['life_spot'].append(row)
            item._update_positions_from_done_executors()
            item.store_executor(StoreExecutorAction(controller_id='life_spot', executor_id=row.config.id))
    position = item.positions_held['life_spot'][0]
    assert position.net_amount_base == 300 and position.volume_traded_quote == 30
    assert position.cum_fees_quote == Decimal('.3')
    assert len(position.order_ids) == 0
    assert len(item.executors_ids_position_held) == 0
    # Retired identities must not be the dirty-checkpoint signal.
    item.strategy.market_data_provider.get_price_by_type.return_value = Decimal('NaN')
    with patch.object(MarketsRecorder, 'get_instance', return_value=MagicMock()):
        with pytest.raises(ValueError, match='drawdown_checkpoint_mark_unavailable'):
            item.store_all_positions()


def test_more_than_fifty_resident_holds_are_processed_once_and_stay_deduplicated():
    item = orchestrator()
    item.executors_ids_position_held = deque(maxlen=50)
    item.active_executors['life_spot'] = [executor(index) for index in range(120)]
    item._update_positions_from_done_executors()
    item._update_positions_from_done_executors()
    assert len(item.executors_ids_position_held) == 120
    assert item.positions_held['life_spot'][0].net_amount_base == 120


def test_failed_executor_commit_retains_position_identities_for_retry():
    item = orchestrator()
    item.executors_ids_position_held = deque(maxlen=50)
    row = executor(1)
    item.active_executors['life_spot'] = [row]
    item._update_positions_from_done_executors()
    recorder = MagicMock()
    recorder.store_or_update_executor.side_effect = RuntimeError('commit unavailable')
    with patch.object(MarketsRecorder, 'get_instance', return_value=recorder):
        item.store_executor(StoreExecutorAction(controller_id='life_spot', executor_id=row.config.id))
    assert item.active_executors['life_spot'] == [row]
    assert item.positions_held['life_spot'][0].order_ids == {'order-1'}
