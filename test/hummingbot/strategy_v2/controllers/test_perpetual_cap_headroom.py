from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import PositionSide, TradeType


@pytest.mark.parametrize('long,short,side_cap,gross_cap,expected', [
    ('0.0030', '0.0049', '0.005', '0.008', []),
    ('0', '0.005', '0.005', '0.008', [TradeType.BUY, TradeType.BUY]),
    ('0', '0', '0.005', '0.008', [TradeType.BUY, TradeType.BUY, TradeType.SELL, TradeType.SELL]),
    ('0.0030', '0.0049', '0.05', '0.1', [TradeType.BUY, TradeType.BUY, TradeType.SELL, TradeType.SELL]),
])
def test_perpetual_quote_requires_headroom_for_whole_entry_and_flattening_restores_both_sides(long, short, side_cap, gross_cap, expected):
    controller, _ = make_controller(configuration(
        connector_name='okx_perpetual', trading_pair='BTC-USDT', total_amount_quote='100',
        initial_anchor='73000', min_price='1', max_price='1000000',
        buy_spreads=[0.001, 0.002], sell_spreads=[0.001, 0.002],
        max_long_base=side_cap, max_short_base=side_cap, max_gross_base=gross_cap))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.account_positions = {
        'long': SimpleNamespace(trading_pair='BTC-USDT', position_side=PositionSide.LONG, amount=Decimal(long)),
        'short': SimpleNamespace(trading_pair='BTC-USDT', position_side=PositionSide.SHORT, amount=-Decimal(short)),
    }
    controller.processed_data = {'ready': True, 'reference_price': Decimal('73000'), 'spread_multiplier': Decimal('1')}
    actions = controller.create_actions_proposal()
    assert [action.executor_config.side for action in actions] == expected
    assert Decimal(long) + Decimal(short) + sum((a.executor_config.amount for a in actions), Decimal('0')) <= Decimal(gross_cap)
    if not expected:
        assert controller.exposure_within_caps()  # Current exposure is legal; new entries do not fit.
        assert all(controller.get_price_and_amount(level)[1] > Decimal('0.0001')
                   for level in ['buy_0', 'buy_1', 'sell_0', 'sell_1'])
        assert set(controller.quote_diagnostics['reasons']['BUY']) == {'inventory_limit'}
        assert set(controller.quote_diagnostics['reasons']['SELL']) == {'inventory_limit'}
