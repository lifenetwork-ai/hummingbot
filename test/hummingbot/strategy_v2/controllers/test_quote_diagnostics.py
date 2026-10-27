from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
async def test_insufficient_cash_reports_actual_filtered_sides_without_changing_proposals(connector_name):
    controller, _ = make_controller(configuration(connector_name=connector_name))
    controller._account_ready = True
    if connector_name == 'okx_perpetual':
        controller.market_data_provider.get_order_book.side_effect = None
        controller.market_data_provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=1000000, last_diff_uid=1000000)
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_available_balance.side_effect = lambda asset: Decimal('0') if asset == 'USDT' else Decimal('1000')
    await controller.update_processed_data()
    actions = controller.create_actions_proposal()
    diagnostics = controller.quote_diagnostics
    assert diagnostics['observed_at'] == 1000
    assert diagnostics['reasons']['BUY'] == ['insufficient_quote_balance']
    assert diagnostics['proposed']['BUY'] == 0
    if connector_name == 'okx':
        assert [action.executor_config.side for action in actions] == [TradeType.SELL]
        assert diagnostics['proposed']['SELL'] == 1
    else:
        assert actions == []
        assert diagnostics['reasons']['SELL'] == ['insufficient_quote_balance']


@pytest.mark.asyncio
async def test_inventory_and_base_shortage_have_side_specific_reasons_and_clear_after_recovery():
    controller, _ = make_controller(configuration(max_base_inventory='1000'))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_available_balance.side_effect = lambda asset: Decimal('0') if asset == 'LIFE' else Decimal('1000')
    await controller.update_processed_data()
    assert controller.create_actions_proposal() == []
    assert controller.quote_diagnostics['reasons'] == {'BUY': ['inventory_limit'], 'SELL': ['insufficient_base_balance']}
    connector.get_balance.side_effect = lambda asset: Decimal('10')
    connector.get_available_balance.side_effect = lambda asset: Decimal('1000')
    assert len(controller.create_actions_proposal()) == 2
    assert controller.quote_diagnostics['reasons'] == {'BUY': [], 'SELL': []}


@pytest.mark.asyncio
async def test_outflow_block_is_observed_without_a_second_preview_or_execution(monkeypatch):
    controller, _ = make_controller(configuration(runtime_config_managed=True))
    await controller.update_processed_data()
    import hummingbot.core.order_submission_guard as guard
    preview = MagicMock(return_value=False)
    monkeypatch.setattr(guard, 'spot_buy_preview', preview)
    actions = controller.create_actions_proposal()
    assert len(actions) == 1 and preview.call_count == 1
    assert controller.quote_diagnostics['reasons']['BUY'] == ['spot_outflow_blocked']
    assert controller.quote_diagnostics['proposed'] == {'BUY': 0, 'SELL': 1}


@pytest.mark.asyncio
async def test_readiness_and_sizing_rejections_are_diagnostics_not_new_trading_actions(monkeypatch):
    controller, _ = make_controller(configuration())
    await controller.update_processed_data()
    monkeypatch.setattr(controller, 'quote_price_valid', lambda price: False)
    assert controller.create_actions_proposal() == []
    assert controller.quote_diagnostics['reasons'] == {'BUY': ['quote_price_rejected'], 'SELL': ['quote_price_rejected']}
    controller.processed_data.update(ready=False, reason='stale_reference: BTC-USDT')
    assert controller.create_actions_proposal() == []
    assert controller.quote_diagnostics['reasons'] == {'BUY': ['stale_reference'], 'SELL': ['stale_reference']}


@pytest.mark.asyncio
async def test_upstream_active_level_and_cooldown_rules_remain_authoritative():
    controller, _ = make_controller(configuration())
    await controller.update_processed_data()
    buy = controller.get_executor_config('buy_0', Decimal('.099'), Decimal('10'))
    sell = controller.get_executor_config('sell_0', Decimal('.101'), Decimal('10'))
    controller.executors_info = [
        SimpleNamespace(is_active=True, custom_info={'level_id': 'buy_0'}, config=buy),
        SimpleNamespace(is_active=False, status=RunnableStatus.TERMINATED, custom_info={'level_id': 'sell_0'},
                        close_type=CloseType.STOP_LOSS, close_timestamp=995, config=sell)]
    assert controller.create_actions_proposal() == []
    assert controller.quote_diagnostics['reasons'] == {'BUY': ['active_executor'], 'SELL': ['cooldown']}
